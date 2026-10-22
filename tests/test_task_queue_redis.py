"""Redis 后端的命令级测试（用内存假客户端模拟 Streams/ZSET/LIST 语义）

真正的 Redis 语义由集成环境验证；这里保证：
  - 用对了命令与 key（XADD/XREADGROUP/XACK/XDEL/XAUTOCLAIM/ZADD/LPUSH...）
  - 状态流转、脏数据兜底、陈旧消息接管、后端不可用时的异常语义
"""

from collections import deque

import pytest

from flask_server.module.task_queue import (
    STATUS_DEAD, STATUS_QUEUED, STATUS_RETRYING, STATUS_RUNNING, STATUS_SUCCEEDED,
    RedisTaskQueue, TaskNotFound, TaskQueueUnavailable, TaskEnvelope, registry,
    reset_task_queue,
)


class FakeRedis:
    """最小可用的 Redis 假实现（只覆盖任务队列用到的命令）"""

    def __init__(self):
        self.kv = {}                 # STRING
        self.streams = {}            # stream -> [(id, fields)]
        self.by_id = {}              # msg_id -> fields
        self.groups = {}             # (stream, group) -> {'cursor': int, 'pending': {id: consumer}}
        self.zsets = {}              # key -> {member: score}
        self.lists = {}              # key -> deque
        self._seq = 0
        self.deleted = set()         # XDEL 掉的 msg_id（xl en 不计入）
        self.fail = None             # 置为异常实例后所有命令都抛错（模拟 Redis 挂掉）

    def _maybe_fail(self):
        if self.fail is not None:
            raise self.fail

    # ---- string ----
    def set(self, key, value, ex=None):
        self._maybe_fail()
        self.kv[key] = value

    def get(self, key):
        self._maybe_fail()
        return self.kv.get(key)

    # ---- stream ----
    def xgroup_create(self, stream, group, id='0', mkstream=False):
        self._maybe_fail()
        key = (stream, group)
        if key in self.groups:
            raise Exception('BUSYGROUP Consumer Group name already exists')
        self.groups[key] = {'cursor': 0, 'pending': {}}
        self.streams.setdefault(stream, [])

    def xadd(self, stream, fields):
        self._maybe_fail()
        self._seq += 1
        msg_id = f'{self._seq}-0'
        self.streams.setdefault(stream, []).append((msg_id, dict(fields)))
        self.by_id[msg_id] = dict(fields)
        return msg_id

    def xreadgroup(self, group, consumer, streams, count=1, block=None):
        self._maybe_fail()
        stream = next(iter(streams))
        state = self.groups[(stream, group)]
        msgs = self.streams.get(stream, [])
        out = []
        while state['cursor'] < len(msgs) and len(out) < count:
            msg_id, fields = msgs[state['cursor']]
            state['cursor'] += 1
            if msg_id in self.deleted:      # XDEL 过的条目不再投递
                continue
            state['pending'][msg_id] = consumer
            out.append((msg_id, fields))
        return [(stream, out)] if out else []

    def xack(self, stream, group, msg_id):
        self._maybe_fail()
        return 1 if self.groups[(stream, group)]['pending'].pop(msg_id, None) else 0

    def xdel(self, stream, msg_id):
        self._maybe_fail()
        self.by_id.pop(msg_id, None)
        self.deleted.add(msg_id)        # 真实 Redis 会真正移除条目（XLEN 随之减少）
        return 1

    def xlen(self, stream):
        self._maybe_fail()
        return sum(1 for msg_id, _ in self.streams.get(stream, []) if msg_id not in self.deleted)

    def xpending(self, stream, group):
        self._maybe_fail()
        return [len(self.groups[(stream, group)]['pending']), None, None, None]

    def xautoclaim(self, stream, group, consumer, min_idle_time=0, start_id='0-0', count=10):
        self._maybe_fail()
        pending = self.groups[(stream, group)]['pending']
        entries = []
        for msg_id, owner in list(pending.items()):
            if owner == consumer:
                continue
            if len(entries) >= count:
                break
            pending[msg_id] = consumer
            entries.append((msg_id, self.by_id.get(msg_id, {})))
        return ('0-0', entries, [])

    # ---- zset ----
    def zadd(self, key, mapping):
        self._maybe_fail()
        self.zsets.setdefault(key, {}).update(mapping)
        return 1

    def zrangebyscore(self, key, min, max, start=0, num=None):
        self._maybe_fail()
        low = float('-inf') if min == '-inf' else float(min)
        high = float('inf') if max == '+inf' else float(max)
        items = [(m, s) for m, s in self.zsets.get(key, {}).items() if low <= s <= high]
        items.sort(key=lambda kv: kv[1])
        items = items[start:start + num if num is not None else None]
        return [m for m, _ in items]

    def zrem(self, key, member):
        self._maybe_fail()
        return 1 if self.zsets.get(key, {}).pop(member, None) is not None else 0

    def zcard(self, key):
        self._maybe_fail()
        return len(self.zsets.get(key, {}))

    # ---- list ----
    def lpush(self, key, value):
        self._maybe_fail()
        self.lists.setdefault(key, deque()).appendleft(value)
        return len(self.lists[key])

    def ltrim(self, key, start, end):
        self._maybe_fail()
        items = list(self.lists.get(key, []))[start:end + 1]
        self.lists[key] = deque(items)

    def llen(self, key):
        self._maybe_fail()
        return len(self.lists.get(key, []))

    def lrange(self, key, start, end):
        self._maybe_fail()
        items = list(self.lists.get(key, []))
        return items[start:end + 1]

    def close(self):
        pass


@pytest.fixture
def fake():
    return FakeRedis()


@pytest.fixture
def queue(fake):
    return RedisTaskQueue('redis://fake:6379/0', client=fake)


@pytest.fixture(autouse=True)
def _clean():
    registry.clear()
    reset_task_queue(None)
    yield
    registry.clear()
    reset_task_queue(None)


# ------------------------- 基础流转 -------------------------


def test_enqueue_writes_stream_and_status(queue, fake):
    task_id = queue.enqueue('demo.job', args=[1], kwargs={'k': 'v'})
    assert fake.xlen(queue.stream) == 1
    status = queue.get_status(task_id)
    assert status['status'] == STATUS_QUEUED
    assert status['name'] == 'demo.job'


def test_reserve_moves_to_pending_and_marks_running(queue, fake):
    task_id = queue.enqueue('demo.job')
    reserved = queue.reserve(timeout=0.01)
    assert reserved is not None
    assert reserved.envelope.id == task_id
    assert fake.groups[(queue.stream, queue.group)]['pending'] == {
        reserved.token: queue.consumer}
    assert queue.get_status(task_id)['status'] == STATUS_RUNNING


def test_ack_removes_message_and_sets_succeeded(queue, fake):
    task_id = queue.enqueue('demo.job')
    reserved = queue.reserve(timeout=0.01)
    queue.ack(reserved)

    assert queue.get_status(task_id)['status'] == STATUS_SUCCEEDED
    assert fake.groups[(queue.stream, queue.group)]['pending'] == {}
    assert fake.by_id == {}


def test_reserve_timeout_returns_none(queue):
    assert queue.reserve(timeout=0.01) is None


def test_retry_schedules_delayed_with_attempts(queue, fake):
    task_id = queue.enqueue('flaky')
    reserved = queue.reserve(timeout=0.01)
    queue.retry(reserved, delay=0.01, error='boom')

    status = queue.get_status(task_id)
    assert status['status'] == STATUS_RETRYING
    assert status['attempts'] == 1
    assert status['last_error'] == 'boom'
    assert len(fake.zsets[queue.delayed_key]) == 1
    assert fake.groups[(queue.stream, queue.group)]['pending'] == {}


def test_delayed_promotion_puts_message_back(queue, fake):
    task_id = queue.enqueue('flaky')
    reserved = queue.reserve(timeout=0.01)
    queue.retry(reserved, delay=0.0, error='boom')

    queue._last_promote = 0            # 跳过 1s 节流
    again = queue.reserve(timeout=0.01)
    assert again is not None
    assert again.envelope.id == task_id
    assert again.envelope.attempts == 1
    assert fake.zsets[queue.delayed_key] == {}


def test_dead_letter(queue, fake):
    task_id = queue.enqueue('doomed')
    reserved = queue.reserve(timeout=0.01)
    queue.dead(reserved, error='fatal')

    assert queue.get_status(task_id)['status'] == STATUS_DEAD
    assert fake.llen(queue.dead_key) == 1
    assert 'fatal' in list(fake.lists[queue.dead_key])[0]


def test_dead_letter_is_trimmed(queue, fake, monkeypatch):
    from flask_server import config
    monkeypatch.setattr(config, 'task_dead_letter_max', 3, raising=False)
    for _ in range(5):
        queue.enqueue('x')
        queue.dead(queue.reserve(timeout=0.01), error='e')
    assert fake.llen(queue.dead_key) == 3


# ------------------------- 健壮性 -------------------------


def test_corrupt_message_goes_to_dead_letter(queue, fake):
    """脏数据（非法 JSON）不能让整条流阻塞"""
    fake.xadd(queue.stream, {'data': 'not-json'})
    assert queue.reserve(timeout=0.01) is None
    assert fake.llen(queue.dead_key) == 1


def test_reclaim_stale_takes_over_other_consumer(queue, fake, monkeypatch):
    """worker 崩溃留下的 pending 消息会被接管并重新投递"""
    from flask_server import config
    monkeypatch.setattr(config, 'task_retry_backoff', 0, raising=False)   # 接管后立即可重投

    queue.enqueue('orphan')
    # 模拟另一个消费者取走后崩溃
    other = RedisTaskQueue('redis://fake:6379/0', client=fake)
    assert other.reserve(timeout=0.01) is not None

    reclaimed = queue.reclaim_stale(count=10)
    assert reclaimed == 1
    queue._last_promote = 0
    again = queue.reserve(timeout=0.05)
    assert again is not None and again.envelope.name == 'orphan'
    assert again.envelope.attempts == 1


def test_get_status_raises_unavailable_when_backend_down(queue, fake):
    """查询状态时后端故障 → TaskQueueUnavailable（而不是裸 ConnectionError → 500）"""
    task_id = queue.enqueue('x')
    fake.fail = ConnectionError('down')
    with pytest.raises(TaskQueueUnavailable):
        queue.get_status(task_id)


def test_init_raises_unavailable_when_backend_down():
    """初始化失败（Redis 不可达）统一暴露为 TaskQueueUnavailable"""
    class BrokenRedis(FakeRedis):
        def xgroup_create(self, *a, **kw):
            raise ConnectionError('cannot connect')

    with pytest.raises(TaskQueueUnavailable):
        RedisTaskQueue('redis://fake:6379/0', client=BrokenRedis())


def test_dead_letters_reader(queue, fake):
    """死信读取接口（运维排查用）"""
    task_id = queue.enqueue('doomed')
    queue.dead(queue.reserve(timeout=0.01), error='fatal')

    letters = queue.dead_letters(limit=5)
    assert len(letters) == 1
    assert letters[0]['id'] == task_id
    assert letters[0]['last_error'] == 'fatal'


def test_dead_letters_handles_corrupt_entries(queue, fake):
    fake.lpush(queue.dead_key, 'not-json')
    assert queue.dead_letters()[0]['raw'] == 'not-json'


def test_status_unknown_raises(queue):
    with pytest.raises(TaskNotFound):
        queue.get_status('missing')


def test_stats(queue, fake):
    queue.enqueue('a')
    queue.retry(queue.reserve(timeout=0.01), delay=10, error='e')
    queue.enqueue('b')
    stats = queue.stats()
    assert stats['ready'] == 1 and stats['delayed'] == 1 and stats['dead'] == 0


def test_enqueue_raises_when_backend_down(queue, fake):
    fake.fail = ConnectionError('redis is gone')
    with pytest.raises(TaskQueueUnavailable):
        queue.enqueue('nope')


def test_reserve_raises_when_backend_down(queue, fake):
    fake.fail = ConnectionError('redis is gone')
    with pytest.raises(TaskQueueUnavailable):
        queue.reserve(timeout=0.01)


def test_existing_group_is_tolerated(fake):
    """组已存在（BUSYGROUP）时初始化不报错"""
    RedisTaskQueue('redis://fake:6379/0', client=fake)
    RedisTaskQueue('redis://fake:6379/0', client=fake)   # 第二次不应抛


def test_envelope_json_roundtrip_through_stream(queue, fake):
    queue.enqueue('roundtrip', args=[1, [2, 3]], kwargs={'nested': {'a': True}})
    reserved = queue.reserve(timeout=0.01)
    env = TaskEnvelope.from_json(reserved.raw)
    assert env.args == [1, [2, 3]] and env.kwargs == {'nested': {'a': True}}


# ------------------------- 与 worker 的集成 -------------------------


def test_worker_processes_redis_backed_task(queue, fake):
    from flask_server.module.task_queue import task
    from flask_server.module.task_worker import TaskWorker

    done = []

    @task('redis.job')
    def job(x):
        done.append(x)

    from flask_server import config
    old = config.task_reserve_timeout
    config.task_reserve_timeout = 0.05
    task_id = queue.enqueue('redis.job', args=[42])
    worker = TaskWorker(queue=queue, concurrency=1).start()
    try:
        for _ in range(200):
            if queue.get_status(task_id)['status'] == STATUS_SUCCEEDED:
                break
            import time
            time.sleep(0.02)
        assert done == [42]
        assert queue.get_status(task_id)['status'] == STATUS_SUCCEEDED
    finally:
        worker.stop()
        config.task_reserve_timeout = old
