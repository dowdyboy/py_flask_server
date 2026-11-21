# 鉴权拦截器参考样例（不在工程运行路径内，仅作教学参考）
# 展示基于 X-AUTH-TOKEN / X-USER-KEY 的 before_request 拦截器写法。
#
# 接入工程：
#   1. 把本文件内容放进 flask_server/component/interceptor.py（或另存为新模块）；
#   2. 在 component/__init__.py 的 init_components() 里调用
#      `init_interceptor_example(app)`——**不要**用模块级 `@app.before_request`，原因见下。
#
# 为什么必须写成 init_xxx(app) 形式：
#   component/ 包是在 create_app() 内部被导入的，此时不应依赖任何"模块级 app 单例"；
#   0.8 起模块级实例只存在于 flask_server/wsgi.py，且包导入本身不再建 app。
#   在模块级写 `from flask_server import app` 会触发默认实例装配（也可能循环导入），
#   写死在模块级单例上也无法支持 create_app(framework_config=...) 的多实例隔离。
#   auth / metrics / rate_limit 三个内置组件都是 init_xxx(app) 形式，本样例保持一致。
#
# before_request 的返回值语义（最容易踩的坑）：
#   - 返回 None  → 请求继续，后续钩子与视图正常执行；
#   - 返回其它值 → **立即短路**，该值作为响应，视图不会被执行。
#   因此拦截器**绝不能**套 @json_response：它会把 None 改写成 GraceResult.ok() 的 dict，
#   于是每个请求都在这一层被替换成 200 {"code":0,"msg":"成功","data":null}，视图永不执行，
#   而且不报任何错——线上表现为"接口全都成功但什么也没做"。
#   失败路径请显式构造统一格式的响应（如下），或 abort(401) 交给统一异常处理。

from flask import jsonify, request

from flask_server.util import CommonUtil, GraceResult, Logger
from flask_server.module import memory_cache as cache

# 需要校验 X-AUTH-TOKEN 的路径（精确匹配；实际项目里通常改成前缀匹配）
need_auth_path_list = [
    # '/api/user/profile'
]

# 需要校验 X-USER-KEY 的路径
need_user_key_path_list = [
    # '/api/article/get'
]


def _business_error_response(code, msg):
    """构造与框架统一响应格式一致的 JSON 响应（拦截器里不能用 @json_response）"""
    return jsonify(CommonUtil.obj_to_dict(GraceResult.business_error(code, msg)))


def _interceptor():
    """单个 before_request 钩子：先校验 token，再校验 user key。

    - 命中 need_auth_path_list：校验 X-AUTH-TOKEN，写 request.info['token'] / ['uid']
    - 命中 need_user_key_path_list：校验 X-USER-KEY，写 request.info['user_key']
    通过时返回 None（请求继续）；失败时返回响应（短路请求）。
    """
    path = request.path

    if path in need_auth_path_list:
        token = request.headers.get('X-AUTH-TOKEN')
        if token is None or not cache.exists(token):
            return _business_error_response(4003, 'Token 无效或已过期'), 401
        request.info['token'] = token
        request.info['uid'] = cache.get(token)

    if path in need_user_key_path_list:
        user_key = request.headers.get('X-USER-KEY')
        if user_key is None:
            return _business_error_response(4004, '会话不存在'), 401
        request.info['user_key'] = user_key

    return None


def init_interceptor_example(app):
    """把拦截器装配到指定 app（由 component/__init__.py::init_components 调用）"""
    app.before_request(_interceptor)
    Logger.info('interceptor example enabled '
                f'(auth paths={len(need_auth_path_list)}, '
                f'user-key paths={len(need_user_key_path_list)})')
