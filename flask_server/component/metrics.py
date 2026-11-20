import os
import time
from typing import Any, Optional

from flask import request, Response

from ..config import config

# Prometheus 指标组件（METRICS_ENABLED=true 默认开启）
# 暴露 /metrics（Prometheus 文本格式），指标：
#   - http_requests_total{method,status,route}     请求总数（按路由聚合，防高基数）
#   - http_request_duration_seconds_bucket/...      请求延迟直方图
#   - http_rate_limited_total{scope}               限流拒绝数（scope=path/ip）
#   - auth_login_locked_total                      登录因锁定被拒数
#   - task_rejected_total{reason}                  任务未执行即被拒绝/死信数
# 标签值全部来自固定集合（不取用户输入），保证低基数；业务代码统一用 `_inc`
# 自增（指标未启用/依赖缺失时自动跳过，绝不影响主流程）。
# 未安装 prometheus-client 时优雅降级（/metrics 返回 503 并告警一次）
#
# 多进程（gunicorn 多 worker）：
#   默认各 worker 独立计数，/metrics 只能看到"恰好被刮到"的那个进程，数据严重偏低。
#   设置 PROMETHEUS_MULTIPROC_DIR（指向一个空目录）即启用 prometheus_client 的多进程模式，
#   指标落盘共享；配合 wsgi_gunicorn.py 的 child_exit 钩子清理退出 worker 的指标文件。

# 多进程目录必须在创建 Counter/Histogram **之前**准备好（prometheus_client 创建指标时
# 立即向该目录写文件；目录不存在会直接抛 FileNotFoundError 导致应用启动失败）
_MULTIPROCESS_DIR = config.metrics_multiprocess_dir
if _MULTIPROCESS_DIR:
    os.makedirs(_MULTIPROCESS_DIR, exist_ok=True)

# 指标对象：prometheus-client 是可选依赖，未安装时全部为 None。
# 显式声明成 Optional 是为了让类型检查接受"先赋值 None、成功时再赋值 Counter"的分支，
# 同时把"调用点必须容忍 None"（见 _inc）写进类型里。
_REQUESTS: Optional[Any] = None
_DURATION: Optional[Any] = None
_RATE_LIMITED: Optional[Any] = None
_AUTH_LOGIN_LOCKED: Optional[Any] = None
_TASK_REJECTED: Optional[Any] = None

try:
    from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

    _REQUESTS = Counter(
        'http_requests_total', 'Total HTTP requests',
        ['method', 'status', 'route'],
    )
    _DURATION = Histogram(
        'http_request_duration_seconds', 'HTTP request duration in seconds',
        ['method', 'route'],
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
    )
    # 限流拒绝：scope 只有 path / ip 两个固定值
    _RATE_LIMITED = Counter(
        'http_rate_limited_total', 'HTTP requests rejected by rate limiting',
        ['scope'],
    )
    # 登录锁定拒绝（不按用户名/IP 打标签：高基数，且可能泄漏账号信息）
    _AUTH_LOGIN_LOCKED = Counter(
        'auth_login_locked_total', 'Login attempts refused because of account/IP lockout',
    )
    # 任务未执行即被拒绝/死信：reason 只有固定几个值（见 docs/api-conventions.md）
    _TASK_REJECTED = Counter(
        'task_rejected_total', 'Tasks rejected or dead-lettered before execution',
        ['reason'],
    )
    _METRICS_AVAILABLE = True
except ImportError:
    from flask_server.util import Logger
    Logger.warn('prometheus-client not installed, /metrics disabled (pip install prometheus-client)')
    _METRICS_AVAILABLE = False


_UNMATCHED_ROUTE = '__unmatched__'   # 未匹配任何路由（404 等）时使用固定标签，防高基数


def _inc(counter, **labels):
    """安全自增计数器：指标未启用/依赖缺失/标签非法时静默跳过，绝不让埋点抛错。

    调用点保持干净（无需任何 try/except）；标签必须来自固定集合（如 scope=path/ip、
    reason=unavailable），禁止传入请求路径、用户名等高基数值。

    Args:
        counter: prometheus_client.Counter 实例（依赖缺失时为 None）
        **labels: 该计数器声明的标签（无标签计数器可不传）
    """
    if counter is None or not _METRICS_AVAILABLE or not config.metrics_enabled:
        return
    try:
        # 无标签计数器不能调 labels()（prometheus_client 会抛 ValueError），直接 inc
        if labels:
            counter.labels(**labels).inc()
        else:
            counter.inc()
    except Exception:
        # 埋点失败只影响观测，不能影响业务返回
        pass


def _route_label():
    """使用路由规则而非实际路径（避免用户输入造成指标高基数）；
    未匹配路由（如 404）用固定标签，防止恶意路径撑爆 Prometheus 标签基数"""
    rule = getattr(request, 'url_rule', None)
    if rule is not None:
        return rule.rule
    return _UNMATCHED_ROUTE


def _multiprocess_registry():
    """构造多进程聚合 Registry（读取 PROMETHEUS_MULTIPROC_DIR 下所有 worker 的指标文件）。

    显式传 path：不依赖 collector 自己读环境变量（配置来源统一为 config），
    目录缺失时由调用方决定降级，而不是在 collector 内部抛 ValueError。
    """
    from prometheus_client import CollectorRegistry, multiprocess
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry, path=config.metrics_multiprocess_dir)
    return registry


def metrics_start_timer():
    # METRICS_ENABLED=false 时零开销（仅 /metrics 返回 503，不再统计请求）
    if not config.metrics_enabled:
        return None
    request._metrics_start_time = time.perf_counter()


def metrics_record(resp):
    if not config.metrics_enabled:
        return resp
    route = _route_label()
    status = resp.status_code
    _REQUESTS.labels(method=request.method, status=status, route=route).inc()
    start = getattr(request, '_metrics_start_time', None)
    if start is not None:
        _DURATION.labels(method=request.method, route=route).observe(time.perf_counter() - start)
    return resp


def metrics():
    """Prometheus 指标端点（text/plain 格式；关闭或依赖缺失时返回 503）"""
    if not config.metrics_enabled or not _METRICS_AVAILABLE:
        return 'metrics disabled', 503
    if config.metrics_multiprocess_dir:
        # 多 worker：聚合所有进程的指标文件（否则只返回当前进程的数据）
        payload = generate_latest(_multiprocess_registry())
    else:
        payload = generate_latest()
    # 用 content_type= 而不是 mimetype=：CONTENT_TYPE_LATEST 自带 charset，走 mimetype
    # 会被 Werkzeug 再补一个 → "text/plain; version=1.0.0; charset=utf-8; charset=utf-8"
    return Response(payload, content_type=CONTENT_TYPE_LATEST)


def init_metrics(app):
    """把 /metrics 路由与请求计时/计数钩子注册到指定 app（由 create_app 调用）

    注意：即使 prometheus-client 未安装也要注册路由 —— 否则 /metrics 返回 404，
    与"依赖缺失时优雅降级为 503"的约定不符（编排系统/采集端会把它当成路径写错）。
    此时 metrics() 自身返回 503，不注册任何计时/计数钩子。
    """
    app.add_url_rule('/metrics', 'metrics', metrics, methods=['GET'])
    if not _METRICS_AVAILABLE:
        return
    app.before_request(metrics_start_timer)
    app.after_request(metrics_record)
