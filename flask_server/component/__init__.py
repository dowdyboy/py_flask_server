# 组件装配入口
#
# 组件（限流 / 认证 / 指标 / 拦截器）不再在 import 时修改"那个全局 app"，
# 而是暴露 `init_*(app)`，由 `flask_server.factory.create_app()` 显式调用。
# 这样才可能在同一进程里创建多个应用实例，也让测试无需依赖导入顺序。
#
# 注册顺序即 before_request 执行顺序：限流 → 认证 → 指标计时。
# 限流必须在认证之前（未登录请求也要受限流保护，防爆破）。
#
# 注意"指标计时"这一项的语义：Flask 在任一 before_request 返回非 None 时短路后续钩子，
# 所以被限流/认证拦下的请求（429/401）不会执行 metrics_start_timer —— 它们会进入
# http_requests_total 计数，但不进入延迟直方图（metrics_record 中 start 为 None）。
# 这是刻意的取舍：拒绝类响应几乎没有业务耗时，混进延迟分位数反而会污染 P95/P99。

from . import interceptor  # noqa: F401  拦截器占位文件（纯注释，无副作用）
from . import rate_limit
from . import auth
from . import metrics


def init_components(app, cfg=None):
    """把全部横切组件装配到指定 app

    Args:
        app (Flask): 目标应用
        cfg (Config, optional): 应用的框架配置（注册期需要判断的开关，如 AUTH_ENABLED）
    """
    rate_limit.init_rate_limit(app, cfg)
    auth.init_auth(app, cfg)
    metrics.init_metrics(app, cfg)


__all__ = [
    'interceptor',
    'rate_limit',
    'auth',
    'metrics',
    'init_components',
]
