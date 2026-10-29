"""任务队列端到端测试：内存后端语义、worker 生命周期、真实 Redis、CLI、内嵌 worker。"""

import json
import os
import time
import uuid
from pathlib import Path

from common import (
    Results, Server, TMP_DIR, base_env, cleanup_redis, dump_exception, free_port,
    get_json, install_probe_modules, redis_url, remove_probe_modules, run_py, run_script,
)

os.environ.update(base_env())

R = Results('TaskQueue')

URL = redis_url(15)


def _wait_until(cond, timeout=8.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if cond():
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(interval)
    return False


def _save_config(keys):
    from flask_server.config import config
    return {k: getattr(config, k) for k in keys}


def _restore_config(saved):
    from flask_server.config import config
    for k, v in saved.items():
        setattr(config, k, v)


# --------------------------------------------------------------------------
# 内存后端
# --------------------------------------------------------------------------

def test_memory_queue_semantics():
    from flask_server.module.task_queue import (
        MemoryTaskQueue, TaskNotFound, TaskQueueUnavailable, reset_task_queue,
        enqueue_task, get_task_status, task_queue_stats, registry, task,
    )

    registry.clear()
    reset_task_queue(None)
    q = MemoryTaskQueue(max_status=3, dead_max=2)
    calls = []

    @task('mem.ok')
    def mem_ok(value):
        calls.append(value)

    # 基本状态流转
    tid = q.enqueue('mem.ok', args=['a'])
    R.eq('内存队列 enqueue 返回 id', isinstance(tid, str) and len(tid) > 0, True)
    st = q.get_status(tid)
    R.eq('入队状态 queued', st['status'], 'queued')
    R.eq('入队 attempts=0', st['attempts'], 0)
    R.eq('入队 max_retries 取配置', st['max_retries'], 2)  # base_env TASK_MAX_RETRIES=2
    R.eq('stats ready=1', q.stats()['ready'], 1)

    reserved = q.reserve(timeout=0.1)
    R.check('reserve 取到任务', reserved is not None)
    R.eq('reserve 后 running', q.get_status(tid)['status'], 'running')
    q.ack(reserved)
    R.eq('ack 后 succeeded', q.get_status(tid)['status'], 'succeeded')

    # 延迟任务
    delayed_id = q.enqueue('mem.ok', args=['d'], delay=0.2)
    R.eq('延迟任务 delayed=1', q.stats()['delayed'], 1)
    R.eq('延迟未到期 reserve 返回 None', q.reserve(timeout=0.05), None)
    time.sleep(0.25)
    r = q.reserve(timeout=0.5)
    R.check('延迟到期后可取', r is not None and r.envelope.id == delayed_id)
    q.ack(r)

    # 重试语义
    retry_id = q.enqueue('mem.ok', args=['r'])
    r = q.reserve(timeout=0.1)
    q.retry(r, delay=0.1, error='planned')
    st = q.get_status(retry_id)
    R.eq('retry 后 retrying', st['status'], 'retrying')
    R.eq('retry attempts=1', st['attempts'], 1)
    R.eq('retry last_error', st['last_error'], 'planned')
    time.sleep(0.15)
    r = q.reserve(timeout=0.5)
    R.check('重试到期后重新取到', r is not None and r.envelope.id == retry_id)
    q.ack(r)

    # 死信
    dead_id = q.enqueue('mem.ok', args=['x'])
    r = q.reserve(timeout=0.1)
    q.dead(r, 'dead-error')
    st = q.get_status(dead_id)
    R.eq('dead 状态', st['status'], 'dead')
    R.eq('dead last_error', st['last_error'], 'dead-error')
    letters = q.dead_letters()
    R.eq('死信数量 1', len(letters), 1)
    R.eq('死信记录 id', letters[0]['id'], dead_id)
    R.eq('死信记录保留 args', letters[0].get('args'), ['x'])
    R.eq('死信记录保留 kwargs', letters[0].get('kwargs'), {})

    # 状态表 LRU 上限
    q2 = MemoryTaskQueue(max_status=3)
    ids = [q2.enqueue('mem.ok', args=[i]) for i in range(4)]
    R.eq('状态表上限 tracked=3', q2.stats()['tracked'], 3)
    R.raises('最旧状态被 LRU 淘汰', lambda: q2.get_status(ids[0]), TaskNotFound)
    R.check('最新状态仍可查', q2.get_status(ids[-1])['status'] == 'queued')

    # 死信上限（保留最新）
    q3 = MemoryTaskQueue(dead_max=2)
    for i in range(3):
        r = q3.reserve(timeout=0.01)
        if r is None:
            q3.enqueue('mem.ok', args=[i])
            r = q3.reserve(timeout=0.1)
        q3.dead(r, f'e{i}')
    letters = q3.dead_letters()
    R.eq('死信上限 2', len(letters), 2)
    R.eq('死信保留最新一条', letters[-1]['last_error'], 'e2')

    # 关闭后不再出队
    q4 = MemoryTaskQueue()
    q4.close()
    R.eq('close 后 reserve 返回 None', q4.reserve(timeout=0.05), None)

    # 单例便捷函数
    reset_task_queue(q)
    tid = enqueue_task('mem.ok', 'singleton')
    R.eq('enqueue_task 单例', get_task_status(tid)['status'], 'queued')
    R.check('task_queue_stats 单例', task_queue_stats()['ready'] >= 1)
    R.raises('未知 task_id 抛 TaskNotFound', lambda: get_task_status('nope'), TaskNotFound)
    reset_task_queue(None)

    # 装饰器注册表
    R.check('task 装饰器注册', registry.get('mem.ok') is not None)
    R.check('registry.names 包含任务', 'mem.ok' in registry.names)
    registry.clear()


def test_worker_memory():
    from flask_server.app import create_app
    from flask_server.config import config
    from flask_server.module.task_queue import (
        MemoryTaskQueue, registry, task,
    )
    from flask_server.module.task_worker import TaskWorker

    saved = _save_config(['task_retry_backoff', 'task_reserve_timeout', 'task_max_retries'])
    config.task_retry_backoff = 0.05
    config.task_reserve_timeout = 0.05
    config.task_max_retries = 2
    registry.clear()
    app = create_app({'TESTING': True})
    results = []
    from flask_server.module import memory_cache
    memory_cache.delete('e2e:flaky')

    @task('worker.ctx')
    def worker_ctx(value):
        from flask import has_app_context
        results.append((value, has_app_context()))

    @task('worker.flaky')
    def worker_flaky():
        from flask_server.module import memory_cache
        n = memory_cache.incr('e2e:flaky', ttl=60)
        if n < 3:
            raise RuntimeError(f'flaky {n}')
        results.append(('flaky-ok', n))

    @task('worker.always_fail')
    def worker_always_fail():
        raise ValueError('always')

    try:
        # process_one 同步处理 + app context
        q = MemoryTaskQueue()
        w = TaskWorker(app=app, queue=q, concurrency=1)
        tid = q.enqueue('worker.ctx', args=['one'])
        R.eq('process_one 处理成功', w.process_one(timeout=0.5), True)
        R.eq('process_one 空队列返回 False', w.process_one(timeout=0.01), False)
        R.eq('handler 在 app context 内执行', results, [('one', True)])
        R.eq('process_one 后状态 succeeded', q.get_status(tid)['status'], 'succeeded')

        # 失败重试直到成功
        q2 = MemoryTaskQueue()
        w2 = TaskWorker(app=app, queue=q2, concurrency=2)
        w2.start()
        tid = q2.enqueue('worker.flaky')
        R.check('worker 重试后成功', _wait_until(lambda: q2.get_status(tid)['status'] == 'succeeded', 8))
        R.eq('flaky 共执行 3 次', q2.get_status(tid)['attempts'], 2)
        R.check('flaky 结果写入', any(x[0] == 'flaky-ok' for x in results))
        w2.stop()

        # 超过重试上限进死信
        q3 = MemoryTaskQueue()
        w3 = TaskWorker(app=app, queue=q3, concurrency=1)
        w3.start()
        tid = q3.enqueue('worker.always_fail')
        R.check('超过重试上限进死信', _wait_until(lambda: q3.get_status(tid)['status'] == 'dead', 8))
        R.eq('死信 attempts', q3.get_status(tid)['attempts'], 2)
        R.check('死信 last_error 含 ValueError', 'ValueError' in (q3.get_status(tid)['last_error'] or ''))
        w3.stop()

        # 未注册任务 -> 死信
        q4 = MemoryTaskQueue()
        w4 = TaskWorker(app=app, queue=q4, concurrency=1)
        tid = q4.enqueue('worker.unknown')
        R.eq('未注册任务 process_one 返回 True', w4.process_one(timeout=0.2), True)
        R.eq('未注册任务进死信', q4.get_status(tid)['status'], 'dead')
        R.check('未注册任务 last_error', 'not registered' in (q4.get_status(tid)['last_error'] or ''))

        # 并发消费
        q5 = MemoryTaskQueue()
        w5 = TaskWorker(app=app, queue=q5, concurrency=4)
        w5.start()
        ids = [q5.enqueue('worker.ctx', args=[f'c{i}']) for i in range(12)]
        R.check('并发消费全部成功',
                _wait_until(lambda: all(q5.get_status(i)['status'] == 'succeeded' for i in ids), 10))
        R.eq('并发 processed 计数', w5.processed, 12)
        w5.stop()

        # stop 等待在途任务完成
        from flask_server.module.task_queue import task as task_decorator

        @task_decorator('worker.slow')
        def worker_slow():
            time.sleep(0.6)
            results.append(('slow-done', True))

        q6 = MemoryTaskQueue()
        w6 = TaskWorker(app=app, queue=q6, concurrency=1)
        w6.start()
        tid = q6.enqueue('worker.slow')
        R.check('慢任务进入 running', _wait_until(lambda: q6.get_status(tid)['status'] == 'running', 3))
        t0 = time.time()
        w6.stop(timeout=5)
        elapsed = time.time() - t0
        R.check('stop 等待在途任务完成', elapsed >= 0.4, f'{elapsed:.2f}s')
        R.eq('stop 后在途任务 succeeded', q6.get_status(tid)['status'], 'succeeded')
        R.check('stop 后慢任务结果写入', ('slow-done', True) in results)
    finally:
        _restore_config(saved)
        registry.clear()


# --------------------------------------------------------------------------
# Redis 后端
# --------------------------------------------------------------------------

def test_redis_queue_semantics():
    if not URL:
        print('  SKIP  Redis 未配置，跳过 Redis 队列测试')
        return
    from flask_server.module.task_queue import (
        RedisTaskQueue, TaskNotFound, TaskQueueUnavailable,
    )

    stream = f'e2e:tasks:{uuid.uuid4().hex[:8]}'
    group = f'e2e-g-{uuid.uuid4().hex[:6]}'
    q = RedisTaskQueue(URL, stream=stream, group=group)
    try:
        t1 = q.enqueue('e2e.echo', args=['a'])
        t2 = q.enqueue('e2e.echo', args=['b'])
        R.eq('Redis enqueue 返回 id', isinstance(t1, str) and t1 != t2, True)
        st = q.stats()
        R.eq('Redis stats ready=2', st['ready'], 2)
        R.eq('Redis stats pending=0', st['pending'], 0)

        r = q.reserve(timeout=1)
        R.check('Redis reserve 取到任务', r is not None)
        R.eq('Redis reserve 后 running', q.get_status(r.envelope.id)['status'], 'running')
        st = q.stats()
        R.eq('Redis reserve 后 pending=1', st['pending'], 1)
        R.eq('Redis reserve 后 ready=1', st['ready'], 1)

        q.ack(r)
        R.eq('Redis ack 后 succeeded', q.get_status(r.envelope.id)['status'], 'succeeded')
        st = q.stats()
        R.eq('Redis ack 后 pending=0', st['pending'], 0)
        R.eq('Redis ack 后 ready=1', st['ready'], 1)
        r2 = q.reserve(timeout=1)
        R.check('Redis 第二个任务可取', r2 is not None)
        if r2:
            q.ack(r2)
        R.eq('Redis 排空后 ready=0', q.stats()['ready'], 0)

        # 延迟任务（_promote_delayed 每秒一次；用 2s 延迟避免远程 Redis 往返把任务拖到到期）
        tid = q.enqueue('e2e.echo', args=['delayed'], delay=2.0)
        R.eq('Redis 延迟任务 delayed=1', q.stats()['delayed'], 1)
        R.eq('Redis 延迟未到期 reserve None', q.reserve(timeout=0.2), None)
        time.sleep(2.6)
        r = q.reserve(timeout=1.5)
        R.check('Redis 延迟到期后可取', r is not None and r.envelope.id == tid)
        if r:
            q.ack(r)

        # 重试
        tid = q.enqueue('e2e.echo', args=['retry'])
        r = q.reserve(timeout=1)
        q.retry(r, delay=0.1, error='planned')
        st = q.get_status(tid)
        R.eq('Redis retry 后 retrying', st['status'], 'retrying')
        R.eq('Redis retry attempts=1', st['attempts'], 1)
        time.sleep(1.2)
        r = q.reserve(timeout=1.5)
        R.check('Redis 重试到期后重新取到', r is not None and r.envelope.id == tid)
        if r:
            q.ack(r)

        # 死信
        tid = q.enqueue('e2e.always_fail', args=['x'])
        r = q.reserve(timeout=1)
        q.dead(r, 'dead-error')
        R.eq('Redis dead 状态', q.get_status(tid)['status'], 'dead')
        letters = q.dead_letters(limit=5)
        R.check('Redis 死信可读', any(x.get('id') == tid for x in letters), letters)
        letter = next(x for x in letters if x.get('id') == tid)
        R.eq('Redis 死信记录保留 args', letter.get('args'), ['x'])
        R.eq('Redis 死信记录保留 kwargs', letter.get('kwargs'), {})
        R.eq('Redis 死信 last_error', letter['last_error'], 'dead-error')

        # 状态 TTL
        from flask_server.config import config
        old_ttl = config.task_status_ttl
        config.task_status_ttl = 1
        try:
            tid = q.enqueue('e2e.echo', args=['ttl'])
            R.check('TTL 任务状态存在', q.get_status(tid)['status'] == 'queued')
            time.sleep(1.4)
            R.raises('状态 TTL 到期后 TaskNotFound', lambda: q.get_status(tid), TaskNotFound)
        finally:
            config.task_status_ttl = old_ttl
    finally:
        q.close()
        _cleanup_stream(stream)


def _cleanup_stream(stream):
    if not URL:
        return
    import redis
    c = redis.Redis.from_url(URL, decode_responses=True)
    try:
        keys = list(c.scan_iter(match=f'{stream}*', count=200))
        if keys:
            c.delete(*keys)
    finally:
        c.close()


def test_redis_reclaim():
    if not URL:
        print('  SKIP  Redis 未配置，跳过 XAUTOCLAIM 测试')
        return
    from flask_server.config import config
    from flask_server.module.task_queue import RedisTaskQueue

    stream = f'e2e:reclaim:{uuid.uuid4().hex[:8]}'
    group = f'e2e-rg-{uuid.uuid4().hex[:6]}'
    old_vis = config.task_visibility_timeout
    old_backoff = config.task_retry_backoff
    config.task_visibility_timeout = 1
    config.task_retry_backoff = 1
    q1 = RedisTaskQueue(URL, stream=stream, group=group)
    q2 = RedisTaskQueue(URL, stream=stream, group=group)
    try:
        tid = q1.enqueue('e2e.echo', args=['stale'])
        r = q1.reserve(timeout=1)
        R.check('接管测试：先被 consumer1 取走', r is not None and r.envelope.id == tid)
        time.sleep(1.3)
        n = q2.reclaim_stale(count=10)
        R.eq('XAUTOCLAIM 接管陈旧消息', n, 1)
        R.eq('接管后状态 retrying', q2.get_status(tid)['status'], 'retrying')
        time.sleep(1.2)
        r = q2.reserve(timeout=1.5)
        R.check('接管后重新投递可消费', r is not None and r.envelope.id == tid)
        if r:
            q2.ack(r)
    finally:
        q1.close()
        q2.close()
        _cleanup_stream(stream)
        config.task_visibility_timeout = old_vis
        config.task_retry_backoff = old_backoff


def test_redis_init_failure():
    from flask_server.module.task_queue import RedisTaskQueue, TaskQueueUnavailable
    R.raises('Redis 不可用初始化抛 TaskQueueUnavailable',
             lambda: RedisTaskQueue('redis://127.0.0.1:6399/0', stream='e2e:no', group='g'),
             TaskQueueUnavailable)


# --------------------------------------------------------------------------
# worker CLI（真实进程）
# --------------------------------------------------------------------------

def test_worker_cli():
    if not URL:
        print('  SKIP  Redis 未配置，跳过 worker CLI 测试')
        return
    from flask_server.module.task_queue import RedisTaskQueue, TaskNotFound

    stream = f'e2e:cli:{uuid.uuid4().hex[:8]}'
    group = f'e2e-cg-{uuid.uuid4().hex[:6]}'
    out = TMP_DIR / f'cli_{uuid.uuid4().hex[:6]}.txt'
    env = base_env(
        REDIS_URL=URL, TASK_QUEUE_STREAM=stream, TASK_QUEUE_GROUP=group,
        TASK_RETRY_BACKOFF='1', TASK_MAX_RETRIES='1', TASK_RESERVE_TIMEOUT='0.3',
        E2E_TASK_OUTPUT=str(out),
    )
    q = RedisTaskQueue(URL, stream=stream, group=group)
    try:
        ids = {
            'echo': q.enqueue('e2e.echo', args=['cli']),
            'fail_once': q.enqueue('e2e.fail_once', args=['cli']),
            'always_fail': q.enqueue('e2e.always_fail', args=['cli']),
            'unknown': q.enqueue('e2e.nonexistent', args=['cli']),
        }
        cp = run_script(['scripts/worker.py', '--once', '--queue', stream, '--concurrency', '1'], env=env)
        R.check('worker --once 退出码 0', cp.returncode == 0, cp.stdout[-2000:] + cp.stderr[-2000:])
        R.contains('worker 启动打印已注册任务', cp.stdout, 'e2e.echo')
        R.eq('echo 任务成功', q.get_status(ids['echo'])['status'], 'succeeded')
        R.eq('fail_once 第一次失败进入 retrying', q.get_status(ids['fail_once'])['status'], 'retrying')
        R.eq('always_fail 第一次失败进入 retrying', q.get_status(ids['always_fail'])['status'], 'retrying')
        R.eq('未注册任务直接 dead', q.get_status(ids['unknown'])['status'], 'dead')
        R.check('echo 输出文件', out.exists() and 'echo:cli' in out.read_text(encoding='utf-8'))

        time.sleep(1.4)
        cp = run_script(['scripts/worker.py', '--once', '--queue', stream, '--concurrency', '1'], env=env)
        R.check('worker 第二轮退出码 0', cp.returncode == 0, cp.stdout[-2000:] + cp.stderr[-2000:])
        R.eq('fail_once 第二次成功', q.get_status(ids['fail_once'])['status'], 'succeeded')
        R.eq('always_fail 第二次进死信', q.get_status(ids['always_fail'])['status'], 'dead')
        text = out.read_text(encoding='utf-8') if out.exists() else ''
        R.contains('fail_once 输出', text, 'fail_once:cli:2')
        letters = q.dead_letters(limit=10)
        R.check('CLI 死信包含 always_fail',
                any(x.get('id') == ids['always_fail'] for x in letters), letters)
        R.check('CLI 死信包含未注册任务',
                any(x.get('id') == ids['unknown'] for x in letters), letters)

        # 空队列 --once
        cp = run_script(['scripts/worker.py', '--once', '--queue', stream, '--concurrency', '1'], env=env)
        R.eq('空队列 --once 退出码 0', cp.returncode, 0)
        R.contains('空队列处理 0 个任务', cp.stdout, '处理 0 个任务')
    finally:
        q.close()
        _cleanup_stream(stream)
        out.unlink(missing_ok=True)


def test_worker_cli_no_tasks():
    """没有注册任务时 worker 以退出码 1 结束（生产编排 profile 默认不启动的原因）。"""
    remove_probe_modules()
    try:
        cp = run_script(['scripts/worker.py', '--once'], env=base_env())
        R.check('无任务时 worker 退出码 1', cp.returncode == 1, cp.stdout[-1000:] + cp.stderr[-1000:])
        R.contains('无任务提示', cp.stdout, '没有可用任务')
    finally:
        install_probe_modules()


# --------------------------------------------------------------------------
# 内嵌 worker（Web 进程内）
# --------------------------------------------------------------------------

def test_embedded_worker():
    if not URL:
        print('  SKIP  Redis 未配置，跳过内嵌 worker 测试')
        return
    from flask_server.module.task_queue import RedisTaskQueue

    stream = f'e2e:embedded:{uuid.uuid4().hex[:8]}'
    group = f'e2e-eg-{uuid.uuid4().hex[:6]}'
    out = TMP_DIR / f'embedded_{uuid.uuid4().hex[:6]}.txt'
    port = free_port()
    env = base_env(
        SERVER_PORT=port, REDIS_URL=URL, TASK_QUEUE_STREAM=stream, TASK_QUEUE_GROUP=group,
        TASK_WORKER_EMBEDDED='true', TASK_WORKER_CONCURRENCY='2',
        TASK_RETRY_BACKOFF='1', TASK_MAX_RETRIES='1', TASK_RESERVE_TIMEOUT='0.3',
        E2E_TASK_OUTPUT=str(out), AUTH_ENABLED='false',
    )
    q = RedisTaskQueue(URL, stream=stream, group=group)
    try:
        with Server('embedded-worker', port, env) as srv:
            # 等待内嵌 worker 启动（日志出现 TaskWorker started）
            R.check('内嵌 worker 启动日志',
                    _wait_until(lambda: 'TaskWorker started' in srv.logs(), 10), srv.logs()[-1500:])
            tid = q.enqueue('e2e.echo', args=['embedded'])
            R.check('内嵌 worker 消费任务', _wait_until(
                lambda: q.get_status(tid)['status'] == 'succeeded', 10),
                q.get_status(tid) if _wait_until(lambda: False, 0.01) else '')
            text = out.read_text(encoding='utf-8') if out.exists() else ''
            R.contains('内嵌 worker 执行结果写入', text, 'echo:embedded')
            R.contains('内嵌 worker 完成日志', srv.logs(), 'TaskWorker done')

            tid2 = q.enqueue('e2e.sleep', kwargs={'seconds': 0.5, 'value': 'slow'})
            R.check('内嵌 worker 执行慢任务', _wait_until(
                lambda: q.get_status(tid2)['status'] == 'succeeded', 10))
    finally:
        q.close()
        _cleanup_stream(stream)
        out.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# 入队失败契约 / 自动发现
# --------------------------------------------------------------------------

def test_enqueue_unavailable_contract():
    code = (
        'from flask_server.module.task_queue import enqueue_task, TaskQueueUnavailable\n'
        'try:\n'
        "    enqueue_task('e2e.echo', 'x')\n"
        'except TaskQueueUnavailable as e:\n'
        "    print('CONTRACT_OK', type(e).__name__)\n"
        'else:\n'
        "    print('CONTRACT_FAIL')\n"
    )
    env = base_env(REDIS_URL='redis://127.0.0.1:6399/0',
                   TASK_QUEUE_STREAM=f'e2e:no:{uuid.uuid4().hex[:6]}')
    cp = run_py(code, env=env)
    R.check('入队失败子进程退出码 0', cp.returncode == 0, cp.stdout + cp.stderr)
    R.contains('入队失败抛 TaskQueueUnavailable', cp.stdout, 'CONTRACT_OK')


def test_auto_import_tasks():
    from flask_server.task import auto_import_tasks
    from flask_server.module.task_queue import registry
    names = auto_import_tasks()
    for expected in ('e2e.echo', 'e2e.fail_once', 'e2e.always_fail', 'e2e.sleep'):
        R.check(f'自动发现任务 {expected}', expected in names, names)
    R.check('下划线开头模块不被导入', all(not n.startswith('_') for n in names), names)
    registry.clear()


def main():
    install_probe_modules()
    try:
        test_memory_queue_semantics()
        test_worker_memory()
        test_redis_queue_semantics()
        test_redis_reclaim()
        test_redis_init_failure()
        test_worker_cli()
        test_worker_cli_no_tasks()
        test_embedded_worker()
        test_enqueue_unavailable_contract()
        test_auto_import_tasks()
    except Exception as e:  # noqa: BLE001
        dump_exception('e2e_tasks', e)
        R.check('e2e_tasks 未发生未捕获异常', False, f'{type(e).__name__}: {e}')
    finally:
        remove_probe_modules()
        cleanup_redis()
    return 0 if R.summary() else 1


if __name__ == '__main__':
    raise SystemExit(main())
