# 组件装配入口
#
# 组件（限流 / 认证 / 指标 / 拦截器）不再在 import 时修改"那个全局 app"，
# 而是暴露 `init_*(app)`，由 `flask_server.app.create_app()` 显式调用。
# 这样才可能在同一进程里创建多个应用实例，也让测试无需依赖导入顺序。
#
# 注册顺序即 before_request 执行顺序：限流 → 认证 → 指标计时。
# 限流必须在认证之前（未登录请求也要受限流保护，防爆破），认证在指标之前
# （被 401 拒绝的请求也计入指标计时起点）。

from . import interceptor  # noqa: F401  拦截器示例（纯注释，无副作用）
from . import rate_limit
from . import auth
from . import metrics


def init_components(app, cfg=None):
    """把全部横切组件装配到指定 app

    Args:
        app (Flask): 目标应用
        cfg (Config, optional): 应用的框架配置（注册期需要判断的开关，如 AUTH_ENABLED）
    """
    rate_limit.init_rate_limit(app)
    auth.init_auth(app, cfg)
    metrics.init_metrics(app)


__all__ = [
    'interceptor',
    'rate_limit',
    'auth',
    'metrics',
    'init_components',
]
