# 鉴权拦截器参考样例（不在工程运行路径内，仅作教学参考）
# 展示了基于 X-AUTH-TOKEN / X-USER-KEY 的鉴权拦截器写法。
# 接入工程前需：
#   1. 将本文件内容合并到 flask_server/component/interceptor.py，
#      并按需填写 need_auth_path_list / need_user_key_path_list。

from flask_server import app, json_response
from flask_server.util import Logger, GraceResult
from flask_server.module import memory_cache as cache
from flask import request

# 拦截器定义，可以在请求处理前添加认证和授权检查等操作，下为例子
# 写法参考flask，使用了flask的before_request装饰器
# 拦截器函数返回None时，请求继续执行，返回其他值时，请求被中断，返回值作为响应返回

Logger.info('component interceptor example loaded')


need_auth_path_list = [
    # '/api/user/profile'
]


@app.before_request
@json_response
def parse_auth_header():
    if request.path in need_auth_path_list:
        token = request.headers.get('X-AUTH-TOKEN')
        if token is None or not cache.exists(token):
            return GraceResult.business_error(4003, 'Token 无效或已过期'), 401
        uid = cache.get(token)
        request.info['token'] = token
        request.info['uid'] = uid


need_user_key_path_list = [
    # '/api/article/get'
]


@app.before_request
@json_response
def parse_user_key_header():
    if request.path in need_user_key_path_list:
        user_key = request.headers.get('X-USER-KEY')
        if user_key is None:
            return GraceResult.business_error(4004, '会话不存在'), 401
        request.info['user_key'] = user_key
