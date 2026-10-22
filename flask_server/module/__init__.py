from .sqlite import SQLite
from .simple_memory_cache import memory_cache
from .local_file_storage import local_file_storage
from .sqlalchemy import sqlalchemy_trans, init_SQLAlchemy, init_Migrate, sqlalchemy, get_migrate, in_app_context
from .redis_cache import redis_cache
from .tcp_server import tcp_server
from .udp_server import udp_server
from .task_queue import (
    TaskQueueUnavailable, TaskNotFound, enqueue_task, get_task_queue,
    get_task_status, registry as task_registry, task, task_queue_stats,
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
    'task', 'task_registry', 'enqueue_task', 'get_task_status', 'get_task_queue',
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
    if config.tcp_enabled and tcp_server.start():
        started.append('tcp')
    if config.udp_enabled and udp_server.start():
        started.append('udp')
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
    return TaskWorker(app=app).start()
