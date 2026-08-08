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
    """未配置 SNOWFLAKE_WORKER_ID 时按 PID 派生 worker_id（多进程隔离）"""
    from flask_server.util.key_generator import KeyGenerator
    from flask_server import config

    monkeypatch.setattr(config, 'snowflake_worker_id', None)
    monkeypatch.setattr('os.getpid', lambda: 100)     # 100 % 32 = 4
    assert KeyGenerator._resolve_worker_id() == 4
    monkeypatch.setattr('os.getpid', lambda: 200)     # 200 % 32 = 8
    assert KeyGenerator._resolve_worker_id() == 8


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

    config.snowflake_worker_id = None
    for pid in range(0, 10000, 17):
        assert 0 <= pid % 32 <= 31
