import os
import time

from flask import request, Response

from flask_server import app, config

# Prometheus 指标组件（METRICS_ENABLED=true 默认开启）
# 暴露 /metrics（Prometheus 文本格式），指标：
#   - http_requests_total{method,status,route}     请求总数（按路由聚合，防高基数）
#   - http_request_duration_seconds_bucket/...      请求延迟直方图
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
    _METRICS_AVAILABLE = True
except ImportError:
    from flask_server.util import Logger
    Logger.warn('prometheus-client not installed, /metrics disabled (pip install prometheus-client)')
    _METRICS_AVAILABLE = False
    _REQUESTS = None
    _DURATION = None


_UNMATCHED_ROUTE = '__unmatched__'   # 未匹配任何路由（404 等）时使用固定标签，防高基数


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


if _METRICS_AVAILABLE:

    @app.before_request
    def metrics_start_timer():
        # METRICS_ENABLED=false 时零开销（仅 /metrics 返回 503，不再统计请求）
        if not config.metrics_enabled:
            return None
        request._metrics_start_time = time.perf_counter()

    @app.after_request
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


@app.route('/metrics')
def metrics():
    """Prometheus 指标端点（text/plain 格式；关闭或依赖缺失时返回 503）"""
    if not config.metrics_enabled or not _METRICS_AVAILABLE:
        return 'metrics disabled', 503
    if config.metrics_multiprocess_dir:
        # 多 worker：聚合所有进程的指标文件（否则只返回当前进程的数据）
        payload = generate_latest(_multiprocess_registry())
    else:
        payload = generate_latest()
    return Response(payload, mimetype=CONTENT_TYPE_LATEST)
