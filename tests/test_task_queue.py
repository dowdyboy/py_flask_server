"""任务队列测试：注册表、内存后端语义、worker 端到端（成功/重试/死信/并发/停止）"""

import threading
import time

import pytest

from flask_server.module import task_queue as tq
from flask_server.module.task_queue import (
    MemoryTaskQueue, STATUS_DEAD, STATUS_QUEUED, STATUS_RETRYING, STATUS_RUNNING,
    STATUS_SUCCEEDED, TaskEnvelope, TaskNotFound, TaskQueueUnavailable, TaskRegistry,
    enqueue_task, get_task_status, registry, reset_task_queue, task, task_queue_stats,
)


def _wait_until(cond, timeout=5.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(interval)
    return False


@pytest.fixture(autouse=True)
def _clean_registry():
    """每个用例独立的任务注册表 + 队列单例"""
    registry.clear()
    reset_task_queue(None)
    yield
    registry.clear()
    reset_task_queue(None)


@pytest.fixture
def fast_config(monkeypatch):
    """把轮询/退避压到毫秒级，避免用例变慢或抖动"""
    from flask_server import config
    monkeypatch.setattr(config, 'task_reserve_timeout', 0.05, raising=False)
    monkeypatch.setattr(config, 'task_retry_backoff', 0.02, raising=False)
    monkeypatch.setattr(config, 'task_worker_concurrency', 2, raising=False)
    return config


# ------------------------- 注册表 -------------------------


def test_task_decorator_registers_with_name():
    @task('demo.hello')
    def hello():
        pass

    spec = registry.get('demo.hello')
    assert spec is not None and spec.func is hello
    assert 'demo.hello' in registry.names


def test_task_decorator_default_name_uses_module_and_func():
    @task()
    def unnamed():
        pass

    assert any(n.endswith('.unnamed') for n in registry.names)


def test_task_decorator_max_retries_override():
    @task('demo.retry', max_retries=7)
    def retry_demo():
        pass

    assert registry.get('demo.retry').max_retries == 7


def test_registry_overwrite_warns(monkeypatch):
    """同名重复注册：后者生效并告警（便于发现复制粘贴改名忘改）"""
    @task('dup')
    def first():
        pass

    @task('dup')
    def second():
        pass

    assert registry.get('dup').func is second


def test_registry_clear_and_unknown():
    @task('x')
    def x():
        pass

    registry.clear()
    assert registry.names == []
    assert registry.get('x') is None


def test_custom_registry_isolated():
    r = TaskRegistry()

    def f():
        pass

    r.register('a', f)
    assert r.names == ['a'] and registry.get('a') is None


# ------------------------- 信封序列化 -------------------------


def test_envelope_roundtrip():
    env = TaskEnvelope(id='t1', name='n', args=[1, 'x'], kwargs={'k': True}, attempts=2)
    again = TaskEnvelope.from_json(env.to_json())
    assert again.id == 't1' and again.args == [1, 'x'] and again.kwargs == {'k': True}
    assert again.attempts == 2


def test_new_task_id_is_time_prefixed_and_unique(monkeypatch):
    a = tq.new_task_id()
    b = tq.new_task_id()
    assert a != b
    assert len(a.split('-')[0]) == 13        # 毫秒时间戳前缀，便于按时间检索


# ------------------------- 内存后端 -------------------------


def test_memory_enqueue_reserve_ack(fast_config):
    q = MemoryTaskQueue()
    task_id = q.enqueue('demo.job', args=[1], kwargs={'b': 2})
    assert q.get_status(task_id)['status'] == STATUS_QUEUED

    reserved = q.reserve(timeout=0.5)
    assert reserved is not None
    assert reserved.envelope.name == 'demo.job'
    assert reserved.envelope.args == [1] and reserved.envelope.kwargs == {'b': 2}
    assert q.get_status(task_id)['status'] == STATUS_RUNNING

    q.ack(reserved)
    assert q.get_status(task_id)['status'] == STATUS_SUCCEEDED
    assert q.reserve(timeout=0.05) is None      # 队列已空


def test_memory_reserve_timeout_returns_none(fast_config):
    q = MemoryTaskQueue()
    started = time.time()
    assert q.reserve(timeout=0.1) is None
    assert time.time() - started >= 0.09


def test_memory_delayed_task_not_delivered_early(fast_config):
    q = MemoryTaskQueue()
    task_id = q.enqueue('later', delay=0.15)
    assert q.reserve(timeout=0.05) is None
    assert q.get_status(task_id)['status'] in (STATUS_QUEUED, STATUS_RETRYING)
    assert _wait_until(lambda: q.reserve(timeout=0.05) is not None, timeout=2)


def test_memory_retry_increments_attempts_and_keeps_task(fast_config):
    q = MemoryTaskQueue()
    task_id = q.enqueue('flaky')
    reserved = q.reserve(timeout=0.5)
    q.retry(reserved, delay=0.01, error='boom')

    status = q.get_status(task_id)
    assert status['status'] == STATUS_RETRYING
    assert status['attempts'] == 1
    assert status['last_error'] == 'boom'

    again = None
    for _ in range(60):                 # 最多等 3s
        again = q.reserve(timeout=0.05)
        if again is not None:
            break
    assert again is not None, '重试任务应重新入队'
    assert again.envelope.attempts == 1


def test_memory_dead_letter(fast_config):
    q = MemoryTaskQueue()
    task_id = q.enqueue('doomed')
    reserved = q.reserve(timeout=0.5)
    q.dead(reserved, error='fatal')

    assert q.get_status(task_id)['status'] == STATUS_DEAD
    assert q.stats()['dead'] == 1
    letters = q.dead_letters()
    assert letters and letters[-1]['id'] == task_id
    assert letters[-1]['last_error'] == 'fatal'


def test_memory_stats(fast_config):
    q = MemoryTaskQueue()
    q.enqueue('a')
    q.enqueue('b', delay=10)
    stats = q.stats()
    assert stats['ready'] == 1 and stats['delayed'] == 1


def test_memory_status_is_lru_capped(fast_config, monkeypatch):
    """状态表有上限，防止长期运行内存无界增长"""
    from flask_server import config
    monkeypatch.setattr(config, 'task_status_max', 5, raising=False)
    q = MemoryTaskQueue(max_status=5)
    for _ in range(20):
        q.enqueue('x')
    assert q.stats()['tracked'] == 5


def test_memory_unknown_task_status_raises(fast_config):
    q = MemoryTaskQueue()
    with pytest.raises(TaskNotFound):
        q.get_status('nope')


def test_memory_close_wakes_reserve(fast_config):
    q = MemoryTaskQueue()
    threading.Timer(0.05, q.close).start()
    assert q.reserve(timeout=2) is None


# ------------------------- 单例与便捷函数 -------------------------


def test_singleton_uses_memory_without_redis(fast_config):
    from flask_server import config
    assert config.redis_url is None
    q = tq.get_task_queue()
    assert isinstance(q, MemoryTaskQueue)
    assert tq.get_task_queue() is q


def test_reset_queue_rebuilds(fast_config):
    first = tq.get_task_queue()
    tq.reset_task_queue()
    assert tq.get_task_queue() is not first


def test_enqueue_and_status_helpers(fast_config):
    q = MemoryTaskQueue()
    reset_task_queue(q)
    task_id = enqueue_task('helper.job', 1, 2, k=3)
    assert q.get_status(task_id)['name'] == 'helper.job'
    assert get_task_status(task_id)['status'] == STATUS_QUEUED
    assert task_queue_stats()['ready'] == 1


def test_worker_process_one_sync(fast_config):
    """process_one：同步处理一条（--once 模式使用，不启动线程）"""
    from flask_server.module.task_worker import TaskWorker

    ran = []

    @task('sync.task')
    def sync_task(v):
        ran.append(v)

    q = MemoryTaskQueue()
    worker = TaskWorker(queue=q, concurrency=1)
    assert worker.process_one(timeout=0.01) is False      # 空队列

    q.enqueue('sync.task', args=[5])
    assert worker.process_one(timeout=0.5) is True
    assert ran == [5]
    assert worker.processed == 1
    assert worker._threads == []                          # 没有启动消费线程


def test_worker_cli_once_prints_summary(fast_config, monkeypatch, capsys):
    """--once 打印处理条数，且不遗留消费线程"""
    cli = _load_worker_cli()

    @task('cli.sync')
    def cli_sync():
        pass

    q = MemoryTaskQueue()
    q.enqueue('cli.sync')
    monkeypatch.setattr(cli, '_build_queue', lambda stream: q)

    assert cli.main(['--once']) == 0
    out = capsys.readouterr().out
    assert '单次模式：处理 1 个任务' in out


# ------------------------- worker 端到端 -------------------------


def test_worker_executes_successful_task(fast_config):
    from flask_server.module.task_worker import TaskWorker

    done = threading.Event()
    seen = {}

    @task('ok.task')
    def ok(a, b=None):
        seen['args'] = (a, b)
        done.set()

    q = MemoryTaskQueue()
    task_id = q.enqueue('ok.task', args=[1], kwargs={'b': 2})
    worker = TaskWorker(queue=q, concurrency=1).start()
    try:
        assert done.wait(5), '任务未被消费'
        assert seen['args'] == (1, 2)
        assert _wait_until(lambda: q.get_status(task_id)['status'] == STATUS_SUCCEEDED)
    finally:
        worker.stop()


def test_worker_retries_then_succeeds(fast_config):
    from flask_server.module.task_worker import TaskWorker

    calls = []

    @task('flaky.task', max_retries=3)
    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError(f'fail #{len(calls)}')

    q = MemoryTaskQueue()
    task_id = q.enqueue('flaky.task')
    worker = TaskWorker(queue=q, concurrency=1).start()
    try:
        assert _wait_until(
            lambda: q.get_status(task_id)['status'] == STATUS_SUCCEEDED, timeout=10), \
            q.get_status(task_id)
        assert len(calls) == 3
    finally:
        worker.stop()


def test_worker_dead_letters_after_max_retries(fast_config):
    from flask_server.module.task_worker import TaskWorker

    @task('always.fail', max_retries=1)
    def always_fail():
        raise ValueError('nope')

    q = MemoryTaskQueue()
    task_id = q.enqueue('always.fail')
    worker = TaskWorker(queue=q, concurrency=1).start()
    try:
        assert _wait_until(lambda: q.get_status(task_id)['status'] == STATUS_DEAD, timeout=10)
        # 状态先落死信、计数随后累加，这里等计数（避免与 worker 线程竞态）
        assert _wait_until(lambda: worker.failed == 1, timeout=2)
        assert q.dead_letters()[-1]['last_error'].startswith('ValueError')
    finally:
        worker.stop()


def test_worker_dead_letters_unregistered_task(fast_config):
    from flask_server.module.task_worker import TaskWorker

    q = MemoryTaskQueue()
    task_id = q.enqueue('no.such.task')
    worker = TaskWorker(queue=q, concurrency=1).start()
    try:
        assert _wait_until(lambda: q.get_status(task_id)['status'] == STATUS_DEAD, timeout=5)
        assert 'not registered' in q.dead_letters()[-1]['last_error']
    finally:
        worker.stop()


def test_worker_runs_handler_in_app_context(fast_config):
    """handler 里能拿到 Flask app 上下文（任务常需要 db.session）"""
    from flask import current_app
    from flask_server import app as flask_app
    from flask_server.module.task_worker import TaskWorker

    seen = threading.Event()

    @task('ctx.task')
    def needs_context():
        assert current_app._get_current_object() is flask_app
        seen.set()

    q = MemoryTaskQueue()
    q.enqueue('ctx.task')
    worker = TaskWorker(app=flask_app, queue=q, concurrency=1).start()
    try:
        assert seen.wait(5), '任务未在 app 上下文内执行'
    finally:
        worker.stop()


def test_worker_concurrency_processes_many(fast_config):
    from flask_server.module.task_worker import TaskWorker

    lock = threading.Lock()
    processed = []

    @task('bulk.task')
    def bulk(i):
        time.sleep(0.01)
        with lock:
            processed.append(i)

    q = MemoryTaskQueue()
    for i in range(10):
        q.enqueue('bulk.task', args=[i])
    worker = TaskWorker(queue=q, concurrency=4).start()
    try:
        assert _wait_until(lambda: len(processed) == 10, timeout=15), len(processed)
    finally:
        worker.stop()
    assert sorted(processed) == list(range(10))


def test_worker_stop_is_idempotent_and_reports(fast_config):
    from flask_server.module.task_worker import TaskWorker

    @task('noop.task')
    def noop():
        pass

    q = MemoryTaskQueue()
    worker = TaskWorker(queue=q, concurrency=2).start()
    assert worker.running is True
    worker.stop()
    assert worker.running is False
    worker.stop()          # 幂等


def test_worker_survives_backend_outage(fast_config):
    """后端短暂不可用时不让消费线程死掉（退避后恢复）"""
    from flask_server.module.task_worker import TaskWorker

    class FlakyQueue(MemoryTaskQueue):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def reserve(self, timeout=1.0):
            self.calls += 1
            if self.calls <= 2:
                raise TaskQueueUnavailable('redis down')
            return super().reserve(timeout)

    done = threading.Event()

    @task('recover.task')
    def recover():
        done.set()

    q = FlakyQueue()
    q.enqueue('recover.task')
    worker = TaskWorker(queue=q, concurrency=1).start()
    try:
        assert done.wait(10), '后端恢复后应继续消费'
    finally:
        worker.stop()


# ------------------------- 内嵌 worker 与 CLI -------------------------


def test_embedded_worker_disabled_by_default(fast_config):
    from flask_server.module import start_embedded_task_worker

    assert fast_config.task_worker_embedded is False
    assert start_embedded_task_worker() is None


def test_embedded_worker_starts_when_enabled(fast_config, monkeypatch):
    from flask_server.module import start_embedded_task_worker

    @task('embedded.task')
    def embedded():
        pass

    monkeypatch.setattr(fast_config, 'task_worker_embedded', True, raising=False)
    q = MemoryTaskQueue()
    reset_task_queue(q)
    worker = start_embedded_task_worker()
    try:
        assert worker is not None and worker.running is True
    finally:
        worker.stop()


def test_embedded_worker_without_tasks_warns(fast_config, monkeypatch):
    from flask_server.module import start_embedded_task_worker

    monkeypatch.setattr(fast_config, 'task_worker_embedded', True, raising=False)
    assert start_embedded_task_worker() is None      # 注册表为空 → 不启动


def _load_worker_cli():
    import importlib.util
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        'worker_cli_under_test', os.path.join(root, 'scripts', 'worker.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_worker_cli_exits_when_no_tasks(capsys):
    cli = _load_worker_cli()
    assert cli.main([]) == 1
    assert '没有可用任务' in capsys.readouterr().out


def test_worker_cli_once_processes_pending_task(fast_config, monkeypatch, capsys):
    """--once：处理完当前可立即取到的任务就退出（联调用）"""
    cli = _load_worker_cli()
    ran = []

    @task('cli.task')
    def cli_task(n):
        ran.append(n)

    q = MemoryTaskQueue()
    q.enqueue('cli.task', args=[7])
    monkeypatch.setattr(cli, '_build_queue', lambda stream: q)

    assert cli.main(['--once']) == 0
    assert ran == [7]
    assert '已注册任务' in capsys.readouterr().out
