# TCP 协议处理器示例（教学）
#
# 接入工程（推荐）：把下面的处理器定义复制到 flask_server/handler/ 下的
# 任意 .py 文件，启动服务后自动生效（无需改任何入口文件）。
#   - 配置 .env：TCP_ENABLED=true（端口 TCP_PORT，默认 9000；行帧分隔符默认 \n）
#
# 独立运行（演示）：python examples/protocol/tcp_echo_handler.py
#   - 直接调用 tcp_server.start() 启动（忽略 TCP_ENABLED 配置，便于快速联调）
#   - 配套客户端：python examples/protocol/tcp_client.py

import time

from flask_server.module import tcp_server
from flask_server.util import Logger


@tcp_server.on_connect
def on_connect(conn, addr):
    """客户端建立连接时触发"""
    Logger.info(f'[tcp example] client connected: {addr}')


@tcp_server.on_message
def on_message(conn, data, addr):
    """收到一条完整消息时触发（data 为 bytes，已按分隔符拆好）

    连接是流式的：一帧可能跨越多个 TCP 包（拆包），一包可能含多帧（粘包），
    框架已自动处理，这里拿到的始终是完整的一帧。
    """
    Logger.info(f'[tcp example] recv from {addr}: {data}')
    conn.sendall(b'echo: ' + data)   # 用户自己决定如何回（sendall 直接写回）


@tcp_server.on_disconnect
def on_disconnect(conn, addr):
    """客户端关闭连接时触发（无论正常关闭还是异常断开）"""
    Logger.info(f'[tcp example] client disconnected: {addr}')


@tcp_server.on_error
def on_error(e, *args):
    """处理器抛出异常时触发（记录后不影响其他连接）"""
    Logger.error(f'[tcp example] handler error: {e}', exc_info=True)


if __name__ == '__main__':
    tcp_server.start()
    Logger.info('[tcp example] started, try: python examples/protocol/tcp_client.py')
    while True:
        time.sleep(3600)
