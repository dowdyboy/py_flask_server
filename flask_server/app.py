import uuid
from .config import config

# 仅 eventlet 模式才 monkey_patch（须在 socket 相关导入前执行）
_socketio_will_init = config.socketio_enabled
_async_mode = config.socketio_async_mode
if _socketio_will_init and _async_mode == 'eventlet':
    try:
        import eventlet
        eventlet.monkey_patch()
    except ImportError:
        _async_mode = 'threading'   # 未安装 eventlet 则降级为 threading

from functools import wraps
from flask import Flask, request, g, Response
from flask_cors import CORS
from werkzeug.exceptions import HTTPException, UnprocessableEntity
from flask_smorest import Api
from .module import init_SQLAlchemy, init_Migrate
from .util import Logger, GraceResult, CommonUtil

# Flask App 启动


app = Flask(__name__)
CORS(app, origins=config.cors_origins)

# 安全配置
app.config['SECRET_KEY'] = config.secret_key
app.config['MAX_CONTENT_LENGTH'] = config.max_content_length

# 非 development 环境使用默认 SECRET_KEY 时告警（生产环境必须修改）
if config.app_env != 'development' and config.secret_key_is_default:
    Logger.warn('SECRET_KEY is set to the default scaffold value. '
                'Please set a strong custom SECRET_KEY in production!')

# flask-smorest API 文档 + 参数校验
app.config.update({
    'API_TITLE': config.api_title,
    'API_VERSION': config.api_version,
    'OPENAPI_VERSION': '3.0.3',
    'OPENAPI_URL_PREFIX': '/',
    'OPENAPI_JSON_PATH': config.api_spec_url,
    'OPENAPI_SWAGGER_UI_PATH': config.api_docs_url,
    'OPENAPI_SWAGGER_UI_URL': config.swagger_ui_url,
})
api = Api(app)

# SocketIO（可选，默认关闭）
socketio = None
if _socketio_will_init:
    try:
        from flask_socketio import SocketIO
        socketio = SocketIO(
            app,
            async_mode=_async_mode,
            ping_timeout=120,
            ping_interval=30,
            max_http_buffer_size=config.socketio_max_http_buffer_size,
            cors_allowed_origins=config.cors_origins,
        )
    except ImportError:
        Logger.warn('SOCKETIO_ENABLED=true but Flask-SocketIO not installed, skipping SocketIO init')
        socketio = None


# Initialize

init_SQLAlchemy(app)
init_Migrate(app)


# Common Annotation ########

def json_response(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        resp = func(*args, **kwargs)
        if isinstance(resp, tuple):
            # 支持 Flask 2/3 元组约定：(data, status) / (data, status, headers)
            obj, status = resp[0], resp[1]
            headers = resp[2] if len(resp) >= 3 else None
            # 视图返回 Response 时直接透传，避免序列化响应对象
            if isinstance(obj, Response):
                return (obj, status, headers) if headers is not None else (obj, status)
            if obj is None:
                obj = GraceResult.ok()
            data = CommonUtil.obj_to_dict(obj)
            return (data, status, headers) if headers is not None else (data, status)
        # 视图直接返回 Response（send_file/redirect 等）时原样透传
        if isinstance(resp, Response):
            return resp
        if resp is None:
            resp = GraceResult.ok()
        return CommonUtil.obj_to_dict(resp)
    return wrapper


# Request Param Parser ########

@app.before_request
def init_request_info():
    if not hasattr(request, 'info'):
        request.info = {}
    # request_id 链路追踪：从 header 透传或自动生成
    request_id = request.headers.get('X-Request-Id') or uuid.uuid4().hex
    request.info['request_id'] = request_id
    g.request_id = request_id
    Logger.set_request_id(request_id)
    # 默认初始化 payload，防止 content-type 不匹配时 AttributeError
    request.payload = {}


@app.before_request
def parse_request_param():
    request.params = request.args.to_dict()


# 支持携带 body 的方法：POST/PUT/PATCH/DELETE
_BODY_METHODS = ('POST', 'PUT', 'PATCH', 'DELETE')


@app.before_request
def parse_request_json():
    if request.method in _BODY_METHODS and request.is_json:
        data = request.get_json()
        # 顶层 JSON 必须为对象：数组/字符串等非 dict 会被视图按字段访问
        # （payload['x']）抛 TypeError → 500，归一为 {} 后走 KeyError → 400 参数错误
        if data is not None and not isinstance(data, dict):
            Logger.warn(f'non-dict JSON payload ignored: {type(data).__name__}')
            data = {}
        request.payload = data


@app.before_request
def parse_request_form_data():
    if request.method in _BODY_METHODS and \
            str(request.content_type).lower().startswith('application/x-www-form-urlencoded'):
        request.payload = request.form.to_dict()
    if request.method in _BODY_METHODS and \
            str(request.content_type).lower().startswith('multipart/form-data'):
        request.payload = dict(request.form.to_dict(), **request.files.to_dict())


@app.after_request
def set_request_id_header(resp):
    # 回写 X-Request-Id，客户端可据此关联服务端日志进行链路追踪
    rid = getattr(g, 'request_id', None)
    if rid:
        resp.headers['X-Request-Id'] = rid
    return resp


# 基础安全响应头（SECURITY_HEADERS_ENABLED=true 时生效）
_SECURITY_HEADERS = {
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'DENY',
    'Referrer-Policy': 'no-referrer',
}


@app.after_request
def set_security_headers(resp):
    if config.security_headers_enabled:
        for k, v in _SECURITY_HEADERS.items():
            resp.headers.setdefault(k, v)
        if request.path == config.api_docs_url:
            # /docs（Swagger UI）使用 jsdelivr CDN 资源：放行远程脚本与内联样式
            resp.headers.setdefault(
                'Content-Security-Policy',
                "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
            )
        else:
            # 其余路径收紧：不信任内联脚本与远程脚本（webui 若含内联脚本需自行放行）
            resp.headers.setdefault(
                'Content-Security-Policy',
                "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'",
            )
    return resp


@app.teardown_request
def clear_request_id(exc=None):
    """请求结束时清除 request_id，避免线程复用时串号"""
    Logger.clear_request_id()


# Exception Handler ########
# 恢复 RESTful 状态码：HTTP 异常保留原状态码，业务异常返回 500，参数错误返回 400

# 生产环境（非 development）5xx 响应不向客户端回显内部异常详情（可能含 SQL/路径/连接串），
# 详情只写入服务端日志（含 traceback）；development 环境保留详情便于本地调试。
def _internal_error_detail(e):
    return str(e) if config.app_env == 'development' else '服务器内部错误'


@app.errorhandler(HTTPException)
@json_response
def http_exception_handler(e):
    # 4xx（404/405 等）多为客户端/扫描器行为，记 WARNING 避免刷屏；5xx 才记 ERROR
    if e.code is not None and 400 <= e.code < 500:
        Logger.warn(f'http_exception_handler : {e}')
    else:
        Logger.error(f'http_exception_handler : {e}', exc_info=True)
    if e.code == 404:
        return GraceResult.error(data='资源不存在'), 404
    # 5xx 脱敏：只回显通用消息；4xx 客户端错误信息可安全回显
    data = str(e) if (e.code is not None and e.code < 500) else _internal_error_detail(e)
    return GraceResult.error(data=data), e.code


@app.errorhandler(UnprocessableEntity)
@json_response
def unprocessable_entity_handler(e):
    # 参数校验失败（flask-smorest/webargs 422）：统一为 GraceResult 格式，
    # 保留字段级错误信息供客户端定位问题
    Logger.error(f'unprocessable_entity_handler : {e}', exc_info=True)
    data = getattr(e, 'data', None) or {}
    errors = data.get('messages') or data.get('errors')
    return GraceResult.param_error(data=errors), 422


@app.errorhandler(KeyError)
@json_response
def param_exception_handler(e):
    Logger.error(f'param_exception_handler : {e}', exc_info=True)
    return GraceResult.param_error(data=str(e)), 400


@app.errorhandler(Exception)
@json_response
def all_exception_handler(e):
    # 防御性双保险：若 HTTPException 意外落入此 handler，仍按原状态码返回
    if isinstance(e, HTTPException):
        Logger.error(f'http_exception_fallback : {e}', exc_info=True)
        return GraceResult.error(data=_internal_error_detail(e)), e.code
    Logger.error(f'all_exception_handler : {e}', exc_info=True)
    return GraceResult.error(data=_internal_error_detail(e)), 500
