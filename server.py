from werkzeug.serving import is_running_from_reloader
from flask_server import app, socketio, config
from flask_server.module import start_protocol_servers, stop_protocol_servers
from flask_server.util.banner import print_startup_banner


def should_start_protocol_servers():
    """协议服务器是否应在当前进程启动。

    debug 模式启用 Werkzeug reloader：父进程（reloader 监督者）只负责监控文件变化
    并重启子进程，不提供 HTTP 服务（HTTP socket 以 FD 形式传给子进程，但协议服务器
    不走 FD 继承）。若父进程也调用 start_protocol_servers() 绑定 TCP/UDP 端口，
    子进程重新执行本文件时二次绑定会 EADDRINUSE 崩溃 → 无限重启循环。
    因此仅在非 debug 模式，或 reloader 子进程（真实服务进程）中启动。
    """
    if not config.debug:
        return True
    return is_running_from_reloader()


if __name__ == '__main__':
    # 启动服务（开发调试）
    print_startup_banner()
    # 启动 TCP/UDP 协议服务器（TCP_ENABLED / UDP_ENABLED 为 true 时生效；
    # debug+reloader 模式下仅真实服务进程启动，避免父子进程端口冲突）
    if should_start_protocol_servers():
        start_protocol_servers()

    try:
        # 启动服务（部署、支持websocket）
        if socketio is not None:
            socketio.run(app, host=config.host, debug=config.debug, port=config.port, allow_unsafe_werkzeug=True, )
        else:
            app.run(host=config.host, debug=config.debug, port=config.port)
    finally:
        # 退出时停止协议服务器（atexit 亦有兜底）
        stop_protocol_servers()
