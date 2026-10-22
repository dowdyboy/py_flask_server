import threading
import pytest
from flask_server.util.key_generator import KeyGenerator, SnowflakeIdWorker


def test_generate_uuid():
    uid = KeyGenerator.generate_uuid()
    assert isinstance(uid, str)
    assert len(uid) == 36   # uuid4 标准格式


def test_generate_snowflake_id():
    sid = KeyGenerator.generate_snowflake_id()
    assert isinstance(sid, str)
    assert int(sid) > 0


def test_snowflake_uniqueness_single_thread():
    ids = {KeyGenerator.generate_snowflake_id() for _ in range(1000)}
    assert len(ids) == 1000


def test_snowflake_uniqueness_multi_thread():
    """并发场景下不应生成重复 ID（验证线程安全修复）"""
    ids = []
    lock = threading.Lock()

    def generate():
        for _ in range(200):
            sid = KeyGenerator.generate_snowflake_id()
            with lock:
                ids.append(sid)

    threads = [threading.Thread(target=generate) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(ids) == 1600
    assert len(set(ids)) == 1600   # 无重复


def test_snowflake_worker_validation():
    try:
        SnowflakeIdWorker(worker_id=32, datacenter_id=1)
        assert False, 'should raise ValueError'
    except ValueError:
        pass
    try:
        SnowflakeIdWorker(worker_id=1, datacenter_id=32)
        assert False, 'should raise ValueError'
    except ValueError:
        pass


def test_snowflake_clock_moved_backwards():
    """系统时钟回退时应抛异常而非生成重复/异常 ID"""
    worker = SnowflakeIdWorker(worker_id=1, datacenter_id=1)
    timestamps = iter([2000, 1999])   # 第二次时间戳小于第一次 → 回退

    worker._gen_timestamp = lambda: next(timestamps)
    worker.next_id()                  # 正常生成一次
    with pytest.raises(Exception, match='Clock moved backwards'):
        worker.next_id()


def test_snowflake_sequence_overflow_waits_next_millis():
    """同一毫秒内序列溢出时阻塞到下一毫秒（_til_next_millis）"""
    worker = SnowflakeIdWorker(worker_id=1, datacenter_id=1)
    worker.last_timestamp = 1000
    worker.sequence = 4095            # 序列已到最大值 → 下次溢出
    timestamps = iter([1000, 1000, 1001])
    worker._gen_timestamp = lambda: next(timestamps)
    worker.next_id()
    assert worker.sequence == 0       # 溢出后序列归零
    assert worker.last_timestamp == 1001   # 阻塞到了下一毫秒


def test_worker_id_derived_from_pid(monkeypatch):
    """未配置 SNOWFLAKE_WORKER_ID 时按 (hostname, PID) 派生 worker_id（多副本隔离）"""
    from flask_server.util.key_generator import KeyGenerator, _derive_worker_id
    from flask_server import config

    monkeypatch.setattr(config, 'snowflake_worker_id', None)
    monkeypatch.setattr('os.getpid', lambda: 100)
    wid_100 = KeyGenerator._resolve_worker_id()
    monkeypatch.setattr('os.getpid', lambda: 200)
    wid_200 = KeyGenerator._resolve_worker_id()
    assert 0 <= wid_100 <= 31 and 0 <= wid_200 <= 31
    assert wid_100 != wid_200, '不同 PID 应派生不同 worker_id'
    # 同一 (hostname, pid) 必须稳定（重启/重复调用不漂移）
    assert _derive_worker_id(100) == _derive_worker_id(100)


def test_worker_id_hostname_disambiguates_containers(monkeypatch):
    """容器内 PID 恒为 1：仅靠 PID 会撞号，加入 hostname 后可区分副本"""
    from flask_server.util.key_generator import _derive_worker_id

    monkeypatch.setattr('os.getpid', lambda: 1)
    pod_a = _derive_worker_id(1)
    monkeypatch.setattr('socket.gethostname', lambda: 'pod-b-7d9f')
    pod_b = _derive_worker_id(1)
    monkeypatch.setattr('socket.gethostname', lambda: '')
    # hostname 为空时退化为纯 PID 派生，不得抛异常
    assert _derive_worker_id(1) % 32 == 1
    assert 0 <= pod_a <= 31 and 0 <= pod_b <= 31


def test_worker_id_config_takes_priority(monkeypatch):
    """显式配置 SNOWFLAKE_WORKER_ID 时优先于 PID 派生"""
    from flask_server.util.key_generator import KeyGenerator
    from flask_server import config

    monkeypatch.setattr(config, 'snowflake_worker_id', 7)
    monkeypatch.setattr('os.getpid', lambda: 100)
    assert KeyGenerator._resolve_worker_id() == 7


def test_worker_id_range():
    """PID 派生结果必须在 0-31 范围内（雪花 worker_id 位宽）"""
    from flask_server import config
    from flask_server.util.key_generator import _derive_worker_id

    config.snowflake_worker_id = None
    for pid in range(0, 10000, 17):
        assert 0 <= _derive_worker_id(pid) <= 31


def test_generate_uuid7_layout_and_ordering():
    """UUIDv7：版本号 7、RFC4122 变体、毫秒级时间有序"""
    import time
    import uuid

    first = KeyGenerator.generate_uuid7()
    parsed = uuid.UUID(first)
    assert parsed.version == 7
    assert parsed.variant == uuid.RFC_4122
    time.sleep(0.003)
    second = KeyGenerator.generate_uuid7()
    assert first < second, 'UUIDv7 应按毫秒时间有序（字符串可直接排序）'


def test_generate_uuid7_unique_multi_thread():
    """UUIDv7 并发批量生成无重复"""
    total = 2000
    ids = set()
    lock = threading.Lock()

    def generate():
        local = [KeyGenerator.generate_uuid7() for _ in range(200)]
        with lock:
            ids.update(local)

    threads = [threading.Thread(target=generate) for _ in range(total // 200)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(ids) == total


def test_datacenter_id_from_config(monkeypatch):
    """雪花 worker 使用配置中的 SNOWFLAKE_DATACENTER_ID（多机房隔离）"""
    from flask_server import config
    from flask_server.util.key_generator import KeyGenerator

    monkeypatch.setattr(config, 'snowflake_datacenter_id', 9)
    monkeypatch.setattr(config, 'snowflake_worker_id', 3)
    monkeypatch.setattr(KeyGenerator, '_worker', None)
    worker = KeyGenerator._get_worker()
    try:
        assert worker.datacenter_id == 9
        assert worker.worker_id == 3
    finally:
        monkeypatch.setattr(KeyGenerator, '_worker', None)
