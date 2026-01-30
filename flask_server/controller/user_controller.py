from flask_server.app import app, json_response
from flask_server.util import Logger, GraceResult
from flask_server.service import UserService
from flask_server.module import memory_cache as cache
from flask import request, send_file


# 样例Controller，展示了获取参数和使用cache


Logger.info("user_controller.py loaded")


@app.route('/api/user/login', methods=['POST'])
@json_response
def login():
    username = request.payload['username']
    password = request.payload['password']
    token = UserService.login(username, password)
    if token is None:
        return GraceResult.auth_error()
    return GraceResult.ok({
        'token': token
    })


@app.route('/api/user/logout', methods=['POST'])
@json_response
def logout():
    token = request.info['token']
    cache.delete(token)
    Logger.info(f'logout : {token}')
    return GraceResult.ok()

