import time

from flask import request, Response

from flask_server import app, config

# Prometheus 指标组件（METRICS_ENABLED=true 默认开启）
# 暴露 /metrics（Prometheus 文本格式），指标：
#   - http_requests_total{method,status,route}     请求总数（按路由聚合，防高基数）
#   - http_request_duration_seconds_bucket/...      请求延迟直方图
# 未安装 prometheus-client 时优雅降级（/metrics 返回 503 并告警一次）

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


if _METRICS_AVAILABLE and config.metrics_enabled:

    @app.before_request
    def metrics_start_timer():
        request._metrics_start_time = time.perf_counter()

    @app.after_request
    def metrics_record(resp):
        route = _route_label()
        status = resp.status_code
        _REQUESTS.labels(method=request.method, status=status, route=route).inc()
        start = getattr(request, '_metrics_start_time', None)
        if start is not None:
            _DURATION.labels(method=request.method, route=route).observe(time.perf_counter() - start)
        return resp

    @app.route('/metrics')
    def metrics():
        """Prometheus 指标端点（text/plain 格式）"""
        return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)
else:
    from flask_server.util import Logger

    if not _METRICS_AVAILABLE:
        Logger.warn('prometheus-client not installed, /metrics disabled (pip install prometheus-client)')

    @app.route('/metrics')
    def metrics():
        return 'metrics disabled', 503
