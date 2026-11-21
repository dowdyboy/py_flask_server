from flask_server import app, json_response
from flask_server.util import Logger, GraceResult
from flask import request

# 样例Controller，展示了获取参数和使用cache
# 注意：接入工程前需将 UserService 放入 flask_server/service/ 并导出
#       from .user_service import UserService
#       __all__ = ['UserService']

from examples.service.user_service import UserService
from flask_server.module import memory_cache as cache


Logger.info("user_controller.py loaded")


@app.route('/api/user/login', methods=['POST'])
@json_response
def login():
    # 显式校验缺失字段：不要依赖 KeyError → 400（未捕获 KeyError 现在按 500 处理）
    payload = request.payload if isinstance(request.payload, dict) else {}
    username = payload.get('username')
    password = payload.get('password')
    if not username or not password:
        return GraceResult.param_error(
            data={'username': 'required', 'password': 'required'}), 400
    token = UserService.login(username, password)
    if token is None:
        return GraceResult.business_error(4001, '用户名或密码错误')
    return GraceResult.ok({
        'token': token
    })


@app.route('/api/user/logout', methods=['POST'])
@json_response
def logout():
    token = request.info.get('token')
    cache.delete(token)
    Logger.info(f'logout : {token}')
    return GraceResult.ok()

