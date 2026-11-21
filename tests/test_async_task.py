"""BoundedExecutor / SafeThread / 命令任务 / SubprocessTask 测试"""

import threading
import time

from flask_server.util.async_task_util import (
    BoundedExecutor, SafeThread, AsyncTaskUtil, SubprocessTask, SubprocessTaskInterface,
)


def test_submit_rejects_when_queue_full():
    """队列满时 submit 返回 None（拒绝），不阻塞调用方"""
    release = threading.Event()
    slow = lambda: release.wait(5)   # noqa: E731  占住 worker/排队位，直到用例放行

    ex = BoundedExecutor(max_workers=1, max_pending=2)
    try:
        # 占满 1 个 worker + 2 个排队位
        futures = [ex.submit(slow) for _ in range(3)]
        assert all(f is not None for f in futures)
        # 队列已满 → 拒绝
        assert ex.submit(slow) is None
    finally:
        release.set()
        ex.shutdown(wait=True)


def test_submit_rejects_when_executor_rejected():
    """底层 executor 失败时信号量正确释放（不泄漏占用位），后续可继续提交"""
    import pytest
    ex = BoundedExecutor(max_workers=1, max_pending=1)

    def boom():
        raise RuntimeError('mock worker failure')

    try:
        f = ex.submit(boom)
        assert f is not None
        # future 捕获 worker 内异常，result() 时重新抛出
        with pytest.raises(RuntimeError):
            f.result()
        # 信号量已释放：可再次提交新任务（容量 1 worker + 1 pending）
        again = ex.submit(lambda: 'ok')
        assert again is not None
        assert again.result(timeout=5) == 'ok'
    finally:
        ex.shutdown(wait=True)


def test_workers_and_queue_capacity():
    """容量 = 最大排队数（不含运行中的 worker）"""
    ex = BoundedExecutor(max_workers=2, max_pending=5)
    assert ex._max_pending == 5
    ex.shutdown()


def test_shlex_command_split(monkeypatch):
    """submit_cmd_task_plain 应使用 shlex 智能拆分（带空格引号参数不被拆开）"""
    captured = {}

    def fake_submit(fn, *args, **kwargs):
        captured['cmd'] = args[0]
        return None

    monkeypatch.setattr(AsyncTaskUtil.executor, 'submit', fake_submit)
    AsyncTaskUtil.submit_cmd_task_plain('echo "hello world" --flag=1')
    assert captured['cmd'] == ['echo', 'hello world', '--flag=1']


def test_cmd_task_success_callback():
    """命令成功时触发 on_success 回调"""
    import sys
    import queue

    q = queue.Queue()

    def on_success(param, result):
        q.put(('ok', param, result[0]))

    done = threading.Event()

    def wrapper(param, result):
        on_success(param, result)
        done.set()

    AsyncTaskUtil.submit_cmd_task_plain(
        f'{sys.executable} -c "print(42)"',
        extra_param='p', on_success=wrapper, on_error=None)
    assert done.wait(timeout=10), 'command task timeout'
    kind, param, stdout = q.get_nowait()
    assert kind == 'ok'
    assert param == 'p'
    assert '42' in stdout


def test_cmd_task_error_callback():
    """命令失败时触发 on_error 回调"""
    import sys
    import queue

    q = queue.Queue()

    def on_error(param, result):
        q.put(('err', param, result[0]))

    done = threading.Event()

    def wrapper(param, result):
        on_error(param, result)
        done.set()

    AsyncTaskUtil.submit_cmd_task_plain(
        f'{sys.executable} -c "import sys; sys.exit(3)"',
        extra_param='p', on_success=None, on_error=wrapper)
    assert done.wait(timeout=10), 'command task timeout'
    kind, param, code = q.get_nowait()
    assert kind == 'err'
    assert param == 'p'
    assert code == 3


def test_cmd_task_timeout_kills_process(monkeypatch):
    """命令超时（timeout 参数）应终止子进程并触发 on_error 回调"""
    import asyncio
    import sys
    import queue
    from flask_server.util.async_task_util import async_run_command

    q = queue.Queue()
    spawned = {}

    real_exec = asyncio.create_subprocess_exec

    async def recording_exec(*args, **kwargs):
        proc = await real_exec(*args, **kwargs)
        spawned['proc'] = proc
        return proc

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', recording_exec)

    def on_error(param, result):
        q.put(('err', param, result[0], result[1]))

    asyncio.run(async_run_command(
        [sys.executable, '-c', 'import time; time.sleep(30)'],
        'p', on_success=None, on_error=on_error, timeout=1,
    ))
    kind, param, reason, detail = q.get_nowait()
    assert kind == 'err'
    assert param == 'p'
    assert reason == 'timeout'
    assert 'timed out after' in detail

    # 必须真的 kill 并回收子进程，而不是只回调 on_error 后让它继续跑
    proc = spawned['proc']
    assert proc.returncode is not None, '超时后子进程未被 kill/回收（仍在后台运行）'


def test_safe_thread_on_crash():
    """SafeThread：目标抛异常时 on_crash 回调收到异常，且 exception 属性被设置"""
    fired = {}

    def boom():
        raise RuntimeError('thread crash')

    def on_crash(thread, exc):
        fired['exc'] = exc

    t = SafeThread(target=boom, on_crash=on_crash)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive()
    assert isinstance(fired.get('exc'), RuntimeError)
    assert isinstance(t.exception, RuntimeError)


def test_safe_thread_clean():
    """SafeThread：正常线程不触发 on_crash"""
    fired = []
    ran = threading.Event()

    def ok():
        ran.set()

    t = SafeThread(target=ok, on_crash=lambda st, e: fired.append(e))
    t.start()
    t.join(timeout=5)
    assert ran.is_set(), '线程目标函数未执行'
    assert fired == []
    assert t.exception is None


class _BlockingWorker(SubprocessTaskInterface):
    """模拟未按协议处理哨兵的阻塞 worker（用于验证 stop 不挂死）"""

    def __init__(self):
        super().__init__()
        self.received = []
        self.thread_entered = threading.Event()   # 线程已进入 thread_func（开始阻塞 get）

    def thread_func(self, in_queue, out_queue):
        self.thread_entered.set()
        while True:
            data = in_queue.get()
            self.received.append(data)
            if data is None:
                return

    def subprocess_func(self, in_queue, out_queue):
        while True:
            data = in_queue.get()
            if data is None:
                return


class _FakeProcess:
    """替换 multiprocessing.Process：不真正启动子进程（Windows spawn 无法在 pytest 下使用）"""

    def __init__(self, target=None, args=()):
        self.target = target
        self.args = args

    def start(self):
        pass

    def is_alive(self):
        return False

    def terminate(self):
        pass

    def join(self, timeout=None):
        pass

    @property
    def exitcode(self):
        return 0


def test_subprocess_task_stop_not_hang(monkeypatch):
    """stop() 应通过哨兵解除 get() 阻塞并超时返回，不永久挂死"""
    monkeypatch.setattr('flask_server.util.async_task_util.multiprocessing.Process', _FakeProcess)
    worker = _BlockingWorker()
    task = SubprocessTask(worker)
    task.start()
    assert worker.thread_entered.wait(5), '消费线程未进入阻塞 get'

    start = time.time()
    task.stop()
    elapsed = time.time() - start

    # thread 收到哨兵后应退出（stop 内部 join 成功，不会等满超时）
    assert elapsed < 5
    assert not task.thread_handler.is_alive()
    assert worker.received[-1] is None   # 哨兵已送达


def test_subprocess_task_sentinel_to_subprocess_queue(monkeypatch):
    """stop() 应向 subprocess_queue 也推送哨兵（子进程可优雅退出）"""
    monkeypatch.setattr('flask_server.util.async_task_util.multiprocessing.Process', _FakeProcess)
    worker = _BlockingWorker()
    task = SubprocessTask(worker)
    task.start()
    assert worker.thread_entered.wait(5), '消费线程未进入阻塞 get'
    task.stop()
    # multiprocessing.Queue 的 put 由后台 feeder 线程异步刷新到管道：
    # 用带超时的阻塞 get 等待哨兵，而不是固定 sleep 轮询
    sentinel = task.subprocess_queue.get(timeout=5)
    assert sentinel is None


def test_watch_process_none_no_crash():
    """watch_process(None) 应直接返回、不抛异常、不触发回调"""
    worker = _BlockingWorker()
    task = SubprocessTask(worker)
    callbacks = []

    assert task.watch_process(None, lambda *args: callbacks.append(args)) is None
    assert callbacks == []


# ------------------------- 0.8：失败路径 / 回调契约 -------------------------


def test_async_run_func_and_do_run_func_execute():
    """do_run_func/async_run_func 都在独立线程执行普通可调用对象"""
    import asyncio

    from flask_server.util.async_task_util import async_run_func, do_run_func

    out = []

    def record(**kwargs):
        out.append(kwargs['x'])

    asyncio.run(async_run_func(record, x=1))
    do_run_func(record, x=2)
    assert out == [1, 2]


def test_async_run_func_propagates_exception():
    import asyncio

    import pytest as _pytest

    from flask_server.util.async_task_util import async_run_func

    def boom(**kwargs):
        raise ValueError('boom')

    with _pytest.raises(ValueError):
        asyncio.run(async_run_func(boom))


def test_async_run_command_start_failure_calls_on_error():
    """命令根本起不来（文件不存在）→ on_error 收到异常对象，不裸抛"""
    import asyncio

    from flask_server.util.async_task_util import async_run_command

    errors = []
    asyncio.run(async_run_command(
        ['definitely-not-a-real-command-xyz'], 'p',
        on_success=None, on_error=lambda param, result: errors.append((param, result)),
    ))
    assert errors and errors[0][0] == 'p'
    assert isinstance(errors[0][1][0], FileNotFoundError)


def test_async_run_command_none_callbacks_are_safe():
    """on_success/on_error 为 None 时不抛异常"""
    import asyncio
    import sys

    from flask_server.util.async_task_util import async_run_command

    asyncio.run(async_run_command([sys.executable, '-c', 'print(1)'], None, None, None))


def test_submit_cmd_task_and_func_task_forward_to_executor(monkeypatch):
    """两个静态入口都走 AsyncTaskUtil.executor.submit（便于统一限流/替换）"""
    captured = []

    def fake_submit(fn, *args, **kwargs):
        captured.append((fn.__name__, args, kwargs))
        return 'future'

    monkeypatch.setattr(AsyncTaskUtil.executor, 'submit', fake_submit)
    assert AsyncTaskUtil.submit_cmd_task(['echo', 'x'], extra_param='p') == 'future'
    assert AsyncTaskUtil.submit_func_task(lambda: None, a=1) == 'future'
    assert captured[0][0] == 'do_run_cmd'
    assert captured[1][0] == 'do_run_func'
    assert captured[1][2] == {'a': 1}


def test_bounded_executor_submit_failure_releases_semaphore():
    """底层 executor.submit 抛异常时必须释放信号量（否则容量永久泄漏）"""
    import pytest as _pytest

    class _BoomExecutor:
        def submit(self, *args, **kwargs):
            raise RuntimeError('executor closed')

    ex = BoundedExecutor(max_workers=1, max_pending=1)
    ex._executor = _BoomExecutor()
    with _pytest.raises(RuntimeError):
        ex.submit(lambda: None)
    assert ex._semaphore.acquire(blocking=False) is True
    ex._semaphore.release()


def test_subprocess_task_interface_error_handlers_stop_task():
    """默认错误回调应记录日志并停止 task（避免线程/子进程继续跑）"""

    class _Task:
        def __init__(self):
            self.stopped = 0

        def stop(self):
            self.stopped += 1

    worker = SubprocessTaskInterface()
    task = _Task()
    worker.on_thread_func_error(RuntimeError('x'), None, task)
    worker.on_subprocess_func_error(RuntimeError('y'), None, task)
    assert task.stopped == 2


def test_subprocess_task_start_failure_raises_with_hint(monkeypatch):
    """start 失败（线程创建异常）应记录提示并上抛，而不是留下半个任务"""
    import pytest as _pytest

    def _boom(*args, **kwargs):
        raise RuntimeError('cannot start thread')

    monkeypatch.setattr('flask_server.util.async_task_util.SafeThread', _boom)
    task = SubprocessTask(_BlockingWorker())
    with _pytest.raises(RuntimeError, match='cannot start thread'):
        task.start()


def test_subprocess_task_stop_survives_queue_and_handler_errors(monkeypatch):
    """stop() 中 put/join/terminate 失败只记录日志，不能把关闭流程打断"""

    class _BadQueue:
        def put(self, item):
            raise RuntimeError('queue closed')

    class _BadThread:
        def is_alive(self):
            return True

        def join(self, timeout=None):
            raise RuntimeError('join failed')

    class _BadProcess:
        def is_alive(self):
            return True

        def terminate(self):
            raise RuntimeError('terminate failed')

        def join(self, timeout=None):
            raise RuntimeError('join failed')

    task = SubprocessTask(_BlockingWorker())
    task.thread_queue = _BadQueue()
    task.subprocess_queue = _BadQueue()
    task.thread_handler = _BadThread()
    task.subprocess_handler = _BadProcess()
    task.stop()   # 不应抛异常


def test_watch_process_nonzero_exit_calls_callback(monkeypatch):
    """子进程非 0 退出 → 触发 on_subprocess_func_error(exitcode, p, task)"""
    import flask_server.util.async_task_util as mod

    monkeypatch.setattr(mod.time, 'sleep', lambda _: None)

    class _P:
        exitcode = 2

        def __init__(self):
            self.calls = 0

        def is_alive(self):
            self.calls += 1
            return self.calls == 1   # 第一次存活，第二次退出

    task = SubprocessTask(_BlockingWorker())
    seen = []
    task.watch_process(_P(), lambda *args: seen.append(args))
    assert seen and seen[0][0] == 2
    assert seen[0][2] is task
