"""任务消费者：从队列取任务 → 执行 → 确认/重试/死信

设计要点：
  - 每个消费线程独立 `reserve`（Redis consumer group 天然分发；内存后端用条件变量）
  - handler 默认在 Flask app 上下文内执行（任务里可直接用 db.session / 现有 service）
  - at-least-once：handler 必须幂等；确认失败或 worker 崩溃都会导致重投
  - 重试采用指数退避：TASK_RETRY_BACKOFF * 2 ** 已失败次数
  - 收到停止信号后不再取新任务，等在途任务执行完再退出

单独进程运行（推荐）：

    python scripts/worker.py --concurrency 4
"""

import threading
import time

from ..config import config
from ..util import Logger
from .task_queue import TaskQueueUnavailable, get_task_queue, registry

# Redis 后端接管"陈旧 pending 消息"的检查间隔（秒）
RECLAIM_INTERVAL = 30
# 后端不可用时的退避上限（秒）
UNAVAILABLE_BACKOFF_MAX = 30


class TaskWorker:
    """任务消费者（N 个线程，各自 reserve → execute → ack）"""

    def __init__(self, app=None, queue=None, concurrency=None):
        self.app = app
        self.queue = queue if queue is not None else get_task_queue()
        self.concurrency = concurrency if concurrency is not None else config.task_worker_concurrency
        self._stop = threading.Event()
        self._threads = []
        self._lock = threading.Lock()
        self.processed = 0
        self.failed = 0
        self._last_reclaim = 0.0

    # ---------------- 生命周期 ----------------

    def start(self):
        """启动消费线程（幂等）"""
        if self._threads:
            return self
        for i in range(max(1, self.concurrency)):
            t = threading.Thread(target=self._loop, name=f'task-worker-{i}', daemon=True)
            t.start()
            self._threads.append(t)
        Logger.info(f'TaskWorker started: concurrency={self.concurrency}, '
                    f'queue={type(self.queue).__name__}, tasks={len(registry.names)}')
        return self

    def stop(self, timeout=15):
        """停止消费：不再取新任务，等待在途任务完成（超时后线程为 daemon，随进程退出）"""
        self._stop.set()
        deadline = time.time() + timeout
        for t in self._threads:
            t.join(timeout=max(0.0, deadline - time.time()))
        self._threads.clear()
        Logger.info(f'TaskWorker stopped: processed={self.processed}, failed={self.failed}')

    @property
    def running(self):
        return bool(self._threads) and not self._stop.is_set()

    def run_forever(self):
        """阻塞运行直到 stop() 被调用（或收到 KeyboardInterrupt）"""
        self.start()
        try:
            while not self._stop.is_set():
                self._stop.wait(0.5)
        except KeyboardInterrupt:      # pragma: no cover - 交互式场景
            Logger.warn('TaskWorker interrupted by user')
        finally:
            self.stop()

    # ---------------- 消费循环 ----------------

    def _loop(self):
        backoff = 1.0
        while not self._stop.is_set():
            try:
                reserved = self.queue.reserve(timeout=config.task_reserve_timeout)
            except TaskQueueUnavailable as e:
                # 后端不可用：退避重试，不要空转刷日志
                Logger.warn(f'TaskWorker reserve failed, retry in {backoff:.0f}s: {e}')
                self._stop.wait(backoff)
                backoff = min(UNAVAILABLE_BACKOFF_MAX, backoff * 2)
                continue
            except Exception as e:      # 防御：不让单次异常打死消费线程
                Logger.error(f'TaskWorker reserve error: {e}', exc_info=True)
                self._stop.wait(1)
                continue

            backoff = 1.0
            if reserved is None:
                self._maybe_reclaim()
                continue
            self._handle(reserved)

    def _maybe_reclaim(self):
        """Redis 后端：定期接管 worker 崩溃遗留的 pending 消息"""
        reclaim = getattr(self.queue, 'reclaim_stale', None)
        if reclaim is None:
            return
        now = time.time()
        if now - self._last_reclaim < RECLAIM_INTERVAL:
            return
        self._last_reclaim = now
        try:
            count = reclaim()
            if count:
                Logger.warn(f'TaskWorker reclaimed {count} stale task(s)')
        except Exception as e:
            Logger.warn(f'TaskWorker reclaim failed: {e}')

    def _handle(self, reserved):
        env = reserved.envelope
        spec = registry.get(env.name)
        if spec is None:
            msg = f'task not registered: {env.name}'
            Logger.error(f'TaskWorker {msg} (id={env.id})')
            self.queue.dead(reserved, msg)
            self._count(failed=True)
            return

        max_retries = env.max_retries
        if max_retries is None:
            max_retries = spec.max_retries if spec.max_retries is not None else config.task_max_retries

        started = time.perf_counter()
        try:
            self._execute(spec, env)
        except Exception as e:
            error = f'{type(e).__name__}: {e}'
            attempt_no = env.attempts + 1
            if env.attempts < max_retries:
                delay = max(0, config.task_retry_backoff) * (2 ** env.attempts)
                self.queue.retry(reserved, delay, error)
                Logger.warn(f'TaskWorker task failed, retry in {delay}s '
                            f'(attempt {attempt_no}/{max_retries + 1}) id={env.id} name={env.name}: {error}')
            else:
                self.queue.dead(reserved, error)
                Logger.error(f'TaskWorker task dead after {attempt_no} attempt(s) '
                             f'id={env.id} name={env.name}: {error}', exc_info=True)
                self._count(failed=True)
            return

        self.queue.ack(reserved)
        elapsed = (time.perf_counter() - started) * 1000
        Logger.info(f'TaskWorker done id={env.id} name={env.name} elapsed={elapsed:.1f}ms')
        self._count(failed=False)

    def _execute(self, spec, env):
        """在 app 上下文中执行 handler（任务常用 db.session / 现有 service）"""
        if self.app is None:
            spec.func(*env.args, **env.kwargs)
            return
        with self.app.app_context():
            spec.func(*env.args, **env.kwargs)

    def _count(self, failed):
        with self._lock:
            self.processed += 1
            if failed:
                self.failed += 1


def start_worker(app=None, queue=None, concurrency=None) -> TaskWorker:
    """创建并启动一个 worker（便于在脚本/测试里使用）"""
    return TaskWorker(app=app, queue=queue, concurrency=concurrency).start()


__all__ = ['TaskWorker', 'start_worker']
