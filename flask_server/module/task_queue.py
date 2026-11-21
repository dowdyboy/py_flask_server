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
    - 状态流转（ack/retry/dead）用 MULTI/EXEC 管道一次提交：要么全部生效，要么全部不生效。
      EXEC 失败时消息仍留在 pending，可见性超时后重新投递——这正是 at-least-once 的兜底，
      而不是"已 ACK 但目标结构没写进去"的永久丢失。
    - 延迟任务回迁用 Lua 脚本原子完成（ZREM 成功者才 XADD）：并发 worker 中只有抢到的那一个
      投递（不重复），进程在回迁途中崩溃也不会出现"延迟集合与 stream 两边都没有"的静默丢失。
    - 入队失败（Redis 不可用）**抛异常而不是静默丢弃**——任务丢失比接口报错更难发现。
"""

import json
import threading
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..config import config
from ..util import Logger

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
                # 用统一 Logger（而非 logging.getLogger）才能带上 [rid:...] 请求前缀
                Logger.warn(f'task `{name}` already registered, overwriting')
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

    def retry(self, reserved: ReservedTask, delay: float, error: str = '') -> bool:
        raise NotImplementedError

    def dead(self, reserved: ReservedTask, error: str = '') -> bool:
        raise NotImplementedError

    def get_status(self, task_id) -> Dict[str, Any]:
        raise NotImplementedError

    def stats(self) -> Dict[str, int]:
        """队列统计（两种后端返回同一组键，调用方/看板无需按后端分支）。

        Returns:
            Dict[str, int]: 恒含 `ready` / `pending` / `delayed` / `dead`；
                内存后端额外返回 `tracked`（状态表条数）。
                - `ready`：可直接被 worker 取到的任务数
                - `pending`：已投递但未确认的任务数（内存后端没有可见性超时，恒为 0）
                - `delayed`：延迟/重试等待中的任务数
                - `dead`：死信条数
        """
        raise NotImplementedError

    def dead_letters(self, limit=20) -> List[Dict[str, Any]]:
        """读取死信（最多 limit 条，**最新在前**，两种后端的顺序一致）。

        每条记录带 `id/name/args/kwargs`（无法反序列化的脏数据条目没有信封，字段为空），
        脏数据条目保留 `raw` 原始 payload 供人工排查。
        """
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

    def _dead_letter_record(self, env: TaskEnvelope, error: str = '') -> Dict[str, Any]:
        """死信记录：在状态记录之外保留 args/kwargs，便于按原参数重放。

        普通状态记录不携带参数（状态查询高频、且参数可能很大/含敏感信息）；
        只有进入死信、需要人工排查/重放时才保存。
        """
        record = self._status_record(env, STATUS_DEAD)
        record['args'] = list(env.args)
        record['kwargs'] = dict(env.kwargs)
        record['last_error'] = error or env.last_error
        return record


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
        # 与 Redis 后端保持同一契约：参数必须可 JSON 序列化（否则 dev/test 能跑、
        # 生产切到 Redis 才 503）。内存后端也显式校验一次。
        try:
            env.to_json()
        except (TypeError, ValueError) as e:
            raise ValueError(f'task args/kwargs are not JSON serializable: {e}') from e
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

    def retry(self, reserved: ReservedTask, delay: float, error: str = '') -> bool:
        env = reserved.envelope
        env.attempts += 1
        env.last_error = error or None
        with self._cv:
            self._push_delayed(env, max(0.0, delay))
            self._set_status(env, STATUS_RETRYING)
            self._cv.notify()
        return True

    def dead(self, reserved: ReservedTask, error: str = '') -> bool:
        env = reserved.envelope
        env.last_error = error or None
        with self._cv:
            self._dead.append(self._dead_letter_record(env, error))
            self._set_status(env, STATUS_DEAD)
        return True

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
            # pending 恒为 0：内存后端没有可见性超时，取出的任务直接执行，没有"已投递待确认"状态。
            # tracked 是内存后端特有的状态表条数（Redis 的状态在各自 key 上，按 TTL 过期）。
            return {'ready': len(self._ready), 'pending': 0, 'delayed': len(self._delayed),
                    'dead': len(self._dead), 'tracked': len(self._status)}

    def dead_letters(self, limit=20):
        """读取死信（最近 limit 条，最新在前，与 Redis 后端顺序一致）"""
        if limit <= 0:
            return []
        with self._lock:
            return list(reversed(list(self._dead)[-limit:]))

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

# 延迟任务原子回迁脚本（由 register_script 缓存为 EVALSHA 执行）：
#   KEYS[1] = 延迟 ZSET，KEYS[2] = 任务 stream，ARGV[1] = 任务信封 JSON
# ZREM 返回 1（本进程抢到这条）才执行 XADD：摘除与投递在同一次脚本执行内完成，
# 并发 worker 中只有抢到的那一个投递（不会重复），进程在两者之间崩溃也不会丢任务
# （要么仍在 ZSET，要么已在 stream，不存在"两边都没有"的中间态）。
_PROMOTE_DELAYED_SCRIPT = """
if redis.call('ZREM', KEYS[1], ARGV[1]) == 1 then
    redis.call('XADD', KEYS[2], '*', 'data', ARGV[1])
    return 1
end
return 0
"""

# 重试：把消息从 pending 摘除 + 投递到延迟集合，必须在一次原子执行内完成。
# MULTI/EXEC 不够：Redis 事务遇到运行期错误（键类型冲突/WRONGTYPE/OOM）不会回滚
# 已执行的命令，ZADD 失败时 XACK/XDEL 仍会生效 → 任务永久丢失。
#   KEYS[1]=stream  KEYS[2]=delayed  ARGV=[group, msg_id, payload, score]
_RETRY_SCRIPT = """
-- task retry (atomic): ZADD + XACK + XDEL
redis.call('ZADD', KEYS[2], ARGV[4], ARGV[3])
redis.call('XACK', KEYS[1], ARGV[1], ARGV[2])
redis.call('XDEL', KEYS[1], ARGV[2])
return 1
"""

# 死信：LPUSH + LTRIM + XACK + XDEL 同样必须原子（否则 LPUSH 失败时任务连线索都不剩）
#   KEYS[1]=stream  KEYS[2]=dead  ARGV=[group, msg_id, payload, max_len]
_DEAD_SCRIPT = """
-- task dead (atomic): LPUSH + LTRIM + XACK + XDEL
redis.call('LPUSH', KEYS[2], ARGV[3])
redis.call('LTRIM', KEYS[2], 0, tonumber(ARGV[4]) - 1)
redis.call('XACK', KEYS[1], ARGV[1], ARGV[2])
redis.call('XDEL', KEYS[1], ARGV[2])
return 1
"""


def _load_script(client, script):
    """加载 Lua 脚本：优先 register_script（EVALSHA 缓存）；极简客户端退回 EVAL"""
    register = getattr(client, 'register_script', None)
    if register is not None:
        return register(script)
    return lambda keys=None, args=None: client.eval(
        script, len(keys or []), *(keys or []), *(args or []),
    )


class RedisTaskQueue(BaseTaskQueue):
    """基于 Redis Streams 的任务队列。

    key 布局（默认前缀 `tasks`，可由 TASK_QUEUE_STREAM 覆盖）：
        {stream}           ← XADD 消息（任务本体）
        {stream}           ← consumer group 消费位点（XREADGROUP/XACK）
        {stream}:delayed   ← ZSET，延迟/重试任务的到期时间
        {stream}:dead      ← LIST，死信（超过重试上限，供人工排查/重放）
        {stream}:task:{id} ← STRING(JSON)，任务状态（带 TTL）

    延迟回迁原子性：到期任务从 `{stream}:delayed` 搬回 stream 由 Lua 脚本一次完成
    （ZREM 成功者才 XADD）。因此多 worker 并发回迁不会重复投递，进程中途崩溃也不会
    把任务丢在"已摘除但未入流"的间隙里。
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
        # 阻塞式 XREADGROUP 的 socket 读超时必须大于 block 时长，否则空闲队列会先被
        # socket_timeout 打断（redis-py 抛 TimeoutError）→ reserve 恒失败。默认配置是
        # 2s，这里至少给 5s，并随 TASK_RESERVE_TIMEOUT 放大。
        self._socket_timeout = max(5, int(config.task_reserve_timeout) + 2)
        self.client = client or redis.Redis.from_url(
            url, decode_responses=True, socket_connect_timeout=3,
            socket_timeout=self._socket_timeout,
        )
        try:
            self._promote_script = _load_script(self.client, _PROMOTE_DELAYED_SCRIPT)
            self._retry_script = _load_script(self.client, _RETRY_SCRIPT)
            self._dead_script = _load_script(self.client, _DEAD_SCRIPT)
            self._ensure_group()
        except Exception as e:
            # 暴露成统一异常：调用方（如 enqueue_task 的控制器）可以据此返回 503，
            # 而不是把 ConnectionError 直接漏成 500
            raise TaskQueueUnavailable(f'任务队列初始化失败（Redis 不可用？）: {e}') from e

    def _load_promote_script(self):
        """加载延迟回迁脚本；优先 register_script（EVALSHA 缓存，避免每次传脚本体）。

        保留该方法名供旧测试/monkeypatch 使用；实现统一走 _load_script。
        极简客户端（测试替身）没有 register_script 时退回 EVAL，语义完全相同。
        """
        return _load_script(self.client, _PROMOTE_DELAYED_SCRIPT)

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
        # 先序列化：不可序列化是调用方参数错误（ValueError），不能包装成"Redis 不可用"
        try:
            payload = env.to_json()
        except (TypeError, ValueError) as e:
            raise ValueError(f'task args/kwargs are not JSON serializable: {e}') from e
        try:
            if delay and delay > 0:
                self.client.zadd(self.delayed_key, {payload: time.time() + delay})
            else:
                self.client.xadd(self.stream, {'data': payload})
        except Exception as e:
            raise TaskQueueUnavailable(f'任务入队失败（Redis 不可用？）: {e}') from e
        # 状态写入是旁路信息：入队已成功后它再失败，不能反过来告诉调用方"入队失败"
        # （否则调用方重试会重复执行任务）。与 ack/retry/dead 的做法保持一致。
        self._write_status_best_effort(env, STATUS_RETRYING if attempts else STATUS_QUEUED)
        return env.id

    def _write_status(self, env: TaskEnvelope, status: str):
        self.client.set(self.status_prefix + env.id,
                        json.dumps(self._status_record(env, status), ensure_ascii=False),
                        ex=config.task_status_ttl)

    def _write_status_best_effort(self, env: TaskEnvelope, status: str):
        """写状态记录是旁路信息：失败只影响状态查询的展示，绝不能让任务流转报错/回滚"""
        try:
            self._write_status(env, status)
        except Exception as e:
            Logger.warn(f'task status write failed id={env.id} status={status}: {e}')

    # -- 消费 --
    def _promote_delayed(self):
        """把到期的延迟任务搬回 stream（每 ~1s 做一次，避免每次 reserve 都扫 ZSET）

        摘除与入流由 Lua 脚本原子完成（ZREM 成功者才 XADD）：多 worker 并发回迁时
        只有抢到的那一个投递，进程在回迁途中断掉也不会丢任务。

        Raises:
            TaskQueueUnavailable: Redis 不可用（与 reserve 的其它失败语义一致）
        """
        now = time.time()
        if now - self._last_promote < 1.0:
            return
        # 整个回迁过程（ZRANGEBYSCORE + 脚本）都必须在 try 内：修复前 ZREM 在 try 外，
        # 连接抖动会以裸 ConnectionError 逃出 reserve()，让只捕获 TaskQueueUnavailable 的
        # 调用方（如 scripts/worker.py --once）直接崩出 traceback。
        try:
            due = self.client.zrangebyscore(self.delayed_key, '-inf', now, start=0, num=100)
            for raw in due:
                # 脚本内 ZREM+XADD 是一次原子执行：返回 1 表示本进程抢到并已投递，
                # 返回 0 表示别的 worker 已经抢走（本进程不得重复入队）。
                self._promote_script(keys=[self.delayed_key, self.stream], args=[raw])
        except Exception as e:
            raise TaskQueueUnavailable(f'延迟任务回迁失败（Redis 不可用？）: {e}') from e
        # 只有整轮成功才推进节流时间戳：失败后保留旧值，下一次 reserve 立即重试
        self._last_promote = now

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
            # XACK + XDEL 必须原子提交：先 ACK 后 DEL 失败会留下永远不再投递的僵尸条目，
            # 并让 stats()['ready'] 永久虚高。EXEC 失败时消息仍是 pending，可见性超时后重投。
            pipe = self.client.pipeline(transaction=True)
            pipe.xack(self.stream, self.group, reserved.token)
            pipe.xdel(self.stream, reserved.token)
            pipe.execute()
        except Exception as e:
            # 已执行成功但确认失败：消息会在 visibility timeout 后被重新投递 → 依赖幂等
            Logger.error(f'task ack failed id={env.id} name={env.name}: {e}')
            return
        self._write_status_best_effort(env, STATUS_SUCCEEDED)

    def retry(self, reserved: ReservedTask, delay: float, error: str = '') -> bool:
        env = reserved.envelope
        env.attempts += 1
        env.last_error = error or None
        try:
            payload = env.to_json()
        except (TypeError, ValueError) as e:      # 信封本应可序列化；防御未来新增字段
            Logger.error(f'task retry skipped (envelope not serializable) id={env.id}: {e}')
            return False
        try:
            # ZADD + XACK + XDEL 由 Lua 一次原子完成。MULTI/EXEC 在运行期错误
            # （WRONGTYPE/OOM）时不会回滚已执行命令，可能把任务从 stream/PEL 摘除后
            # 又没写进 delayed —— 这正是"声明了原子性却仍丢任务"的残留窗口。
            self._retry_script(
                keys=[self.stream, self.delayed_key],
                args=[self.group, reserved.token, payload, time.time() + max(0.0, delay)],
            )
        except Exception as e:
            # 脚本没执行成功：消息仍在 pending，可见性超时后会重新投递（at-least-once）
            Logger.error(f'task retry failed id={env.id} name={env.name}: {e}')
            return False
        self._write_status_best_effort(env, STATUS_RETRYING)
        return True

    def dead(self, reserved: ReservedTask, error: str = '') -> bool:
        env = reserved.envelope
        env.last_error = error or None
        return self._dead_letter_raw(reserved.token, reserved.raw, error, env=env)

    def _dead_letter_raw(self, msg_id, raw, error, env=None) -> bool:
        try:
            if env is not None:
                record = self._dead_letter_record(env, error)
            else:
                # 脏数据（无法反序列化）：没有可用信封，字段置空；raw 必须保留供人工排查
                record = {
                    'id': None, 'name': None, 'args': [], 'kwargs': {},
                    'status': STATUS_DEAD, 'last_error': error, 'raw': raw,
                }
            payload = json.dumps(record, ensure_ascii=False, default=str)
        except Exception as e:
            Logger.error(f'task dead-letter skipped (record not serializable): {e}')
            return False
        try:
            # LPUSH + LTRIM + XACK + XDEL 原子完成：任何一步失败都不能让消息
            # "已 ACK 但死信没写进去"（会连人工排查的线索都丢掉）。
            self._dead_script(
                keys=[self.stream, self.dead_key],
                args=[self.group, msg_id, payload, config.task_dead_letter_max],
            )
        except Exception as e:
            Logger.error(f'task dead-letter failed: {e}')
            return False
        if env is not None:
            self._write_status_best_effort(env, STATUS_DEAD)
        return True

    # -- 崩溃恢复 --
    def reclaim_stale(self, count=10):
        """接管因 worker 崩溃而滞留在 pending 列表的旧消息（≥ 可见性超时）

        Returns:
            int: 本次成功接管的条数
        """
        min_idle_ms = int(config.task_visibility_timeout * 1000)
        try:
            resp = self.client.xautoclaim(self.stream, self.group, self.consumer,
                                          min_idle_time=min_idle_ms, start_id='0-0', count=count)
        except Exception as e:
            # 不能静默：Redis 故障时崩溃恢复完全停摆，运维必须看得到
            Logger.warn(f'task reclaim (XAUTOCLAIM) failed: {e}')
            return 0
        # redis-py 不同版本返回 (next_id, entries) 或 (next_id, entries, deleted)
        entries = resp[1] if isinstance(resp, (list, tuple)) and len(resp) >= 2 else []
        reclaimed = 0
        for item in entries or []:
            if not item or not item[0]:
                continue
            msg_id, fields = item[0], item[1] if len(item) > 1 else {}
            raw = (fields or {}).get('data')
            try:
                env = TaskEnvelope.from_json(raw)
            except Exception as e:
                self._dead_letter_raw(msg_id, raw, f'反序列化失败: {e}')
                continue
            env.attempts = int(env.attempts or 0)
            reserved = ReservedTask(token=msg_id, envelope=env, raw=raw)
            max_retries = env.max_retries if env.max_retries is not None else config.task_max_retries
            if env.attempts >= max_retries:
                # 回收也是"一次失败投递"：重试预算已耗尽就直接进死信，不能无限重投
                if self.dead(reserved, 'reclaimed from stale consumer (retry budget exhausted)'):
                    reclaimed += 1
                continue
            # 退避随 attempts 指数增长（与 TaskWorker._handle 一致），上限 60s
            delay = min(60.0, max(0.0, config.task_retry_backoff) * (2 ** env.attempts))
            if self.retry(reserved, delay=delay, error='reclaimed from stale consumer'):
                reclaimed += 1
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
        """读取死信（最近 limit 条，最新在前，与内存后端顺序一致）

        正常记录由 _dead_letter_record 生成，带 id/name/args/kwargs/status/last_error；
        无法反序列化的脏数据条目没有信封，字段置空并保留 raw 原始 payload。
        """
        if limit <= 0:
            return []
        try:
            raws = self.client.lrange(self.dead_key, 0, limit - 1)
        except Exception as e:
            raise TaskQueueUnavailable(f'读取死信失败: {e}') from e
        out = []
        for raw in raws:
            try:
                record = json.loads(raw)
            except Exception:
                record = None
            if isinstance(record, dict):
                out.append(record)
            else:
                out.append({'id': None, 'name': None, 'args': [], 'kwargs': {}, 'raw': raw})
        return out

    def stats(self) -> Dict[str, int]:
        try:
            # ready = 流中未被消费的消息（XLEN 含"已投递待确认"的消息，需扣掉 pending）
            total = int(self.client.xlen(self.stream))
            try:
                pending = self.client.xpending(self.stream, self.group)
                # redis-py 5.x 的 XPENDING 摘要返回 dict（{'pending': n, ...}），
                # 旧版本/部分假客户端返回 [count, min, max, consumers]；两种都要兼容。
                # 修复前只按 list 取 pending[0]，真实 Redis 下 KeyError 被吞后 pending 恒为 0，
                # 导致 ready 把 pending 也算进去（积压统计虚高）。
                if isinstance(pending, dict):
                    pending_count = int(pending.get('pending', 0) or 0)
                elif pending:
                    pending_count = int(pending[0])
                else:
                    pending_count = 0
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
