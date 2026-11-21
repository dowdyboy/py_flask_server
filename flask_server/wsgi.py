"""默认 WSGI 实例（唯一的模块级 `app = create_app()` 所在地）。

gunicorn / flask CLI / 容器入口请引用 `flask_server.wsgi:app`；
测试或多实例请直接用 `flask_server.factory.create_app()`，不要导入本模块。
"""

from .factory import create_app

app = create_app()

__all__ = ['app']
