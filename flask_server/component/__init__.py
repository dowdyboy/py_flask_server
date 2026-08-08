# 导入拦截器模块（显式导入；拦截器通过 @app.before_request 自动注册，无需导出符号）
from . import interceptor  # noqa: F401
from . import rate_limit  # noqa: F401
from . import auth  # noqa: F401
from . import metrics  # noqa: F401

# 导入所有拦截器，拦截器写在interceptor.py中


__all__ = [
    'interceptor',
    'rate_limit',
    'auth',
    'metrics',
]
