# Controller 自动发现注册
#
# 用法：在 controller/ 目录下新建 .py 文件并定义 Blueprint 实例（blp），
# 无需修改本文件——框架会自动导入该模块并注册其中所有 Blueprint。
#
# 约定：
#   - 模块内可定义多个 Blueprint，全部自动注册
#   - 模块的 import 副作用（如 @app.route）在导入时执行（webui 静态服务依赖此行为）
#   - 注册顺序按文件名排序（确定性）；URL 匹配由 Werkzeug 按路由权重决定，与顺序无关

import importlib
import pkgutil

from flask_smorest import Blueprint

from flask_server.app import api


def auto_register_controllers(package_name='flask_server.controller'):
    """自动注册 controller 包下所有模块中的 Blueprint 实例"""
    package = importlib.import_module(package_name)
    for module_info in sorted(pkgutil.iter_modules(package.__path__), key=lambda m: m.name):
        module = importlib.import_module(f'{package_name}.{module_info.name}')
        for attr_name in dir(module):
            obj = getattr(module, attr_name)
            if isinstance(obj, Blueprint) and not getattr(obj, '_flask_server_registered', False):
                api.register_blueprint(obj)
                obj._flask_server_registered = True


auto_register_controllers()

__all__ = []
