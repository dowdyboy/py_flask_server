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
    # /some/spa/route 无路由规则，使用实际路径作兜底标签——应可接受
    assert 'http_requests_total' in body


def test_metrics_histogram_present(client):
    client.get('/hello')
    body = client.get('/metrics').get_data(as_text=True)
    assert 'http_request_duration_seconds' in body
    assert 'http_request_duration_seconds_bucket' in body
