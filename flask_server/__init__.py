"""Flask Server 包入口（0.8 起**导入本包不会创建应用**）。

三种用法：

    # 1) 只需要配置/工具：完全不装配 app、不连 DB
    from flask_server import config
    from flask_server.util import Logger

    # 2) WSGI / 容器 / gunicorn：显式取默认实例
    from flask_server.wsgi import app          # gunicorn: flask_server.wsgi:app
    # 旧写法仍可用，但只有**首次访问**才创建（惰性）：
    from flask_server import app

    # 3) 测试/多实例/worker：显式建 app
    from flask_server.factory import create_app, create_core_app

`__getattr__`（PEP 562）负责三组惰性导出：默认 app（app/api/socketio）、
工厂函数、以及 util 里的常用工具；未列出的名字仍按普通子模块导入
（`from flask_server import module` / `task` / `controller` 等）。
"""

from .config import config

__all__ = [
    'config',
    'app', 'api', 'socketio',
    'create_app', 'create_core_app', 'json_response',
    'Logger', 'GraceResult', 'ErrorCode', 'RandomGenerator', 'KeyGenerator',
    'DateTimeUtil', 'CommonUtil', 'DataEncryptUtil', 'AsyncTaskUtil',
    'SubprocessTask', 'SubprocessTaskInterface', 'Page', 'paginate',
]

# 惰性导出表：名字 → 来源模块（首次访问时导入并缓存到包命名空间）
_FACTORY_EXPORTS = {'create_app', 'create_core_app', 'json_response'}
_UTIL_EXPORTS = {
    'Logger', 'GraceResult', 'ErrorCode', 'RandomGenerator', 'KeyGenerator',
    'DateTimeUtil', 'CommonUtil', 'DataEncryptUtil', 'AsyncTaskUtil',
    'SubprocessTask', 'SubprocessTaskInterface', 'Page', 'paginate',
}


def __getattr__(name):
    if name == 'app':
        # 等价于旧版模块级 `app = create_app()`，但推迟到首次访问；
        # 缓存写回 globals()，后续访问不再经过 __getattr__。
        from .wsgi import app as _app
        globals()['app'] = _app
        return _app
    if name in ('api', 'socketio'):
        value = getattr(__getattr__('app'), name)
        globals()[name] = value
        return value
    if name in _FACTORY_EXPORTS:
        from . import factory
        value = getattr(factory, name)
        globals()[name] = value
        return value
    if name in _UTIL_EXPORTS:
        from . import util
        value = getattr(util, name)
        globals()[name] = value
        return value
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


def __dir__():
    return sorted(set(globals()) | set(__all__))
