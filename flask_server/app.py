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
from flask import Flask, request, g
from flask_cors import CORS
from werkzeug.exceptions import HTTPException
from flask_smorest import Api
from .module import init_SQLAlchemy, init_Migrate
from .util import Logger, GraceResult, CommonUtil

# Flask App 启动


app = Flask(__name__)
CORS(app, origins=config.cors_origins)

# 安全配置
app.config['SECRET_KEY'] = config.secret_key
app.config['MAX_CONTENT_LENGTH'] = config.max_content_length

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
            max_http_buffer_size=5e8,
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
            obj, status = resp
            if obj is None:
                obj = GraceResult.ok()
            return CommonUtil.obj_to_dict(obj), status
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
        request.payload = request.get_json()


@app.before_request
def parse_request_form_data():
    if request.method in _BODY_METHODS and \
            str(request.content_type).lower().startswith('application/x-www-form-urlencoded'):
        request.payload = request.form.to_dict()
    if request.method in _BODY_METHODS and \
            str(request.content_type).lower().startswith('multipart/form-data'):
        request.payload = dict(request.form.to_dict(), **request.files.to_dict())


@app.teardown_request
def clear_request_id(exc=None):
    """请求结束时清除 request_id，避免线程复用时串号"""
    Logger.clear_request_id()


# Exception Handler ########
# 恢复 RESTful 状态码：HTTP 异常保留原状态码，业务异常返回 500，参数错误返回 400

@app.errorhandler(HTTPException)
@json_response
def http_exception_handler(e):
    Logger.error(f'http_exception_handler : {e}')
    if e.code == 404:
        return GraceResult.error(data='资源不存在'), 404
    return GraceResult.error(data=str(e)), e.code


@app.errorhandler(KeyError)
@json_response
def param_exception_handler(e):
    Logger.error(f'param_exception_handler : {e}')
    return GraceResult.param_error(data=str(e)), 400


@app.errorhandler(Exception)
@json_response
def all_exception_handler(e):
    # 防御性双保险：若 HTTPException 意外落入此 handler，仍按原状态码返回
    if isinstance(e, HTTPException):
        Logger.error(f'http_exception_fallback : {e}')
        return GraceResult.error(data=str(e)), e.code
    Logger.error(f'all_exception_handler : {e}')
    return GraceResult.error(data=str(e)), 500
