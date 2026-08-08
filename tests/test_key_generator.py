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
