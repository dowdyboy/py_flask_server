"""应用容器（app.extensions）约定键与访问器。

0.9 起，组件把"属于某个 app 的运行时对象"登记到 `app.extensions`：

    flask_server.db        SQLAlchemy 实例（进程级单例，见 module/sqlalchemy.py）
    flask_server.migrate   Flask-Migrate 实例
    flask_server.logger    该 app 独立的 logging.Logger
    flask_server.metrics   该 app 独立的 MetricsBundle
    flask_server.config    Config（既有约定）
    flask_server.auth_store 认证用户存储（既有约定）

访问器统一走当前 app 上下文；无上下文时返回调用方给的 default（或 None），
不会隐式创建应用/容器。
"""

from typing import Any

from flask import current_app, has_app_context

DB_EXT_KEY = 'flask_server.db'
MIGRATE_EXT_KEY = 'flask_server.migrate'
LOGGER_EXT_KEY = 'flask_server.logger'
METRICS_EXT_KEY = 'flask_server.metrics'


def get_extension(key: str, default: Any = None, app=None) -> Any:
    """读取当前 app（或显式 app）的扩展；无 app 上下文时返回 default"""
    if app is None:
        if not has_app_context():
            return default
        app = current_app
    return app.extensions.get(key, default)


def get_db(default: Any = None, app=None) -> Any:
    """当前 app 绑定的 SQLAlchemy 实例；无上下文回退 default（调用方可传模块单例）"""
    return get_extension(DB_EXT_KEY, default, app=app)


def get_logger(app=None):
    """当前 app 的 logger；无上下文返回 None"""
    return get_extension(LOGGER_EXT_KEY, None, app=app)


def get_metrics(app=None):
    """当前 app 的 MetricsBundle；无上下文返回 None"""
    return get_extension(METRICS_EXT_KEY, None, app=app)


__all__ = [
    'DB_EXT_KEY', 'MIGRATE_EXT_KEY', 'LOGGER_EXT_KEY', 'METRICS_EXT_KEY',
    'get_extension', 'get_db', 'get_logger', 'get_metrics',
]
