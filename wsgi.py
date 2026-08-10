import signal
from flask_server import app, config, socketio
from flask_server.module import start_protocol_servers, stop_protocol_servers
from flask_server.util.banner import print_startup_banner
from waitress import serve


# waitress 是纯 WSGI 服务器，不支持 WebSocket；启用 SocketIO 时给出明确警告
if socketio is not None:
    from flask_server.util import Logger
    Logger.warn('SOCKETIO_ENABLED=true but waitress does not support WebSocket. '
                'WebSocket 请求将失效，请改用 server.py（eventlet/threading 模式）。')


def _graceful_shutdown(signum, frame):
    """优雅关闭：停止协议服务器（先停消息入口）→ 清理 DB 连接池、Redis 连接、线程池"""
    from flask_server.util import Logger
    Logger.info('Graceful shutdown initiated (SIGTERM received)')

    # 先停止 TCP/UDP 协议服务器：处理中的 on_message 可能用到 DB/Redis，
    # 必须在关闭依赖之前停止消息入口，给在途消息一个完成窗口
    try:
        stop_protocol_servers()
        Logger.info('Protocol servers stopped')
    except Exception as e:
        Logger.warn(f'Protocol servers stop error: {e}')

    # 关闭 SQLAlchemy 连接池
    try:
        from flask_server.module import sqlalchemy
        db = sqlalchemy()
        if db is not None:
            db.engine.dispose()
            Logger.info('SQLAlchemy engine disposed')
    except Exception as e:
        Logger.warn(f'SQLAlchemy dispose error: {e}')

    # 关闭 Redis 连接
    try:
        from flask_server.module import redis_cache
        if redis_cache is not None:
            redis_cache.client.close()
            Logger.info('Redis connection closed')
    except Exception as e:
        Logger.warn(f'Redis close error: {e}')

    # 关闭线程池
    try:
        from flask_server.util.async_task_util import AsyncTaskUtil
        AsyncTaskUtil.executor.shutdown(wait=False)
        Logger.info('AsyncTask executor shutdown')
    except Exception as e:
        Logger.warn(f'AsyncTask shutdown error: {e}')

    Logger.info('Graceful shutdown complete')
    # sys.exit 会抛 SystemExit，在信号处理器中可能被吞导致进程不退；os._exit 确保立即退出
    import os
    os._exit(0)


# 注册 SIGTERM 信号处理（Docker stop / K8s pod 终止时触发）
signal.signal(signal.SIGTERM, _graceful_shutdown)
# 注册 SIGINT（Windows 下 Ctrl+C 触发；SIGTERM 在 Windows 不会产生）
signal.signal(signal.SIGINT, _graceful_shutdown)


if __name__ == '__main__':
    print_startup_banner()
    # 启动 TCP/UDP 协议服务器（TCP_ENABLED / UDP_ENABLED 为 true 时生效）
    start_protocol_servers()
    serve(
        app,
        host=config.host,
        port=config.port,
        threads=config.thread_num,
    )
