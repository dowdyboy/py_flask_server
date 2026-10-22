"""任务队列：可持久化、多进程安全的后台任务（Redis Streams / 进程内内存两种后端）

与 `util/async_task_util.py` 的 BoundedExecutor 的区别：

    线程池（AsyncTaskUtil）           任务队列（本模块）
    ------------------------------   ------------------------------------------
    进程内、重启即丢                  持久化（Redis），worker 重启后继续消费
    多 worker 各自为战                同一 consumer group，任务只会被一个 worker 取走
    无重试 / 无死信 / 无状态查询      指数退避重试、死信队列、按 task_id 查状态
    适合"尽力而为"的轻量动作          适合"必须执行"的业务（发邮件、生成报表、同步数据）

用法：

    # 1) 定义任务（放在 flask_server/task/ 下即自动发现）
    from flask_server.module.task_queue import task

    @task('send_email', max_retries=5)
    def send_email(to, subject):
        ...

    # 2) 入队（在 controller/service 里）
    from flask_server.module.task_queue import enqueue_task
    task_id = enqueue_task('send_email', to='a@b.com', subject='hi')

    # 3) 消费（独立进程）
    python scripts/worker.py --concurrency 4

    # 4) 查状态
    from flask_server.module.task_queue import get_task_status
    get_task_status(task_id)   # {'status': 'succeeded', ...}

后端选择：
    - 配置 REDIS_URL → RedisTaskQueue（多进程/多实例正确，消息持久化）
    - 未配置       → MemoryTaskQueue（单进程开发/测试用，进程退出即丢）
    多 worker 部署必须配置 REDIS_URL，否则每个 worker 只消费自己进程内入队的任务。

可靠性语义：
    - at-least-once：任务至少执行一次。handler 必须**幂等**（或自行去重）。
    - Redis 后端用 consumer group + XACK 保证不丢；worker 崩溃留下的 pending 消息
      超过 TASK_VISIBILITY_TIMEOUT 后被 XAUTOCLAIM 重新投递（会重复执行，属预期）。
    - 入队失败（Redis 不可用）**抛异常而不是静默丢弃**——任务丢失比接口报错更难发现。
"""

import json
import logging
import threading
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..config import config

# 任务状态机：queued → running → succeeded / retrying → dead
STATUS_QUEUED = 'queued'
STATUS_RUNNING = 'running'
STATUS_SUCCEEDED = 'succeeded'
STATUS_RETRYING = 'retrying'
STATUS_DEAD = 'dead'


class TaskQueueUnavailable(RuntimeError):
    """入队失败（后端不可用）。调用方应转成 503 或降级处理，不能假装成功。"""


class TaskNotFound(LookupError):
    """按 task_id 查不到状态（未入队或状态已过期）"""


# --------------------------------------------------------------------------
# 任务注册表
# --------------------------------------------------------------------------

@dataclass
class TaskSpec:
    name: str
    func: Callable
    max_retries: Optional[int] = None      # None 表示用全局配置


class TaskRegistry:
    """任务名 → handler 的注册表（进程内，worker 启动时由自动发现填充）"""

    def __init__(self):
        self._tasks: Dict[str, TaskSpec] = {}
        self._lock = threading.Lock()

    def register(self, name, func, max_retries=None):
        with self._lock:
            existing = self._tasks.get(name)
            if existing is not None and existing.func is not func:
                logging.getLogger('flask_server').warning(
                    f'task `{name}` already registered, overwriting')
            self._tasks[name] = TaskSpec(name=name, func=func, max_retries=max_retries)
        return func

    def get(self, name) -> Optional[TaskSpec]:
        return self._tasks.get(name)

    @property
    def names(self) -> List[str]:
        return sorted(self._tasks)

    def clear(self):
        with self._lock:
            self._tasks.clear()


registry = TaskRegistry()


def task(name=None, *, max_retries=None):
    """任务声明装饰器。

    Args:
        name (str, optional): 任务名（默认用 `模块.函数名`，便于排查）
        max_retries (int, optional): 该任务的最大重试次数（默认取 TASK_MAX_RETRIES）
    """
    def decorator(func):
        registry.register(name or f'{func.__module__}.{func.__name__}', func, max_retries)
        return func
    return decorator


# --------------------------------------------------------------------------
# 任务信封（在队列中传输的结构）
# --------------------------------------------------------------------------

@dataclass
class TaskEnvelope:
    """队列中的任务载体（必须可 JSON 序列化）"""

    id: str
    name: str
    args: List[Any] = field(default_factory=list)
    kwargs: Dict[str, Any] = field(default_factory=dict)
    attempts: int = 0                 # 已失败次数
    enqueued_at: float = field(default_factory=time.time)
    max_retries: Optional[int] = None
    last_error: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw):
        data = json.loads(raw)
        return cls(**data)


@dataclass
class ReservedTask:
    """worker 取到的一条任务（token 用于 ack/重试；Redis 后端是消息 id）"""

    token: str
    envelope: TaskEnvelope
    raw: Any = None


def new_task_id() -> str:
    """任务 id：时间前缀 + 随机后缀（可按时间粗排，便于日志检索）"""
    return f'{int(time.time() * 1000):013d}-{uuid.uuid4().hex[:12]}'


# --------------------------------------------------------------------------
# 后端接口
# --------------------------------------------------------------------------

class BaseTaskQueue:
    """队列后端接口（内存 / Redis 两种实现）"""

    def enqueue(self, name, args=None, kwargs=None, *, task_id=None,
                delay=0.0, attempts=0, max_retries=None) -> str:
        raise NotImplementedError

    def reserve(self, timeout=1.0) -> Optional[ReservedTask]:
        raise NotImplementedError

    def ack(self, reserved: ReservedTask) -> None:
        raise NotImplementedError

    def retry(self, reserved: ReservedTask, delay: float, error: str = '') -> None:
        raise NotImplementedError

    def dead(self, reserved: ReservedTask, error: str = '') -> None:
        raise NotImplementedError

    def get_status(self, task_id) -> Dict[str, Any]:
        raise NotImplementedError

    def stats(self) -> Dict[str, int]:
        raise NotImplementedError

    def close(self) -> None:
        pass

    # -- 内部工具 --
    def _status_record(self, env: TaskEnvelope, status: str) -> Dict[str, Any]:
        return {
            'id': env.id,
            'name': env.name,
            'status': status,
            'attempts': env.attempts,
            'max_retries': env.max_retries if env.max_retries is not None else config.task_max_retries,
            'enqueued_at': env.enqueued_at,
            'last_error': env.last_error,
        }


# --------------------------------------------------------------------------
# 内存后端（单进程；开发/测试/未配置 Redis 时的降级）
# --------------------------------------------------------------------------

class MemoryTaskQueue(BaseTaskQueue):
    """进程内队列：线程安全，支持延迟/重试/死信/状态查询，但不跨进程、不持久化。"""

    def __init__(self, max_status=None, dead_max=None):
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._ready = deque()
        self._delayed: List[tuple] = []      # heapq: (available_at, seq, envelope)
        self._seq = 0
        self._dead = deque(maxlen=dead_max or config.task_dead_letter_max)
        self._status: 'OrderedDict[str, Dict]' = OrderedDict()
        self._status_max = max_status or config.task_status_max
        self._closed = False

    # -- 入队 --
    def enqueue(self, name, args=None, kwargs=None, *, task_id=None,
                delay=0.0, attempts=0, max_retries=None) -> str:
        env = TaskEnvelope(
            id=task_id or new_task_id(), name=name,
            args=list(args or ()), kwargs=dict(kwargs or {}),
            attempts=attempts, max_retries=max_retries,
        )
        with self._cv:
            if delay and delay > 0:
                self._push_delayed(env, delay)
                self._set_status(env, STATUS_RETRYING if attempts else STATUS_QUEUED)
            else:
                self._ready.append(env)
                self._set_status(env, STATUS_RETRYING if attempts else STATUS_QUEUED)
            self._cv.notify()
        return env.id

    def _push_delayed(self, env, delay):
        import heapq
        self._seq += 1
        heapq.heappush(self._delayed, (time.time() + delay, self._seq, env))

    def _promote_due(self):
        """把到期的延迟任务移入就绪队列（调用方持有锁）"""
        import heapq
        now = time.time()
        while self._delayed and self._delayed[0][0] <= now:
            _, _, env = heapq.heappop(self._delayed)
            self._ready.append(env)

    # -- 消费 --
    def reserve(self, timeout=1.0) -> Optional[ReservedTask]:
        deadline = time.time() + max(0.0, timeout)
        with self._cv:
            while True:
                self._promote_due()
                if self._ready:
                    env = self._ready.popleft()
                    self._set_status(env, STATUS_RUNNING)
                    return ReservedTask(token=env.id, envelope=env)
                if self._closed:
                    return None
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                self._cv.wait(remaining)

    def ack(self, reserved: ReservedTask) -> None:
        with self._cv:
            self._set_status(reserved.envelope, STATUS_SUCCEEDED)

    def retry(self, reserved: ReservedTask, delay: float, error: str = '') -> None:
        env = reserved.envelope
        env.attempts += 1
        env.last_error = error or None
        with self._cv:
            self._push_delayed(env, max(0.0, delay))
            self._set_status(env, STATUS_RETRYING)
            self._cv.notify()

    def dead(self, reserved: ReservedTask, error: str = '') -> None:
        env = reserved.envelope
        env.last_error = error or None
        with self._cv:
            self._dead.append(self._status_record(env, STATUS_DEAD))
            self._set_status(env, STATUS_DEAD)

    # -- 查询 --
    def get_status(self, task_id) -> Dict[str, Any]:
        with self._lock:
            record = self._status.get(task_id)
            if record is None:
                raise TaskNotFound(task_id)
            return dict(record)

    def stats(self) -> Dict[str, int]:
        with self._lock:
            self._promote_due()
            return {'ready': len(self._ready), 'delayed': len(self._delayed),
                    'dead': len(self._dead), 'tracked': len(self._status)}

    def dead_letters(self, limit=20):
        with self._lock:
            return list(self._dead)[-limit:]

    def close(self):
        with self._cv:
            self._closed = True
            self._cv.notify_all()

    # -- 内部 --
    def _set_status(self, env: TaskEnvelope, status: str):
        """更新状态（调用方持锁）；状态表按 LRU 上限裁剪，防内存无界增长"""
        self._status[env.id] = self._status_record(env, status)
        self._status.move_to_end(env.id)
        while len(self._status) > self._status_max:
            self._status.popitem(last=False)


# --------------------------------------------------------------------------
# Redis 后端（Streams + consumer group：多 worker 正确、消息持久化）
# --------------------------------------------------------------------------

class RedisTaskQueue(BaseTaskQueue):
    """基于 Redis Streams 的任务队列。

    key 布局（默认前缀 `tasks`，可由 TASK_QUEUE_STREAM 覆盖）：
        {stream}           ← XADD 消息（任务本体）
        {stream}           ← consumer group 消费位点（XREADGROUP/XACK）
        {stream}:delayed   ← ZSET，延迟/重试任务的到期时间
        {stream}:dead      ← LIST，死信（超过重试上限，供人工排查/重放）
        {stream}:task:{id} ← STRING(JSON)，任务状态（带 TTL）
    """

    def __init__(self, url, stream=None, group=None, client=None):
        import redis

        self.stream = stream or config.task_queue_stream
        self.group = group or config.task_queue_group
        self.delayed_key = f'{self.stream}:delayed'
        self.dead_key = f'{self.stream}:dead'
        self.status_prefix = f'{self.stream}:task:'
        self.consumer = f'worker-{uuid.uuid4().hex[:8]}'
        self._last_promote = 0.0
        self.client = client or redis.Redis.from_url(
            url, decode_responses=True, socket_connect_timeout=3, socket_timeout=5,
        )
        try:
            self._ensure_group()
        except Exception as e:
            # 暴露成统一异常：调用方（如 enqueue_task 的控制器）可以据此返回 503，
            # 而不是把 ConnectionError 直接漏成 500
            raise TaskQueueUnavailable(f'任务队列初始化失败（Redis 不可用？）: {e}') from e

    # -- 初始化 --
    def _ensure_group(self):
        try:
            self.client.xgroup_create(self.stream, self.group, id='0', mkstream=True)
        except Exception as e:      # BUSYGROUP：组已存在，正常
            if 'BUSYGROUP' not in str(e):
                raise

    # -- 入队 --
    def enqueue(self, name, args=None, kwargs=None, *, task_id=None,
                delay=0.0, attempts=0, max_retries=None) -> str:
        env = TaskEnvelope(
            id=task_id or new_task_id(), name=name,
            args=list(args or ()), kwargs=dict(kwargs or {}),
            attempts=attempts, max_retries=max_retries,
        )
        try:
            if delay and delay > 0:
                self.client.zadd(self.delayed_key, {env.to_json(): time.time() + delay})
            else:
                self.client.xadd(self.stream, {'data': env.to_json()})
            self._write_status(env, STATUS_RETRYING if attempts else STATUS_QUEUED)
        except Exception as e:
            raise TaskQueueUnavailable(f'任务入队失败（Redis 不可用？）: {e}') from e
        return env.id

    def _write_status(self, env: TaskEnvelope, status: str):
        self.client.set(self.status_prefix + env.id,
                        json.dumps(self._status_record(env, status), ensure_ascii=False),
                        ex=config.task_status_ttl)

    # -- 消费 --
    def _promote_delayed(self):
        """把到期的延迟任务搬回 stream（每 ~1s 做一次，避免每次 reserve 都扫 ZSET）"""
        now = time.time()
        if now - self._last_promote < 1.0:
            return
        self._last_promote = now
        try:
            due = self.client.zrangebyscore(self.delayed_key, '-inf', now, start=0, num=100)
        except Exception:
            return
        for raw in due:
            # ZREM 返回 1 代表本进程抢到这条，避免多 worker 重复投递
            if self.client.zrem(self.delayed_key, raw) == 1:
                try:
                    self.client.xadd(self.stream, {'data': raw})
                except Exception:
                    self.client.zadd(self.delayed_key, {raw: now + 5})   # 失败退回，稍后再试

    def reserve(self, timeout=1.0) -> Optional[ReservedTask]:
        self._promote_delayed()
        block_ms = max(1, int(timeout * 1000))
        try:
            resp = self.client.xreadgroup(
                self.group, self.consumer, {self.stream: '>'}, count=1, block=block_ms,
            )
        except Exception as e:
            # 消费端遇到 Redis 故障：交给上层循环重试（不要吞掉后假装"没有任务"死循环刷日志）
            raise TaskQueueUnavailable(f'任务出队失败（Redis 不可用？）: {e}') from e
        if not resp:
            return None
        for _stream, entries in resp:
            for msg_id, fields in entries:
                raw = fields.get('data')
                try:
                    env = TaskEnvelope.from_json(raw)
                except Exception as e:
                    # 脏数据：直接确认并丢死信，避免阻塞整条流
                    self._dead_letter_raw(msg_id, raw, f'反序列化失败: {e}')
                    continue
                env.attempts = int(env.attempts or 0)
                try:
                    self._write_status(env, STATUS_RUNNING)
                except Exception:
                    pass
                return ReservedTask(token=msg_id, envelope=env, raw=raw)
        return None

    def ack(self, reserved: ReservedTask) -> None:
        env = reserved.envelope
        try:
            self.client.xack(self.stream, self.group, reserved.token)
            self.client.xdel(self.stream, reserved.token)
            self._write_status(env, STATUS_SUCCEEDED)
        except Exception as e:
            # 已执行成功但确认失败：消息会在 visibility timeout 后被重新投递 → 依赖幂等
            logging.getLogger('flask_server').error(
                f'task ack failed id={env.id} name={env.name}: {e}')

    def retry(self, reserved: ReservedTask, delay: float, error: str = '') -> None:
        env = reserved.envelope
        env.attempts += 1
        env.last_error = error or None
        try:
            self.client.xack(self.stream, self.group, reserved.token)
            self.client.xdel(self.stream, reserved.token)
            self.client.zadd(self.delayed_key, {env.to_json(): time.time() + max(0.0, delay)})
            self._write_status(env, STATUS_RETRYING)
        except Exception as e:
            logging.getLogger('flask_server').error(
                f'task retry failed id={env.id} name={env.name}: {e}')

    def dead(self, reserved: ReservedTask, error: str = '') -> None:
        env = reserved.envelope
        env.last_error = error or None
        self._dead_letter_raw(reserved.token, reserved.raw, error, env=env)

    def _dead_letter_raw(self, msg_id, raw, error, env=None):
        try:
            record = dict(self._status_record(env, STATUS_DEAD)) if env else {
                'id': None, 'status': STATUS_DEAD, 'last_error': error, 'raw': raw,
            }
            record['last_error'] = error
            self.client.xack(self.stream, self.group, msg_id)
            self.client.xdel(self.stream, msg_id)
            self.client.lpush(self.dead_key, json.dumps(record, ensure_ascii=False, default=str))
            self.client.ltrim(self.dead_key, 0, config.task_dead_letter_max - 1)
            if env is not None:
                self._write_status(env, STATUS_DEAD)
        except Exception as e:
            logging.getLogger('flask_server').error(f'task dead-letter failed: {e}')

    # -- 崩溃恢复 --
    def reclaim_stale(self, count=10):
        """接管因 worker 崩溃而滞留在 pending 列表的旧消息（≥ 可见性超时）

        Returns:
            int: 本次接管的条数
        """
        min_idle_ms = int(config.task_visibility_timeout * 1000)
        try:
            resp = self.client.xautoclaim(self.stream, self.group, self.consumer,
                                          min_idle_time=min_idle_ms, start_id='0-0', count=count)
        except Exception:
            return 0
        # redis-py 不同版本返回 (next_id, entries) 或 (next_id, entries, deleted)
        entries = resp[1] if isinstance(resp, (list, tuple)) and len(resp) >= 2 else []
        reclaimed = 0
        for item in entries or []:
            if not item or not item[0]:
                continue
            msg_id, fields = item[0], item[1] if len(item) > 1 else {}
            reclaimed += 1
            try:
                env = TaskEnvelope.from_json((fields or {}).get('data'))
            except Exception as e:
                self._dead_letter_raw(msg_id, (fields or {}).get('data'), f'反序列化失败: {e}')
                continue
            delay = min(60.0, max(0.0, config.task_retry_backoff))
            env.attempts = int(env.attempts or 0)
            self.retry(ReservedTask(token=msg_id, envelope=env, raw=(fields or {}).get('data')),
                       delay=delay, error='reclaimed from stale consumer')
        return reclaimed

    # -- 查询 --
    def get_status(self, task_id) -> Dict[str, Any]:
        try:
            raw = self.client.get(self.status_prefix + task_id)
        except Exception as e:
            raise TaskQueueUnavailable(f'读取任务状态失败: {e}') from e
        if raw is None:
            raise TaskNotFound(task_id)
        return json.loads(raw)

    def dead_letters(self, limit=20):
        """读取死信（最近 limit 条，解析失败的条目原样返回）"""
        try:
            raws = self.client.lrange(self.dead_key, 0, max(0, limit - 1))
        except Exception as e:
            raise TaskQueueUnavailable(f'读取死信失败: {e}') from e
        out = []
        for raw in raws:
            try:
                out.append(json.loads(raw))
            except Exception:
                out.append({'raw': raw})
        return out

    def stats(self) -> Dict[str, int]:
        try:
            # ready = 流中未被消费的消息（XLEN 含"已投递待确认"的消息，需扣掉 pending）
            total = int(self.client.xlen(self.stream))
            try:
                pending = self.client.xpending(self.stream, self.group)
                pending_count = int(pending[0]) if pending else 0
            except Exception:
                pending_count = 0
            return {
                'ready': max(0, total - pending_count),
                'pending': pending_count,
                'delayed': int(self.client.zcard(self.delayed_key)),
                'dead': int(self.client.llen(self.dead_key)),
            }
        except Exception as e:
            raise TaskQueueUnavailable(f'读取队列统计失败: {e}') from e

    def close(self):
        try:
            self.client.close()
        except Exception:
            pass


# --------------------------------------------------------------------------
# 单例与便捷函数
# --------------------------------------------------------------------------

_queue: Optional[BaseTaskQueue] = None
_queue_lock = threading.Lock()


def _build_queue() -> BaseTaskQueue:
    if config.redis_url:
        return RedisTaskQueue(config.redis_url)
    return MemoryTaskQueue()


def get_task_queue() -> BaseTaskQueue:
    """获取队列单例（有 REDIS_URL 用 Redis，否则进程内内存）"""
    global _queue
    if _queue is None:
        with _queue_lock:
            if _queue is None:
                _queue = _build_queue()
    return _queue


def reset_task_queue(queue=None):
    """重置队列单例（测试注入 / 配置变更后重建）；不传参则下次访问时重建"""
    global _queue
    with _queue_lock:
        old, _queue = _queue, queue
    if old is not None and queue is not old:
        try:
            old.close()
        except Exception:
            pass


def enqueue_task(name, *args, delay=0.0, task_id=None, max_retries=None, **kwargs) -> str:
    """把任务放入队列，返回 task_id。

    Raises:
        TaskQueueUnavailable: 后端不可用（调用方应返回 503 或走降级逻辑，不要假装成功）
    """
    return get_task_queue().enqueue(
        name, args, kwargs, task_id=task_id, delay=delay, max_retries=max_retries,
    )


def get_task_status(task_id) -> Dict[str, Any]:
    """查询任务状态；不存在（未入队或状态已过期）时抛 TaskNotFound"""
    return get_task_queue().get_status(task_id)


def task_queue_stats() -> Dict[str, int]:
    return get_task_queue().stats()
