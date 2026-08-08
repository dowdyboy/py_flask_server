"""BoundedExecutor / SafeThread / 命令任务 / SubprocessTask 测试"""

import threading
import time

from flask_server.util.async_task_util import (
    BoundedExecutor, SafeThread, AsyncTaskUtil, SubprocessTask, SubprocessTaskInterface,
)


def test_submit_rejects_when_queue_full():
    """队列满时 submit 返回 None（拒绝），不阻塞调用方"""
    slow = lambda: time.sleep(0.3)   # noqa: E731
    ex = BoundedExecutor(max_workers=1, max_pending=2)
    try:
        # 占满 1 个 worker + 2 个排队位
        futures = [ex.submit(slow) for _ in range(3)]
        assert all(f is not None for f in futures)
        # 队列已满 → 拒绝
        assert ex.submit(slow) is None
    finally:
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
        slow = lambda: time.sleep(0.05)   # noqa: E731
        assert ex.submit(slow) is not None
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

    def ok():
        time.sleep(0.01)

    t = SafeThread(target=ok, on_crash=lambda st, e: fired.append(e))
    t.start()
    t.join(timeout=5)
    assert fired == []
    assert t.exception is None


class _BlockingWorker(SubprocessTaskInterface):
    """模拟未按协议处理哨兵的阻塞 worker（用于验证 stop 不挂死）"""

    def __init__(self):
        super().__init__()
        self.received = []

    def thread_func(self, in_queue, out_queue):
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
    time.sleep(0.1)   # 等待 thread 进入阻塞 get

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
    time.sleep(0.1)
    task.stop()
    assert task.subprocess_queue.get_nowait() is None


def test_watch_process_none_no_crash():
    """watch_process(None) 应直接返回不抛异常"""
    worker = _BlockingWorker()
    task = SubprocessTask(worker)
    task.watch_process(None, lambda *a: None)   # 不抛即为通过
