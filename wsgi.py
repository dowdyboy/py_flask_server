import os
import signal
import threading

from flask_server import app, config, socketio
from flask_server.module import (
    start_embedded_task_worker, start_protocol_servers, stop_protocol_servers,
)
from flask_server.util import Logger
from flask_server.util.banner import print_startup_banner
from waitress.server import create_server


# waitress 是纯 WSGI 服务器，不支持 WebSocket；启用 SocketIO 时给出明确警告
if socketio is not None:
    Logger.warn('SOCKETIO_ENABLED=true but waitress does not support WebSocket. '
                'WebSocket 请求将失效，请改用 server.py（eventlet/threading 模式）。')

# 优雅关闭超时（秒）：停止接收新连接后，等待在途请求完成的最长时间。
# 超时后强制退出，避免 keep-alive 空闲连接或卡死请求把进程永久挂住。
GRACEFUL_TIMEOUT = 15


# 内嵌任务消费者（TASK_WORKER_EMBEDDED=true 时由 main() 赋值，关闭时先停它）
_embedded_worker = None


def _cleanup_resources():
    """停止内嵌 worker / 协议服务器 → 释放 DB 连接池 / Redis / 线程池（幂等，可被多次调用）"""

    # 内嵌任务在 Web 进程内执行：先停它，避免 DB 被 dispose 后仍在跑的任务报错
    global _embedded_worker
    if _embedded_worker is not None:
        try:
            _embedded_worker.stop()
            Logger.info('Embedded task worker stopped')
        except Exception as e:
            Logger.warn(f'Embedded task worker stop error: {e}')
        _embedded_worker = None

    # 先停止 TCP/UDP 协议服务器：处理中的 on_message 可能用到 DB/Redis，
    # 必须在关闭依赖之前停止消息入口，给在途消息一个完成窗口
    try:
        stop_protocol_servers()
        Logger.info('Protocol servers stopped')
    except Exception as e:
        Logger.warn(f'Protocol servers stop error: {e}')

    try:
        from flask_server.module import sqlalchemy
        db = sqlalchemy()
        if db is not None:
            db.engine.dispose()
            Logger.info('SQLAlchemy engine disposed')
    except Exception as e:
        Logger.warn(f'SQLAlchemy dispose error: {e}')

    try:
        from flask_server.module import redis_cache
        if redis_cache is not None:
            redis_cache.client.close()
            Logger.info('Redis connection closed')
    except Exception as e:
        Logger.warn(f'Redis close error: {e}')

    try:
        from flask_server.util.async_task_util import AsyncTaskUtil
        AsyncTaskUtil.executor.shutdown(wait=False)
        Logger.info('AsyncTask executor shutdown')
    except Exception as e:
        Logger.warn(f'AsyncTask shutdown error: {e}')


def _graceful_exit(reason='graceful shutdown complete'):
    """清理依赖并立即退出。

    信号处理器里 sys.exit 会抛 SystemExit，可能被 WSGI 主循环吞掉导致进程不退，
    因此用 os._exit 确保立即退出（此时在途请求已 drain 或已超时）。
    """
    Logger.info(reason)
    _cleanup_resources()
    os._exit(0)


def _install_signal_handlers(server, graceful_timeout=GRACEFUL_TIMEOUT):
    """注册 SIGTERM/SIGINT：停止 accept → 等在途请求完成 → 退出。

    实现要点：
      - `server.close()` 只关闭监听 socket 并从 asyncore map 中移除，**不会**打断已建立
        的连接；`server.run()` 的 loop 在 map 清空后自然返回，此时在途请求已处理完
        （修复前直接 os._exit(0)，在途请求被强杀，滚动更新时表现为偶发 502/400）。
      - 空闲 keep-alive 连接会一直占着 map，因此用看门狗线程在上限时间后强制退出。
      - 二次信号（运维连按 Ctrl+C）立即退出，不等 drain。

    Returns:
        dict: 状态字典（'triggered' 表示已进入关闭流程），便于测试断言。
    """
    state = {'triggered': False}

    def _handler(signum, frame):
        if state['triggered']:
            Logger.warn(f'signal {signum} received again, forcing exit')
            _cleanup_resources()
            os._exit(0)
        state['triggered'] = True
        Logger.info(f'Graceful shutdown initiated (signal {signum}): '
                    f'stop accepting, drain in-flight requests up to {graceful_timeout}s')
        try:
            server.close()
        except Exception as e:
            Logger.warn(f'waitress server close error: {e}')

        watchdog = threading.Timer(
            graceful_timeout,
            lambda: _graceful_exit(f'graceful shutdown timed out after {graceful_timeout}s, forcing exit'),
        )
        watchdog.daemon = True
        watchdog.start()

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)
    return state


def _waitress_options():
    """waitress 启动参数（含代理头策略）。

    ⚠️ waitress 3 起 `clear_untrusted_proxy_headers` **默认 True**：未配置
    `trusted_proxy` 时会直接删除请求中的 `X-Forwarded-*`。这会让框架的
    `TRUSTED_PROXIES` + `CommonUtil.get_real_ip()` 逻辑彻底失效——置于反向代理之后时
    所有请求的 `remote_addr` 都是代理 IP，直接后果：
      - 限流按 IP 计数退化成一个全局桶（全站共享 60 次/分钟）
      - 审计日志/链路追踪记录的都是代理 IP
    这里显式关闭该清理：信任决策统一由 `get_real_ip` 按 `TRUSTED_PROXIES`（支持 CIDR）
    完成——来源不在可信列表时 XFF 一律被忽略，不会引入伪造风险。
    """
    return {
        'host': config.host,
        'port': config.port,
        'threads': config.thread_num,
        'clear_untrusted_proxy_headers': False,
    }


def main():
    """启动 waitress 生产服务器（单进程多线程）"""
    global _embedded_worker
    print_startup_banner()
    # 协议服务器随主进程启动（TCP_ENABLED / UDP_ENABLED 为 true 时生效）
    start_protocol_servers()
    # 内嵌任务消费者（TASK_WORKER_EMBEDDED=true；生产建议独立进程跑 scripts/worker.py）
    _embedded_worker = start_embedded_task_worker(app=app)

    options = _waitress_options()
    server = create_server(app, **options)
    _install_signal_handlers(server)
    Logger.info(f'waitress serving on http://{config.host}:{config.port} '
                f'(threads={config.thread_num}, graceful_timeout={GRACEFUL_TIMEOUT}s)')
    try:
        # 收到信号后 close() 已执行，loop 在在途连接处理完后返回 → 正常退出路径
        server.run()
    finally:
        _graceful_exit('Graceful shutdown complete')


if __name__ == '__main__':
    main()
