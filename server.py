from flask_server import app, socketio, config


if __name__ == '__main__':
    # 启动服务（开发调试）
    # app.run(debug=config.debug, port=config.port)

    # 启动服务（部署、支持websocket）
    if socketio is not None:
        socketio.run(app, host=config.host, debug=config.debug, port=config.port, allow_unsafe_werkzeug=True, )
    else:
        app.run(host=config.host, debug=config.debug, port=config.port)
