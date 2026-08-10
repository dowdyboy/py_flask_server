from .sqlite import SQLite
from .simple_memory_cache import memory_cache
from .local_file_storage import local_file_storage
from .sqlalchemy import sqlalchemy_trans, init_SQLAlchemy, init_Migrate, sqlalchemy, get_migrate, in_app_context
from .redis_cache import redis_cache
from .tcp_server import tcp_server
from .udp_server import udp_server
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
    'start_protocol_servers', 'stop_protocol_servers',
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
