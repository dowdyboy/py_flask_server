from flask_server import app, socketio, config


if __name__ == '__main__':
    # 启动服务（开发调试）
    # app.run(debug=config.debug, port=config.port)

    # 启动服务（部署、支持websocket）
    socketio.run(app, host='0.0.0.0', debug=config.debug, port=config.port, allow_unsafe_werkzeug=True, )

