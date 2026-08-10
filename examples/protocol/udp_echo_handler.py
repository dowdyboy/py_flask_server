# UDP 协议处理器示例（教学）
#
# 接入工程（推荐）：把下面的处理器定义复制到 flask_server/handler/ 下的
# 任意 .py 文件，启动服务后自动生效（无需改任何入口文件）。
#   - 配置 .env：UDP_ENABLED=true（端口 UDP_PORT，默认 9001）
#
# 独立运行（演示）：python examples/protocol/udp_echo_handler.py
#   - 直接调用 udp_server.start() 启动（忽略 UDP_ENABLED 配置，便于快速联调）
#   - 配套客户端：python examples/protocol/udp_client.py

import time

from flask_server.module import udp_server
from flask_server.util import Logger


@udp_server.on_message
def on_message(data, addr):
    """收到一个数据报时触发（UDP 天然按消息边界，data 即完整消息，bytes）

    返回 bytes → 框架自动回发到来源地址（最简用法）；
    返回 None  → 不回发（可用 udp_server.send(data, addr) 主动发送）。
    """
    Logger.info(f'[udp example] recv from {addr}: {data}')
    return b'echo: ' + data


@udp_server.on_error
def on_error(e, *args):
    """处理器抛出异常时触发（记录后不影响其他数据报）"""
    Logger.error(f'[udp example] handler error: {e}', exc_info=True)


if __name__ == '__main__':
    udp_server.start()
    Logger.info('[udp example] started, try: python examples/protocol/udp_client.py')
    while True:
        time.sleep(3600)
