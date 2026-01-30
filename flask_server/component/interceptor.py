from flask_server import app, json_response
from flask_server.util import Logger, GraceResult, CommonUtil
from flask_server.module import memory_cache as cache
from flask import Flask, request, jsonify, send_file

# 拦截器定义，可以在请求处理前添加认证和授权检查等操作，下为例子
# 写法参考flask，使用了flask的before_request装饰器
# 拦截器函数返回None时，请求继续执行，返回其他值时，请求被中断，返回值作为响应返回

Logger.info(f'component interceptor loaded')


need_auth_path_list = [
    # '/test/url'
]


@app.before_request
@json_response
def parse_auth_header():
    if request.path in need_auth_path_list:
        token = request.headers.get('X-AUTH-TOKEN')
        if token is None or not cache.exists(token):
            return GraceResult.auth_token_error()
        uid = cache.get(token)
        request.info['token'] = token
        request.info['uid'] = uid


need_user_key_path_list = [
    # '/test/url'
]


@app.before_request
@json_response
def parse_user_key_header():
    if request.path in need_user_key_path_list:
        user_key = request.headers.get('X-USER-KEY')
        if user_key is None:
            return GraceResult.session_not_exist_error()
        request.info['user_key'] = user_key



