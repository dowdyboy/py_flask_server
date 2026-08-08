from concurrent.futures import ThreadPoolExecutor
import asyncio
import multiprocessing
import threading
import traceback
import time
import atexit
import shlex
import os

from ..config import config
from .logger import Logger


# 异步任务工具类，用于执行异步任务


async def async_run_func(func, **kwargs):
    """
    异步执行指定函数（在独立线程中真正异步执行）

    注意：若在 eventlet 模式下使用，asyncio 与 eventlet 事件循环可能冲突，
    建议在 threading 模式下使用。

    Args:
        func (callable): 需要异步执行的函数对象
        **kwargs: 传递给func的关键字参数
    """
    await asyncio.to_thread(func, **kwargs)


def do_run_func(func, **kwargs):
    """
    同步执行异步函数

    Args:
        func (coroutine): 需要执行的异步函数
        **kwargs: 传递给异步函数的参数

    Raises:
        RuntimeError: 如果异步函数执行失败
    """
    asyncio.run(async_run_func(func, **kwargs))


async def async_run_command(cmd, extra_param, on_success, on_error):
    """
    异步执行系统命令并处理结果

    Args:
        cmd: 要执行的命令列表，例如 ['ls', '-l']
        extra_param: 传递给回调函数的额外参数
        on_success: 成功回调函数，接收参数 (extra_param, [stdout])
        on_error: 错误回调函数，接收参数 (extra_param, [returncode, stderr] 或 [exception])

    Raises:
        不会直接抛出异常，所有异常将通过on_error回调处理
    """
    try:
        proc = await asyncio.create_subprocess_exec(*cmd,
                                                    stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            if on_success is not None:
                on_success(extra_param, [stdout.decode()])
        else:
            if on_error is not None:
                on_error(extra_param, [proc.returncode, stderr.decode()])
    except Exception as e:
        if on_error is not None:
            on_error(extra_param, [e])


def do_run_cmd(cmd, extra_param, on_success, on_error):
    """
    异步执行系统命令的包装函数

    Args:
        cmd: 要执行的命令列表，例如 ['python', 'script.py']
        extra_param: 传递给回调函数的额外参数
        on_success: 成功回调函数，接收参数 (extra_param, stdout)
        on_error: 错误回调函数，接收参数 (extra_param, returncode, stderr)

    Note:
        此函数是async_run_command的同步包装器，使用asyncio.run来运行异步函数
    """
    asyncio.run(async_run_command(cmd, extra_param, on_success, on_error))


class BoundedExecutor:
    """有界线程池执行器：限制排队任务数，防止无界队列导致内存膨胀。

    达到上限时拒绝新任务（返回 None 并告警），调用方可根据返回值决定降级策略。
    """

    def __init__(self, max_workers, max_pending):
        self._max_pending = max_pending
        self._semaphore = threading.Semaphore(max_workers + max_pending)
        self._executor = ThreadPoolExecutor(max_workers)

    def submit(self, fn, *args, **kwargs):
        if not self._semaphore.acquire(blocking=False):
            Logger.warn(f'AsyncTask queue full (limit={self._max_pending}), task rejected')
            return None
        try:
            future = self._executor.submit(fn, *args, **kwargs)
        except Exception:
            self._semaphore.release()
            raise
        future.add_done_callback(lambda f: self._semaphore.release())
        return future

    def shutdown(self, wait=False):
        self._executor.shutdown(wait=wait)


class AsyncTaskUtil:

    executor = BoundedExecutor(config.thread_num, config.async_task_queue_max)  # 有界线程池

    @staticmethod
    def submit_cmd_task(cmd_arr, extra_param=None, on_success=None, on_error=None):
        """
        提交命令行任务到异步线程池执行

        Args:
            cmd_arr (list): 要执行的命令及其参数列表
            extra_param (Any, optional): 传递给回调函数的额外参数
            on_success (Callable, optional): 任务成功时的回调函数，格式为 (extra_param, stdout)
            on_error (Callable, optional): 任务失败时的回调函数，格式为 (extra_param, returncode, stderr)
        """
        AsyncTaskUtil.executor.submit(
            do_run_cmd, cmd_arr, extra_param, on_success, on_error
        )

    @staticmethod
    def submit_cmd_task_plain(cmd, extra_param=None, on_success=None, on_error=None):
        """
        提交命令行任务到异步任务队列（简化版）

        Args:
            cmd (str): 要执行的命令字符串，使用 shlex 智能分割（支持带空格的引号参数）
            extra_param (Any, optional): 传递给任务的额外参数
            on_success (Callable, optional): 任务成功时的回调函数
            on_error (Callable, optional): 任务失败时的回调函数

        Note:
            Windows 下使用 posix=False 拆分（保留反斜杠路径），并剥离两端引号；
            含复杂转义的命令建议改用 submit_cmd_task 直接传 list
        """
        if os.name == 'nt':
            parts = [p.strip('"') for p in shlex.split(cmd, posix=False)]
        else:
            parts = shlex.split(cmd)
        AsyncTaskUtil.submit_cmd_task(
            parts,
            extra_param,
            on_success,
            on_error
        )

    @staticmethod
    def submit_func_task(func, **kwargs):
        """
        异步提交函数任务到线程池执行

        Args:
            func: 要执行的函数对象
            **kwargs: 传递给函数的命名参数
        """
        AsyncTaskUtil.executor.submit(
            do_run_func, func, **kwargs
        )


# 进程退出时关闭线程池（wait=False 不等待任务完成直接关闭）
atexit.register(lambda: AsyncTaskUtil.executor.shutdown(wait=False))


class SafeThread(threading.Thread):
    def __init__(self, *args, on_crash=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._on_crash = on_crash
        self.exception = None  # 保存异常供外部查询

    def run(self):
        try:
            super().run()
        except Exception as e:
            self.exception = e
            traceback.print_exc()
            if self._on_crash:
                self._on_crash(self, e)  # 触发回调


class SubprocessTaskInterface:

    """子进程任务接口（thread + subprocess 双向管道协作）

    停止协议（务必遵守，否则 stop() 无法优雅退出）：
      - 本接口约定 **None 为哨兵**：stop() 会向两个队列各推送一个 None。
      - thread_func 应循环 `while True: data = in_queue.get()`，
        收到 None 时退出循环并结束。
      - subprocess_func 同样从 in_queue 消费，收到 None 退出。
      - is_stop 标志仅对当前进程生效（子进程持有的是 pickle 副本），
        子进程侧应依赖哨兵而非 is_stop 退出。
    """

    def __init__(self):
        self.is_stop = False

    def thread_func(self, in_queue, out_queue):
        raise NotImplementedError

    def subprocess_func(self, in_queue, out_queue):
        raise NotImplementedError

    def on_thread_func_error(self, e, handler, task):
        Logger.error(f'SubprocessTask thread_func error: {e}', exc_info=True)
        task.stop()

    def on_subprocess_func_error(self, e, handler, task):
        Logger.error(f'SubprocessTask subprocess_func error: {e}', exc_info=True)
        task.stop()

class SubprocessTask:

    def __init__(self, instance):
        self.instance = instance
        self.thread_queue = multiprocessing.Queue()
        self.subprocess_queue = multiprocessing.Queue()
        self.thread_handler = None
        self.subprocess_handler = None
        self.subprocess_watcher = None

    def start(self):
        # 注意这里如果异常，则self.stop是在子线程中调用
        try:
            self.thread_handler = SafeThread(
                target=self.instance.thread_func,
                args=(self.thread_queue, self.subprocess_queue),
                on_crash=lambda st, e: self.instance.on_thread_func_error(e, st, self))
            # self.thread_handler = Thread(target=self.instance.thread_func, args=(self.thread_queue, self.subprocess_queue), )
            self.thread_handler.start()

            self.subprocess_handler = multiprocessing.Process(target=self.instance.subprocess_func, args=(self.subprocess_queue, self.thread_queue))
            self.subprocess_handler.start()

            self.subprocess_watcher = SafeThread(target=self.watch_process, args=(self.subprocess_handler, self.instance.on_subprocess_func_error))
            self.subprocess_watcher.daemon = True
            self.subprocess_watcher.start()
        except Exception as e:
            # Windows 下 multiprocessing 使用 spawn 模式，必须在脚本入口提供
            # if __name__ == '__main__' 保护，否则会递归启动导致异常。
            # 捕获后给出可操作的错误提示，避免堆栈不可读。
            Logger.error(
                "SubprocessTask start failed: %s. On Windows, make sure the code "
                "using SubprocessTask is guarded by `if __name__ == '__main__'`." % e
            )
            raise

    def stop(self):
        self.instance.is_stop = True
        # 推送哨兵解除阻塞在 queue.get() 的线程/子进程（接口约定 None 为停止信号）
        try:
            self.thread_queue.put(None)
        except Exception as e:
            Logger.error(f'SubprocessTask Stop Sentry Thread Error: {e}')
        try:
            self.subprocess_queue.put(None)
        except Exception as e:
            Logger.error(f'SubprocessTask Stop Sentry Subprocess Error: {e}')
        try:
            if self.thread_handler is not None and self.thread_handler.is_alive():
                if threading.current_thread() is not self.thread_handler:
                    # 超时 join：即使 thread_func 未按协议退出也不会永久挂死
                    self.thread_handler.join(timeout=5)
        except Exception as e:
            Logger.error(f'SubprocessTask Stop Thread Error: {e}')
        try:
            if self.subprocess_handler is not None and self.subprocess_handler.is_alive():
                self.subprocess_handler.terminate()
                self.subprocess_handler.join(timeout=5)
        except Exception as e:
            Logger.error(f'SubprocessTask Stop Subprocess Error: {e}')

    def watch_process(self, p, callback):
        """独立监控线程：子进程退出且返回码非 0 时触发错误回调"""
        if p is None:
            return
        while p.is_alive():
            time.sleep(1)
        if p.exitcode != 0:
            callback(p.exitcode, p, self)

