from flask_server.app import app, json_response
from flask_server.util import Logger, GraceResult
from flask import request

# 样例Controller，展示了获取参数和使用cache
# 注意：接入工程前需在 flask_server/service/__init__.py 中导出 UserService
#       from .user_service import UserService
#       __all__ = ['UserService']

from flask_server.service import UserService
from flask_server.module import memory_cache as cache


Logger.info("user_controller.py loaded")


@app.route('/api/user/login', methods=['POST'])
@json_response
def login():
    username = request.payload['username']
    password = request.payload['password']
    token = UserService.login(username, password)
    if token is None:
        return GraceResult.business_error(4001, '用户名或密码错误')
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

