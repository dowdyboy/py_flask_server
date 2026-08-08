# 先加载webui的controller（显式导入，避免 * 导入污染命名空间）
from . import webui_controller  # noqa: F401   # 注册 webui 路由
# 后面加载的url覆盖前面的
from .hello_controller import blp as hello_blp

# 注册 flask-smorest Blueprint（带参数校验与 API 文档）
from flask_server.app import api
api.register_blueprint(hello_blp)

# 导出所有controller
# 所有定义的controller都需要被导出，否则不会被加载
# 导出方法如上
# 用户/文章等接口样例可参考 examples/controller/

__all__ = [
    'webui_controller',
]

