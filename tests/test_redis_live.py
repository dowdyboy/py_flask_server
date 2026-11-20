"""真实 Redis 集成测试：设置 REDIS_URL 后才运行（CI 用 `-m redis_live` 选择）。

假客户端（test_task_queue_redis.py / test_redis_cache.py）能验证命令调用形状，
但证明不了 Redis 服务端语义：GETDEL 的原子性、consumer group 的 pending/claim/ack
状态机、XPENDING 摘要结构。这里全部用真实 Redis 覆盖。

隔离约定：
  - 每个用例使用随机 key 前缀（pytest:redis_live:<uuid>:）；
  - 只删除自己创建、且匹配该前缀的 key，绝不 FLUSHDB。
"""

import os
import threading
import uuid

import pytest

pytestmark = [
    pytest.mark.redis_live,
    pytest.mark.skipif(not os.environ.get('REDIS_URL'), reason='REDIS_URL not set'),
]


@pytest.fixture
def live_redis():
    """真实 Redis 客户端 + 本次用例独占的 key 前缀"""
    redis_mod = pytest.importorskip('redis')
    redis_url = os.environ.get('REDIS_URL')
    client = redis_mod.Redis.from_url(
        redis_url, decode_responses=True,
        socket_connect_timeout=3, socket_timeout=5,
    )
    try:
        client.ping()
    except Exception as exc:
        pytest.skip(f'REDIS_URL 不可达，跳过真实 Redis 集成测试: {exc}')

    prefix = f'pytest:redis_live:{uuid.uuid4().hex}:'
    try:
        yield client, prefix
    finally:
        try:
            for key in client.scan_iter(match=f'{prefix}*'):
                client.delete(key)
        finally:
            client.close()


def _require_getdel(client):
    """GETDEL 需要 Redis >= 6.2；老服务端明确 skip 而不是报未知命令"""
    try:
        client.getdel('pytest:redis_live:probe:getdel')
    except Exception as exc:
        if 'unknown command' in str(exc).lower():
            pytest.skip(f'Redis 版本过低，不支持 GETDEL: {exc}')
        raise


def _create_group(client, stream, group):
    """创建 consumer group（stream 不存在时一并创建）"""
    client.xgroup_create(stream, group, id='0', mkstream=True)


def test_getdel_is_atomic(live_redis):
    """两个并发消费者同时 GETDEL 同一 key：恰好一个拿到值，另一个拿到 None"""
    client, prefix = live_redis
    _require_getdel(client)
    key = f'{prefix}getdel'
    client.set(key, 'winner')

    barrier = threading.Barrier(2, timeout=5)
    results = []
    results_lock = threading.Lock()

    def consume():
        barrier.wait()
        value = client.getdel(key)
        with results_lock:
            results.append(value)

    threads = [threading.Thread(target=consume) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads), 'GETDEL 并发线程未结束'
    assert results.count('winner') == 1, f'GETDEL 不原子: {results}'
    assert results.count(None) == 1, f'GETDEL 不原子（多个消费者拿到值）: {results}'
    assert client.exists(key) == 0


def test_xadd_xreadgroup_xack_xdel_roundtrip(live_redis):
    """Streams 基本闭环：XADD → XREADGROUP → XPENDING → XACK → XDEL"""
    client, prefix = live_redis
    stream = f'{prefix}stream'
    group = 'workers'
    _create_group(client, stream, group)

    msg_id = client.xadd(stream, {'data': 'payload-1'})
    resp = client.xreadgroup(group, 'worker-a', {stream: '>'}, count=1, block=200)
    assert resp, 'XREADGROUP 未读到刚写入的消息'
    read_stream, entries = resp[0]
    assert read_stream == stream
    assert len(entries) == 1
    entry_id, fields = entries[0]
    assert entry_id == msg_id
    assert fields['data'] == 'payload-1'

    # 未 ACK：消息在 pending 列表里，XLEN 仍包含它
    summary = client.xpending(stream, group)
    assert summary['pending'] == 1
    assert client.xlen(stream) == 1

    assert client.xack(stream, group, entry_id) == 1
    assert client.xpending(stream, group)['pending'] == 0
    assert client.xdel(stream, entry_id) == 1
    assert client.xlen(stream) == 0


def test_xautoclaim_reclaims_stale_pending(live_redis):
    """XAUTOCLAIM：崩溃 worker 遗留的 pending 消息可被新 consumer 接管"""
    client, prefix = live_redis
    stream = f'{prefix}claim'
    group = 'workers'
    _create_group(client, stream, group)

    msg_id = client.xadd(stream, {'data': 'orphan'})
    first = client.xreadgroup(group, 'crashed-worker', {stream: '>'}, count=1, block=200)
    assert first
    read_id = first[0][1][0][0]
    assert read_id == msg_id

    # min_idle_time=0：允许立即接管（生产按 TASK_VISIBILITY_TIMEOUT 设置）
    claimed = client.xautoclaim(
        stream, group, 'recovery-worker', min_idle_time=0, start_id='0-0', count=10)
    # redis-py 返回 (next_id, entries) 或 (next_id, entries, deleted)
    entries = claimed[1] if isinstance(claimed, (list, tuple)) and len(claimed) >= 2 else []
    claimed_ids = [entry[0] for entry in entries]
    assert msg_id in claimed_ids, f'XAUTOCLAIM 未接管 pending 消息: {claimed!r}'

    # owner 已转移：pending 详情里的 consumer 不再是崩溃 worker
    details = client.xpending_range(stream, group, '-', '+', 10)
    owners = {detail.get('consumer') for detail in details}
    assert 'recovery-worker' in owners
    assert 'crashed-worker' not in owners

    client.xack(stream, group, msg_id)
    client.xdel(stream, msg_id)


def test_xpending_summary_shape(live_redis):
    """XPENDING 摘要（无 range 参数）返回 dict：pending/min/max/consumers"""
    client, prefix = live_redis
    stream = f'{prefix}pending'
    group = 'workers'
    _create_group(client, stream, group)
    client.xadd(stream, {'data': 'p1'})
    client.xreadgroup(group, 'worker-a', {stream: '>'}, count=1, block=200)

    summary = client.xpending(stream, group)
    assert isinstance(summary, dict), f'XPENDING 摘要形状异常: {summary!r}'
    assert {'pending', 'min', 'max', 'consumers'} <= set(summary)
    assert summary['pending'] == 1
    assert isinstance(summary['consumers'], list)
    assert summary['min'] is not None and summary['max'] is not None


def test_redis_task_queue_enqueue_reserve_ack_cycle(live_redis):
    """真实 Redis 上跑通 RedisTaskQueue：enqueue → reserve → ack（含状态与 pending）"""
    client, prefix = live_redis
    from flask_server.module.task_queue import (
        STATUS_QUEUED, STATUS_RUNNING, STATUS_SUCCEEDED, RedisTaskQueue,
    )

    stream = f'{prefix}queue'
    group = 'workers'
    queue = RedisTaskQueue(
        os.environ['REDIS_URL'], stream=stream, group=group, client=client)
    task_id = queue.enqueue('demo.job', args=[1], kwargs={'b': 2})
    # 与 live_redis fixture 共用客户端：key 清理与连接关闭都交给 fixture
    assert queue.get_status(task_id)['status'] == STATUS_QUEUED
    assert queue.get_status(task_id)['name'] == 'demo.job'

    reserved = queue.reserve(timeout=1)
    assert reserved is not None, '入队后应能 reserve 到任务'
    assert reserved.envelope.id == task_id
    assert reserved.envelope.args == [1]
    assert reserved.envelope.kwargs == {'b': 2}
    assert queue.get_status(task_id)['status'] == STATUS_RUNNING
    assert client.xpending(stream, group)['pending'] == 1

    queue.ack(reserved)
    assert queue.get_status(task_id)['status'] == STATUS_SUCCEEDED
    assert client.xpending(stream, group)['pending'] == 0
    assert client.xlen(stream) == 0
    assert queue.stats()['ready'] == 0
    assert queue.stats()['pending'] == 0
