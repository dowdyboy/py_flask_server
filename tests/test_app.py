"""HTTP 层集成测试：验证统一响应格式、422 校验格式、404、request_id 等"""

from flask import request
from werkzeug.exceptions import abort


def _probe(app, hook):
    """临时注册 before_request 探针钩子（绕过已处理请求的限制），测试后移除"""
    app.before_request_funcs.setdefault(None, []).append(hook)

    def teardown():
        app.before_request_funcs[None].remove(hook)

    return teardown


def test_hello_unified_json(client):
    resp = client.get('/hello')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['code'] == 0
    assert body['msg'] == '成功'
    assert body['data']['message'] == 'Hello, World!'


def test_health(client):
    resp = client.get('/api/v1/health')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['code'] == 0
    assert body['data']['status'] == 'up'
    assert 'version' in body['data']
    assert 'uptime' in body['data']


def test_echo_ok(client):
    resp = client.post('/api/v1/echo', json={'message': 'hi'})
    assert resp.status_code == 200
    assert resp.get_json()['data']['echo'] == 'hi'


def test_echo_validation_422_unified(client):
    """参数校验失败返回 422 + 统一 GraceResult 格式，且保留字段级错误"""
    resp = client.post('/api/v1/echo', json={})
    assert resp.status_code == 422
    body = resp.get_json()
    assert body['code'] == 1001
    assert body['msg'] == '参数错误'
    # webargs 错误结构：{"json": {"message": [...]}}
    assert 'message' in body['data']['json']


def test_404_unified(client):
    resp = client.get('/api/nonexistent')
    assert resp.status_code == 404
    body = resp.get_json()
    assert body['code'] == -1
    assert body['data'] == '资源不存在'


def test_request_id_hooks_registered():
    """request_id 链路追踪的钩子应在应用启动时注册"""
    from flask_server import app
    hooks = [f.__name__ for f in app.before_request_funcs.get(None, [])]
    assert 'init_request_info' in hooks
    assert 'clear_request_id' in [f.__name__ for f in app.teardown_request_funcs.get(None, [])]


def test_request_id_response_header(client):
    """响应应回写 X-Request-Id（透传客户端提供的值）"""
    resp = client.get('/hello', headers={'X-Request-Id': 'my-trace-id'})
    assert resp.headers.get('X-Request-Id') == 'my-trace-id'


def test_request_id_response_header_auto_generated(client):
    """未提供请求头时自动生成 X-Request-Id"""
    resp = client.get('/hello')
    rid = resp.headers.get('X-Request-Id')
    assert rid is not None
    assert len(rid) == 32   # uuid4().hex


def test_docs_page(client):
    resp = client.get('/docs')
    assert resp.status_code == 200


def test_openapi_json(client):
    resp = client.get('/openapi.json')
    assert resp.status_code == 200
    assert resp.get_json()['openapi'] == '3.0.3'


def test_security_headers(client):
    """安全响应头应默认启用"""
    resp = client.get('/hello')
    assert resp.headers.get('X-Content-Type-Options') == 'nosniff'
    assert resp.headers.get('X-Frame-Options') == 'DENY'
    assert resp.headers.get('Referrer-Policy') == 'no-referrer'
    assert resp.headers.get('Content-Security-Policy') is not None


def test_security_headers_can_be_disabled(client, monkeypatch):
    """SECURITY_HEADERS_ENABLED=false 时不再注入安全头"""
    from flask_server import config
    monkeypatch.setattr(config, 'security_headers_enabled', False)
    resp = client.get('/hello')
    assert resp.headers.get('X-Content-Type-Options') is None
    assert resp.headers.get('X-Frame-Options') is None


def test_internal_error_500(client, monkeypatch):
    """未捕获异常 → 500 + 统一 GraceResult 格式"""
    from flask_server import app
    monkeypatch.setitem(app.config, 'TESTING', False)   # TESTING 下异常会传播，不走 500

    def boom():
        raise ValueError('mock internal error')

    teardown = _probe(app, boom)
    try:
        resp = client.get('/hello')
        assert resp.status_code == 500
        body = resp.get_json()
        assert body['code'] == -1
        assert body['msg'] == '接口发生错误'
    finally:
        teardown()
        monkeypatch.setitem(app.config, 'TESTING', True)


def test_key_error_400(client):
    """KeyError → 400 + 参数错误"""
    from flask_server import app

    def missing_key():
        raise KeyError('username')

    teardown = _probe(app, missing_key)
    try:
        resp = client.get('/hello')
        assert resp.status_code == 400
        body = resp.get_json()
        assert body['code'] == 1001
        assert body['msg'] == '参数错误'
    finally:
        teardown()


def test_http_exception_custom_code(client):
    """abort 抛出的 HTTP 异常保留原状态码"""
    from flask_server import app

    def teapot():
        abort(418)

    teardown = _probe(app, teapot)
    try:
        resp = client.get('/hello')
        assert resp.status_code == 418
        body = resp.get_json()
        assert body['code'] == -1
    finally:
        teardown()


def test_form_urlencoded_payload(client):
    """application/x-www-form-urlencoded 的 body 应解析到 request.payload"""
    from flask_server import app
    captured = {}

    def capture():
        captured['payload'] = request.payload

    teardown = _probe(app, capture)
    try:
        client.post('/api/v1/echo', data={'message': 'from-form'},
                    content_type='application/x-www-form-urlencoded')
        assert captured['payload'] == {'message': 'from-form'}
    finally:
        teardown()


def test_multipart_payload_with_file(client):
    """multipart/form-data 应合并 form 字段与文件到 request.payload"""
    import io
    from werkzeug.datastructures import FileStorage
    from flask_server import app
    captured = {}

    def capture():
        file_field = request.payload['file']
        captured['message'] = request.payload['message']
        captured['filename'] = file_field.filename
        captured['content'] = file_field.read()

    teardown = _probe(app, capture)
    try:
        client.post('/api/v1/echo',
                    data={'message': 'hi',
                          'file': FileStorage(stream=io.BytesIO(b'file-content'),
                                              filename='a.txt')},
                    content_type='multipart/form-data')
        assert captured['message'] == 'hi'
        assert captured['filename'] == 'a.txt'
        assert captured['content'] == b'file-content'
    finally:
        teardown()


def test_put_json_payload(client):
    """PUT 携带 JSON body 应解析到 request.payload"""
    from flask_server import app
    captured = {}

    def capture():
        captured['payload'] = request.payload

    teardown = _probe(app, capture)
    try:
        client.put('/api/nonexistent', json={'a': 1})
        assert captured['payload'] == {'a': 1}
    finally:
        teardown()


def test_json_response_decorator():
    """json_response 装饰器：None / tuple / 普通对象 三个分支"""
    from flask_server.app import json_response
    from flask_server.util import GraceResult

    @json_response
    def returns_none():
        return None

    @json_response
    def returns_tuple():
        return None, 201

    @json_response
    def returns_ok():
        return GraceResult.ok({'x': 1})

    assert returns_none() == {'code': 0, 'msg': '成功', 'data': None}
    assert returns_tuple()[1] == 201
    assert returns_ok()['data'] == {'x': 1}


def test_spa_fallback_returns_index(client):
    """无扩展名的路径回退到 index.html（SPA）"""
    resp = client.get('/some/spa/route')
    assert resp.status_code == 200
    assert 'Flask' in resp.get_data(as_text=True)


def test_healthz_liveness(client):
    """存活探针：恒 200，不检查依赖"""
    resp = client.get('/api/v1/healthz')
    assert resp.status_code == 200
    assert resp.get_json()['data']['status'] == 'up'


def test_readyz_no_dependencies(client):
    """未配置 DB/Redis 时就绪探针返回 200"""
    from flask_server.module import sqlalchemy, redis_cache
    assert sqlalchemy() is None
    assert redis_cache is None
    resp = client.get('/api/v1/readyz')
    assert resp.status_code == 200
    assert resp.get_json()['data']['status'] == 'ready'


def test_readyz_db_failure_returns_503(client, monkeypatch):
    """DB 依赖故障时就绪探针返回 503 + 统一格式"""
    from importlib import import_module
    sa_mod = import_module('flask_server.module.sqlalchemy')
    from flask_server import config

    class _FakeDb:
        class _Engine:
            def connect(self):
                raise RuntimeError('mock db down')

        engine = _Engine()
        text = lambda s: s   # noqa: E731

    monkeypatch.setattr(sa_mod, 'db', _FakeDb())
    monkeypatch.setattr(config, 'sqlalchemy_uri', 'mysql+pymysql://x:y@z/db')
    resp = client.get('/api/v1/readyz')
    assert resp.status_code == 503
    body = resp.get_json()
    assert body['code'] == 5030
    assert body['data']['db'] == 'error'


def test_readyz_redis_failure_returns_503(client, monkeypatch):
    """Redis 依赖故障时就绪探针返回 503"""
    from flask_server import config

    class _FakeClient:
        def ping(self):
            raise ConnectionError('mock redis down')

    class _FakeCache:
        client = _FakeClient()

    # 默认 redis_cache 包属性为 None（未配置），此处注入假实例触发故障路径
    monkeypatch.setattr('flask_server.module.redis_cache', _FakeCache())
    monkeypatch.setattr(config, 'redis_url', 'redis://localhost:6379/0')
    resp = client.get('/api/v1/readyz')
    assert resp.status_code == 503
    assert resp.get_json()['data']['redis'] == 'error'
