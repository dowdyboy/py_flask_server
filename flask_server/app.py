from functools import wraps
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from flask_socketio import SocketIO
from .module import init_SQLAlchemy
from .util import Logger, GraceResult, CommonUtil


# Flask App 启动


app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# Initialize

init_SQLAlchemy(app)


# Common Annotation ########

def json_response(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        resp = func(*args, **kwargs)
        resp = CommonUtil.obj_to_dict(resp)
        return resp
    return wrapper


# Request Param Parser ########

@app.before_request
def init_request_info():
    if not hasattr(request, 'info'):
        request.info = {}


@app.before_request
def parse_request_param():
    request.params = request.args.to_dict()


@app.before_request
def parse_request_json():
    if request.method == 'POST' and request.is_json:
        request.payload = request.get_json()


@app.before_request
def parse_request_form_data():
    if (request.method == 'POST' and
            str(request.content_type).lower().startswith('application/x-www-form-urlencoded')):
        request.payload = request.form.to_dict()
    if (request.method == 'POST' and
            str(request.content_type).lower().startswith('multipart/form-data')):
        request.payload = dict(request.form.to_dict(), **request.files.to_dict())


# Exception Handler ########

@app.errorhandler(KeyError)
@json_response
def param_exception_handler(e):
    Logger.error(f'param_exception_handler : {e}')
    return GraceResult.param_error(data=str(e))


@app.errorhandler(Exception)
@json_response
def all_exception_handler(e):
    Logger.error(f'all_exception_handler : {e}')
    return GraceResult.error(data=str(e))

