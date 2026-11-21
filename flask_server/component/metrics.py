"""Prometheus 指标组件（按 app 隔离，0.9 起）。

每个 app 在 `app.extensions['flask_server.metrics']` 持有自己的 `MetricsBundle`
（独立 CollectorRegistry + Counter/Histogram），因此同进程多个 app 的指标不会串在
默认 registry 里；无 app 上下文时使用进程默认 bundle（脚本/测试直调）。

指标：
  - http_requests_total{method,status,route}
  - http_request_duration_seconds_bucket/...
  - http_rate_limited_total{scope}
  - auth_login_locked_total
  - task_processed_total{result}
  - task_rejected_total{reason}

标签值全部来自固定集合（不取用户输入），低基数；业务代码统一用
`inc_*` 函数（指标未启用/依赖缺失时自动跳过，绝不影响主流程）。

多进程（gunicorn）：设 `PROMETHEUS_MULTIPROC_DIR`，/metrics 走 MultiProcessCollector
聚合；单进程下用当前 app 的 registry。
"""

import os
import time
from typing import Any, Optional

from flask import current_app, has_app_context, request, Response

from ..config import config
from ..module.container import METRICS_EXT_KEY

_METRICS_AVAILABLE = False
try:
    from prometheus_client import (
        CollectorRegistry, Counter, Histogram, CONTENT_TYPE_LATEST, generate_latest,
    )
    _METRICS_AVAILABLE = True
except ImportError:
    from ..util import Logger
    Logger.warn('prometheus-client not installed, /metrics disabled '
                '(pip install prometheus-client)')

_UNMATCHED_ROUTE = '__unmatched__'   # 未匹配路由（404 等）的固定标签，防高基数


class MetricsBundle:
    """单个 app 的指标集合（独立 registry，避免多 app 在默认 registry 中重名冲突）"""

    def __init__(self, multiprocess_dir: Optional[str] = None):
        self.available = _METRICS_AVAILABLE
        self.registry = None
        self.requests: Optional[Any] = None
        self.duration: Optional[Any] = None
        self.rate_limited: Optional[Any] = None
        self.login_locked: Optional[Any] = None
        self.task_rejected: Optional[Any] = None
        self.task_processed: Optional[Any] = None
        if not self.available:
            return
        # 多进程目录必须在创建 Counter/Histogram 之前存在
        if multiprocess_dir:
            os.makedirs(multiprocess_dir, exist_ok=True)
        self.registry = CollectorRegistry()
        self.requests = Counter(
            'http_requests_total', 'Total HTTP requests',
            ['method', 'status', 'route'], registry=self.registry,
        )
        self.duration = Histogram(
            'http_request_duration_seconds', 'HTTP request duration in seconds',
            ['method', 'route'],
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
            registry=self.registry,
        )
        self.rate_limited = Counter(
            'http_rate_limited_total', 'HTTP requests rejected by rate limiting',
            ['scope'], registry=self.registry,
        )
        self.login_locked = Counter(
            'auth_login_locked_total', 'Login attempts refused because of account/IP lockout',
            registry=self.registry,
        )
        self.task_rejected = Counter(
            'task_rejected_total', 'Tasks rejected or dead-lettered before execution',
            ['reason'], registry=self.registry,
        )
        self.task_processed = Counter(
            'task_processed_total', 'Tasks processed by worker', ['result'],
            registry=self.registry,
        )


_default_bundle: Optional[MetricsBundle] = None


def _get_bundle() -> Optional[MetricsBundle]:
    """当前 app 的指标 bundle；无 app 上下文时回退进程默认 bundle"""
    global _default_bundle
    if has_app_context():
        bundle = current_app.extensions.get(METRICS_EXT_KEY)
        if bundle is not None:
            return bundle
    if _default_bundle is None:
        _default_bundle = MetricsBundle(config.metrics_multiprocess_dir)
    return _default_bundle


def _inc(counter, **labels):
    """安全自增计数器：指标未启用/依赖缺失/标签非法时静默跳过，绝不让埋点抛错。

    标签必须来自固定集合（如 scope=path/ip、reason=unavailable），禁止传入请求路径、
    用户名等高基数值。
    """
    if counter is None or not _METRICS_AVAILABLE or not config.metrics_enabled:
        return
    try:
        if labels:
            counter.labels(**labels).inc()
        else:
            counter.inc()
    except Exception:
        # 埋点失败只影响观测，不能影响业务返回
        pass


def inc_rate_limited(scope):
    bundle = _get_bundle()
    _inc(bundle.rate_limited if bundle else None, scope=scope)


def inc_login_locked():
    bundle = _get_bundle()
    _inc(bundle.login_locked if bundle else None)


def inc_task_rejected(reason):
    bundle = _get_bundle()
    _inc(bundle.task_rejected if bundle else None, reason=reason)


def inc_task_processed(result):
    bundle = _get_bundle()
    _inc(bundle.task_processed if bundle else None, result=result)


def _route_label():
    """使用路由规则而非实际路径（避免用户输入造成指标高基数）；
    未匹配路由（如 404）用固定标签，防止恶意路径撑爆 Prometheus 标签基数"""
    rule = getattr(request, 'url_rule', None)
    if rule is not None:
        return rule.rule
    return _UNMATCHED_ROUTE


def _multiprocess_registry():
    """构造多进程聚合 Registry（读取 PROMETHEUS_MULTIPROC_DIR 下所有 worker 的指标文件）。"""
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
    bundle = _get_bundle()
    if not config.metrics_enabled or not _METRICS_AVAILABLE or bundle is None:
        return resp
    if bundle.requests is None or bundle.duration is None:
        return resp
    route = _route_label()
    status = resp.status_code
    bundle.requests.labels(method=request.method, status=status, route=route).inc()
    start = getattr(request, '_metrics_start_time', None)
    if start is not None:
        bundle.duration.labels(method=request.method, route=route).observe(
            time.perf_counter() - start)
    return resp


def metrics():
    """Prometheus 指标端点（text/plain 格式；关闭或依赖缺失时返回 503）"""
    bundle = _get_bundle()
    if not config.metrics_enabled or not _METRICS_AVAILABLE or bundle is None:
        return 'metrics disabled', 503
    if config.metrics_multiprocess_dir:
        # 多 worker：聚合所有进程的指标文件（否则只返回当前进程的数据）
        payload = generate_latest(_multiprocess_registry())
    else:
        payload = generate_latest(bundle.registry)
    # 用 content_type= 而不是 mimetype=：CONTENT_TYPE_LATEST 自带 charset，走 mimetype
    # 会被 Werkzeug 再补一个 → "text/plain; version=1.0.0; charset=utf-8; charset=utf-8"
    return Response(payload, content_type=CONTENT_TYPE_LATEST)


def init_metrics(app, cfg=None):
    """为指定 app 创建指标 bundle、注册 /metrics 与计时/计数钩子。

    即使 prometheus-client 未安装也要注册路由（否则 /metrics 是 404 而非承诺的 503）。
    """
    cfg = cfg if cfg is not None else config
    app.extensions[METRICS_EXT_KEY] = MetricsBundle(cfg.metrics_multiprocess_dir)
    app.add_url_rule('/metrics', 'metrics', metrics, methods=['GET'])
    if not _METRICS_AVAILABLE:
        return
    app.before_request(metrics_start_timer)
    app.after_request(metrics_record)
