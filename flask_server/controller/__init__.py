# Controller 自动发现注册
#
# 用法：在 controller/ 目录下新建 .py 文件并定义 Blueprint 实例（blp），
# 无需修改本文件——框架会自动导入该模块并注册其中所有 Blueprint。
#
# 约定（两种写法都支持，可混用）：
#   1. 定义 flask_smorest.Blueprint（推荐：自动校验 + OpenAPI 文档）
#   2. 定义模块级 `def register(app)`（用于 @app.route 风格或需要在 app 上挂 URL 规则）
#
# 注册顺序按文件名排序（确定性）；URL 匹配由 Werkzeug 按路由权重决定，与顺序无关。
#
# 与 handler/ 的策略差异（刻意）：
#   - controller 导入失败 → **fail-fast**：接口缺失意味着服务对外契约不完整，
#     静默跳过会让线上少一批接口而无人察觉；此处包装错误信息便于快速定位模块。
#   - handler 导入失败 → 跳过并记 ERROR：单个消息处理器不应拖垮整个 HTTP 服务。
#   handler 侧同样会打印日志，两侧都可观测。

import importlib
import pkgutil

from flask_smorest import Blueprint

# 幂等标记：同一 app 只装配一次（重复调用会重复 add_url_rule，产生重复路由规则）
_REGISTERED_KEY = 'flask_server.controllers_registered'


def register_controllers(app, api, package_name='flask_server.controller'):
    """导入 controller 包下的所有模块，并把 Blueprint / register(app) 装配到指定 app。

    幂等：同一个 app 重复调用直接返回（标记写在 app.extensions，注册成功后才落标记，
    中途失败可重试）；另一个 app 实例（新的 Api）仍会完整装配。

    Args:
        app (Flask): 目标应用实例（传给模块级 register(app) 钩子）
        api (flask_smorest.Api): 与 app 绑定的 Api，用于注册 Blueprint
        package_name (str): controller 包名
    """
    if app.extensions.get(_REGISTERED_KEY):
        return

    package = importlib.import_module(package_name)
    for module_info in sorted(pkgutil.iter_modules(package.__path__), key=lambda m: m.name):
        try:
            module = importlib.import_module(f'{package_name}.{module_info.name}')
        except Exception as e:
            raise RuntimeError(
                f'controller 模块 `{module_info.name}` 导入失败，应用无法启动：{e}'
                '（controller 采用 fail-fast，避免接口静默缺失；排查该文件的语法/导入错误）'
            ) from e

        # 约定 2：模块级 register(app) 钩子
        hook = getattr(module, 'register', None)
        if callable(hook):
            hook(app)

        # 约定 1：模块内的 Blueprint 实例
        for attr_name in dir(module):
            obj = getattr(module, attr_name)
            if isinstance(obj, Blueprint):
                api.register_blueprint(obj)

    app.extensions[_REGISTERED_KEY] = True


__all__ = ['register_controllers']
