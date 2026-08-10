from .app import app, json_response, api
from .app import socketio
from .config import config

# 1、优先加载util
from .util import *
# 2、再加载module
from .module import *
# 3、再加载model（UserPO 等声明式模型需在 SQLAlchemy 初始化后导入，
#    才能被 Flask-Migrate autogenerate 识别建表）
from . import model  # noqa: F401
# 4、再加载component
from .component import *
from .controller import *
from .service import *
# 5、再加载handler（协议消息处理器：注册 TCP/UDP 处理器，不启动 socket）
from . import handler  # noqa: F401


__all__ = [
    'app',
    'api',
    'socketio',
    'json_response',
    'config',
]
