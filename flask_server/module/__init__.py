from .sqlite import SQLite
from .simple_memory_cache import memory_cache
from .local_file_storage import local_file_storage
from .sqlalchemy import sqlalchemy_trans, init_SQLAlchemy, init_Migrate, sqlalchemy, get_migrate, in_app_context
from .redis_cache import redis_cache
from .tcp_server import tcp_server
from .udp_server import udp_server
from .task_queue import (
    TaskQueueUnavailable, TaskNotFound, enqueue_task, get_task_queue,
    get_task_status, registry as task_registry, task_queue_stats,
)
from .task_worker import TaskWorker, start_worker
from ..config import config

# 导出所有的功能模块
# 可使用已经有的模块，也可以自己写新的模块

__all__ = [
    'SQLite',
    'memory_cache',
    'local_file_storage',
    'sqlalchemy', 'sqlalchemy_trans', 'init_SQLAlchemy', 'init_Migrate', 'get_migrate', 'in_app_context',
    'redis_cache',
    'tcp_server', 'udp_server',
    'start_protocol_servers', 'stop_protocol_servers', 'start_embedded_task_worker',
    # 任务声明装饰器刻意不从这里导出：`task` 与子包 `flask_server.task` 同名，
    # 经 `from .module import *` 会在包属性上互相覆盖。规范写法：
    #   from flask_server.module.task_queue import task
    'task_registry', 'enqueue_task', 'get_task_status', 'get_task_queue',
    'task_queue_stats', 'TaskQueueUnavailable', 'TaskNotFound',
    'TaskWorker', 'start_worker',
]


def start_protocol_servers():
    """启动配置启用的协议服务器（TCP/UDP），返回已启动的服务器名称列表。

    由入口（server.py / wsgi.py / wsgi_gunicorn.py）调用；
    处理器在 flask_server/handler/ 下注册（自动导入），无 on_message 处理器时告警不启动。
    """
    from ..util import Logger
    started = []
    try:
        if config.tcp_enabled and tcp_server.start():
            started.append('tcp')
        if config.udp_enabled and udp_server.start():
            started.append('udp')
    except BaseException:
        # 部分成功不做回滚时，TCP 已在监听而 UDP bind 失败会把异常抛给入口，
        # 进程带着一个"半启动"的协议服务器退出。先回滚已启动的再上抛。
        for name in started:
            (tcp_server if name == 'tcp' else udp_server).stop()
        Logger.error(f'protocol servers start failed, rolled back: {", ".join(started) or "none"}')
        raise
    if started:
        Logger.info(f'protocol servers started: {", ".join(started)}')
    return started


def stop_protocol_servers():
    """停止所有协议服务器（幂等；入口优雅关闭时调用，atexit 亦有兜底）"""
    tcp_server.stop()
    udp_server.stop()


def start_embedded_task_worker(app=None):
    """按 TASK_WORKER_EMBEDDED 在 Web 进程内启动任务消费者（开发/单进程便捷模式）。

    生产建议用独立进程 `python scripts/worker.py`：不占 Web 线程、可独立扩容重启。
    Returns:
        TaskWorker | None
    """
    from ..util import Logger
    if not config.task_worker_embedded:
        return None
    from ..task import auto_import_tasks
    from .task_worker import TaskWorker

    names = auto_import_tasks()
    if not names:
        Logger.warn('TASK_WORKER_EMBEDDED=true 但未注册任何任务（flask_server/task/）')
        return None
    if not config.redis_url:
        Logger.warn('TASK_WORKER_EMBEDDED=true 且未配置 REDIS_URL：使用进程内内存队列，'
                    '只有本进程入队的任务会被消费（多 worker 部署请改用 scripts/worker.py + Redis）')
    # 任务子系统的可用性问题不能拖死 Web 进程：Redis 配置了但启动瞬间不可达时，
    # get_task_queue() → RedisTaskQueue.__init__ 会抛 TaskQueueUnavailable。
    # 修复前该异常从 server.py / wsgi.py 的入口直接逃逸 → 进程退出 → 容器重启循环。
    # 这里降级为"不启动内嵌 worker 并明确告警"，HTTP 服务照常可用（与缓存降级策略一致）。
    from .task_queue import TaskQueueUnavailable
    try:
        return TaskWorker(app=app).start()
    except TaskQueueUnavailable as e:
        Logger.error('内嵌任务 worker 未启动（任务队列不可用），HTTP 服务继续运行：'
                     f'{e}')
        return None
