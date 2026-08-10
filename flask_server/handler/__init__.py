# 协议消息处理器自动发现
#
# 用法：在本目录下新建 .py 文件，用装饰器注册 TCP/UDP 消息处理器
# （建文件即出接口，无需修改本文件，也无需改动任何入口文件）：
#
#   from flask_server.module import tcp_server, udp_server
#   from flask_server.util import Logger
#
#   # TCP：连接建立 / 消息（已按分隔符拆好，bytes）/ 连接断开
#   @tcp_server.on_connect
#   def on_connect(conn, addr):
#       Logger.info(f'tcp client connected: {addr}')
#
#   @tcp_server.on_message
#   def on_message(conn, data, addr):
#       conn.sendall(b'echo: ' + data)      # 用户自己决定如何回
#
#   @tcp_server.on_disconnect
#   def on_disconnect(conn, addr):
#       Logger.info(f'tcp client disconnected: {addr}')
#
#   # UDP：返回 bytes 自动回发到来源地址；返回 None 不回发
#   @udp_server.on_message
#   def on_message(data, addr):
#       return b'echo: ' + data
#
# 处理器异常不会中断服务器：Logger.error 记录完整 traceback 后走
# @tcp_server.on_error / @udp_server.on_error 钩子。
#
# 注册是 import 副作用（不启动 socket）；服务器由入口的
# start_protocol_servers() 启动，配置见 .env（TCP_ENABLED / UDP_ENABLED 等）。
# 完整教学样例见 examples/protocol/。

import importlib
import pkgutil

from flask_server.module import tcp_server, udp_server  # noqa: F401 确保单例可用
from flask_server.util import Logger


def auto_import_handlers(package_name='flask_server.handler'):
    """自动导入 handler 包下所有模块（模块内的装饰器注册即生效）。

    单个模块导入失败（语法错误/依赖缺失等）不影响其他模块与应用启动：
    记录 ERROR（含 traceback）后跳过，便于快速定位问题模块。
    """
    package = importlib.import_module(package_name)
    for module_info in sorted(pkgutil.iter_modules(package.__path__), key=lambda m: m.name):
        try:
            importlib.import_module(f'{package_name}.{module_info.name}')
        except Exception as e:
            Logger.error(f'handler module `{module_info.name}` import failed, skipped: {e}',
                         exc_info=True)


auto_import_handlers()

__all__ = []
