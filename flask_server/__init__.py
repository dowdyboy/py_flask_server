from .app import app, json_response, socketio
from .config import config

# 1、优先加载util
from .util import *
# 2、再加载module
from .module import *
# 3、再加载component
from .component import *

from .controller import *
from .service import *


__all__ = [
    'app',
    'socketio',
    'json_response',
    'config',
]
