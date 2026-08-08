# SocketIO 示例：聊天式事件演示（教学参考，不在工程运行路径内）
#
# 启用步骤：
#   1. pip install Flask-SocketIO simple-websocket
#   2. .env 设置 SOCKETIO_ENABLED=true（异步模式默认 threading）
#   3. python server.py
#   4. 前端连接：io('http://127.0.0.1:5000')
#      - 发送事件 'client_msg'：{message: 'hello'}
#      - 服务端回事件 'server_msg'
#   5. 生产环境：waitress 不支持 WebSocket，需使用 wsgi_gunicorn.py（Linux + eventlet）
#
# 接入工程：将下方 handler 合并到 flask_server/app.py（socketio 初始化之后）。

from flask_server import socketio


@socketio.on('connect')
def handle_connect():
    """客户端连接时触发"""
    print(f'[socketio] client connected: {socketio.get_environ().get("REMOTE_ADDR")}')


@socketio.on('disconnect')
def handle_disconnect():
    print('[socketio] client disconnected')


@socketio.on('client_msg')
def handle_client_msg(data):
    """回显消息：客户端发送 {'message': 'hello'} → 服务端回 {'echo': 'hello'}"""
    message = (data or {}).get('message', '')
    socketio.emit('server_msg', {'echo': message})


# 若在 app.py 合并，还需：
# @socketio.on('message')
# def handle_message(message):
#     socketio.send(f'echo: {message}')
