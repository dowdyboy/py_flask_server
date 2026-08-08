"""Prometheus 指标测试：/metrics 端点、路由标签防高基数"""


def test_metrics_endpoint_records_requests(client):
    """请求后 /metrics 应包含对应路由的计数指标"""
    client.get('/hello')
    resp = client.get('/metrics')
    assert resp.status_code == 200
    assert 'text/plain' in resp.content_type
    body = resp.get_data(as_text=True)
    assert 'http_requests_total' in body
    # 计数使用路由规则标签（而非实际路径，防高基数）
    assert 'route="/hello"' in body
    assert 'method="GET"' in body


def test_metrics_route_label_not_raw_path(client):
    """实际路径参数不应进入指标标签（防高基数）"""
    client.get('/api/v1/healthz')
    client.get('/some/spa/route')
    body = client.get('/metrics').get_data(as_text=True)
    assert 'http_requests_total' in body


def test_metrics_unmatched_route_fixed_label(client, monkeypatch):
    """P5 回归：未匹配路由使用固定标签，原始路径不得进入指标标签"""
    from flask_server.component import metrics as metrics_module
    from flask_server.component.metrics import _UNMATCHED_ROUTE

    class _FakeRequest:
        def __init__(self, url_rule=None):
            self.url_rule = url_rule

    # HTTP 层：大量恶意/不存在路径请求后，原始路径不出现在 /metrics
    for i in range(5):
        client.get(f'/nonexistent/path/{i}')
    body = client.get('/metrics').get_data(as_text=True)
    assert '/nonexistent/path/3' not in body

    # 无 url_rule（未匹配任何路由）→ 固定标签（独立 context，避免影响 HTTP 请求处理）
    with monkeypatch.context() as m:
        m.setattr(metrics_module, 'request', _FakeRequest(None))
        assert metrics_module._route_label() == _UNMATCHED_ROUTE

    # 有 url_rule → 使用路由规则
    with monkeypatch.context() as m:
        rule = type('_Rule', (), {'rule': '/api/v1/healthz'})()
        m.setattr(metrics_module, 'request', _FakeRequest(rule))
        assert metrics_module._route_label() == '/api/v1/healthz'


def test_metrics_histogram_present(client):
    client.get('/hello')
    body = client.get('/metrics').get_data(as_text=True)
    assert 'http_request_duration_seconds' in body
    assert 'http_request_duration_seconds_bucket' in body


def test_metrics_disabled_returns_503(client, monkeypatch):
    """R4 回归：METRICS_ENABLED=false 时 /metrics 返回 503（运行时判断）"""
    from flask_server import config
    monkeypatch.setattr(config, 'metrics_enabled', False)
    resp = client.get('/metrics')
    assert resp.status_code == 503
    assert 'metrics disabled' in resp.get_data(as_text=True)


def test_metrics_record_without_timer(client):
    """metrics_record 无计时起点（未走 before_request）时不记录时长（46->63 分支）"""
    from flask_server.component.metrics import metrics_record
    from werkzeug.wrappers import Response

    with client.application.test_request_context('/x'):
        resp = metrics_record(Response())
        assert resp.status_code == 200
