"""任务消费者：从队列取任务 → 执行 → 确认/重试/死信

设计要点：
  - 每个消费线程独立 `reserve`（Redis consumer group 天然分发；内存后端用条件变量）
  - handler 默认在 Flask app 上下文内执行（任务里可直接用 db.session / 现有 service）
  - at-least-once：handler 必须幂等；确认失败或 worker 崩溃都会导致重投
  - 重试采用指数退避：TASK_RETRY_BACKOFF * 2 ** 已失败次数
  - 收到停止信号后不再取新任务，等在途任务执行完再退出
  - 可观测性：未执行即被拒绝/死信的任务计入 Prometheus `task_rejected_total{reason}`
    （reason 见 TASK_REJECTED_REASONS，固定低基数，详见 docs/api-conventions.md）

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

# task_rejected_total{reason} 的固定标签值（只增不改，保持指标低基数）：
#   - unavailable  ：队列后端不可用，本轮 reserve 失败（任务未取到，退避后重试）
#   - unregistered ：任务名未注册，无法执行 → 死信
#   - max_retries  ：handler 重试次数耗尽 → 死信
TASK_REJECTED_REASONS = ('unavailable', 'unregistered', 'max_retries')


def _count_rejected(reason):
    """任务被拒绝/未执行即进入死信时计数（metrics 依赖缺失时自动跳过）"""
    from ..component.metrics import inc_task_rejected
    inc_task_rejected(reason)


def _count_processed(result):
    """任务执行结果计数：succeeded / retried / dead（固定低基数标签）"""
    from ..component.metrics import inc_task_processed
    inc_task_processed(result)


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
        """启动消费线程（幂等：重复调用不应重复创建线程）"""
        alive = [t for t in self._threads if t.is_alive()]
        if alive:
            Logger.warn(f'TaskWorker already running ({len(alive)} thread(s)), start() ignored')
            return self
        # 支持 stop() 之后再次 start()：_stop 是 Event，stop() 会置位且不会自动复位，
        # 不复位的话重启出来的线程会立即退出（"静默重启失效"）。
        self._threads = []
        self._stop.clear()
        for i in range(max(1, self.concurrency)):
            t = threading.Thread(target=self._loop, name=f'task-worker-{i}', daemon=True)
            t.start()
            self._threads.append(t)
        Logger.info(f'TaskWorker started: concurrency={self.concurrency}, '
                    f'queue={type(self.queue).__name__}, tasks={len(registry.names)}')
        return self

    def stop(self, timeout=15):
        """停止消费：不再取新任务，等待在途任务完成（超时后线程为 daemon，随进程退出）

        超时后**保留**仍存活线程的引用（不能 clear）：否则后续 stop() 再也等不到它们，
        而 start() 的 `_stop.clear()` 会让这些旧线程重新进入消费循环，造成 N+1 个
        消费者且多出来的那个永远不受管理。
        """
        self._stop.set()
        deadline = time.time() + timeout
        for t in list(self._threads):
            t.join(timeout=max(0.0, deadline - time.time()))
        alive = [t for t in self._threads if t.is_alive()]
        self._threads = alive
        if alive:
            Logger.warn(f'TaskWorker stop timed out: {len(alive)} consumer thread(s) still '
                        f'running after {timeout}s (they will exit with the process)')
        Logger.info(f'TaskWorker stopped: processed={self.processed}, failed={self.failed}')

    @property
    def running(self):
        """是否真的有存活的消费线程（不能只看 _threads 非空：线程可能已崩溃/已结束）"""
        return bool(self._threads) and not self._stop.is_set() \
            and any(t.is_alive() for t in self._threads)

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

    def process_one(self, timeout=None):
        """同步处理一条任务（不启动消费线程），返回是否处理了任务。

        供 `scripts/worker.py --once`（联调/定时触发）与测试使用。
        与 `_loop` 的区别：不做后端故障退避，异常直接抛给调用方。
        """
        # 也要做一次崩溃恢复检查：--once 模式（尤其被 cron 高频拉起时）是唯一入口，
        # 若不做，崩溃 worker 遗留的 pending 消息永远不会被接管（_loop 才调 _maybe_reclaim）。
        self._maybe_reclaim()
        reserved = self.queue.reserve(
            config.task_reserve_timeout if timeout is None else timeout)
        if reserved is None:
            return False
        self._handle(reserved)
        return True

    def _loop(self):
        backoff = 1.0
        while not self._stop.is_set():
            # 崩溃恢复检查放在循环顶部（内部按 RECLAIM_INTERVAL 节流）：
            # 只在 reserve() 返回 None 时检查会让"持续有任务"的队列永远不接管
            # 崩溃 worker 留下的 stale pending（高负载下任务可能长期滞留）。
            self._maybe_reclaim()
            try:
                reserved = self.queue.reserve(timeout=config.task_reserve_timeout)
            except TaskQueueUnavailable as e:
                # 后端不可用：退避重试，不要空转刷日志
                Logger.warn(f'TaskWorker reserve failed, retry in {backoff:.0f}s: {e}')
                _count_rejected('unavailable')
                self._stop.wait(backoff)
                backoff = min(UNAVAILABLE_BACKOFF_MAX, backoff * 2)
                continue
            except Exception as e:      # 防御：不让单次异常打死消费线程
                Logger.error(f'TaskWorker reserve error: {e}', exc_info=True)
                self._stop.wait(1)
                continue

            backoff = 1.0
            if reserved is None:
                continue
            try:
                self._handle(reserved)
            except BaseException as e:
                # _handle 内部已捕获 handler 的普通异常；这里兜的是 BaseException
                # （例如 handler 调了 sys.exit()）——否则会直接打死消费线程且不留任何日志，
                # 线上表现为"并发消费能力悄悄下降"。
                Logger.error(f'TaskWorker _handle crashed: {e}', exc_info=True)

    def _maybe_reclaim(self):
        """Redis 后端：定期接管 worker 崩溃遗留的 pending 消息"""
        reclaim = getattr(self.queue, 'reclaim_stale', None)
        if reclaim is None:
            return
        now = time.time()
        if now - self._last_reclaim < RECLAIM_INTERVAL:
            return
        try:
            count = reclaim()
        except Exception as e:
            # 失败不推进节流时间戳：下一轮 reserve 立即重试（否则要再等 30s）
            Logger.warn(f'TaskWorker reclaim failed: {e}')
            return
        self._last_reclaim = now
        if count:
            Logger.warn(f'TaskWorker reclaimed {count} stale task(s)')

    def _handle(self, reserved):
        env = reserved.envelope
        spec = registry.get(env.name)
        if spec is None:
            msg = f'task not registered: {env.name}'
            Logger.error(f'TaskWorker {msg} (id={env.id})')
            _count_rejected('unregistered')
            self.queue.dead(reserved, msg)
            _count_processed('dead')
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
                _count_processed('retried')
                Logger.warn(f'TaskWorker task failed, retry in {delay}s '
                            f'(attempt {attempt_no}/{max_retries + 1}) id={env.id} name={env.name}: {error}')
            else:
                self.queue.dead(reserved, error)
                _count_rejected('max_retries')
                _count_processed('dead')
                Logger.error(f'TaskWorker task dead after {attempt_no} attempt(s) '
                             f'id={env.id} name={env.name}: {error}', exc_info=True)
                self._count(failed=True)
            return

        self.queue.ack(reserved)
        _count_processed('succeeded')
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
