"""HTTP 通用能力：ETag / 条件请求（0.7.0 新增）。"""


def test_get_json_response_has_etag(client):
    resp = client.get('/hello')
    assert resp.status_code == 200
    assert resp.headers.get('ETag'), 'JSON 200 响应应带 ETag'


def test_if_none_match_returns_304(client):
    first = client.get('/hello')
    etag = first.headers['ETag']
    second = client.get('/hello', headers={'If-None-Match': etag})
    assert second.status_code == 304
    assert second.headers.get('ETag') == etag
    assert second.get_data() == b''


def test_etag_can_be_disabled(client, monkeypatch):
    from flask_server import config
    monkeypatch.setattr(config, 'etag_enabled', False)
    assert client.get('/hello').headers.get('ETag') is None


def test_post_response_has_no_etag(client):
    resp = client.post('/api/v1/echo', json={'message': 'hi'})
    assert resp.status_code == 200
    assert resp.headers.get('ETag') is None, 'ETag 只用于可缓存的 GET/HEAD 响应'
