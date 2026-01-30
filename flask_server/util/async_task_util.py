from concurrent.futures import ThreadPoolExecutor
import asyncio
from ..config import config


# 异步任务工具类，用于执行异步任务


async def async_run_func(func, **kwargs):
    """
    异步执行指定函数
    
    Args:
        func (callable): 需要异步执行的函数对象
        **kwargs: 传递给func的关键字参数
    
    Note:
        此函数仅包装同步函数为异步调用，不会改变函数的执行方式
    """
    func(**kwargs)


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
            # print(f"{cmd} succeeded, {stdout.decode()}")
            if on_success is not None:
                on_success(extra_param, [stdout.decode()])
        else:
            # print(f"{cmd} failed with code {proc.returncode}")
            # print(stderr.decode())
            if on_error is not None:
                on_error(extra_param, [proc.returncode, stderr.decode()])
    except Exception as e:
        # print(f"{cmd} failed with try catch: {e}")
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


class AsyncTaskUtil:

    executor = ThreadPoolExecutor(config.thread_num)  # 设置线程数

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
            cmd (str): 要执行的命令字符串，会自动按空格分割并去除空项
            extra_param (Any, optional): 传递给任务的额外参数
            on_success (Callable, optional): 任务成功时的回调函数，签名为 (result, extra_param)
            on_error (Callable, optional): 任务失败时的回调函数，签名为 (exception, extra_param)
        """
        AsyncTaskUtil.submit_cmd_task(
            list(filter(
                lambda x: x != '',
                map(lambda x: x.strip(), str(cmd).split(' '))
            )),
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


# AsyncTaskUtil.submit_cmd_task_plain(
#     'node --version',
#     'hello,task', lambda a,b: print(a,b), lambda a,b: print(a,b))
