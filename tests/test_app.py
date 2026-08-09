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


def test_controllers_auto_registered():
    """controller 自动发现注册：新建模块的 blp 无需手动注册即生效"""
    from flask_server import app
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert '/api/v1/auth/login' in rules
    assert '/api/v1/echo' in rules
    assert '/api/v1/healthz' in rules
    assert '/metrics' in rules


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


def test_security_headers_csp_docs_vs_others(client):
    """P9 回归：仅 /docs 放行 CDN/内联脚本，其余路径 CSP 收紧"""
    resp = client.get('/hello')
    csp = resp.headers.get('Content-Security-Policy')
    assert 'script-src' in csp
    assert 'cdn.jsdelivr.net' not in csp
    # 收紧：脚本不允许内联（style 的 unsafe-inline 保留，webui 内联样式常用）
    script_part = csp.split('script-src')[1].split(';')[0]
    assert 'unsafe-inline' not in script_part

    resp = client.get('/docs')
    assert resp.status_code == 200
    docs_csp = resp.headers.get('Content-Security-Policy')
    assert 'cdn.jsdelivr.net' in docs_csp


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


def test_non_dict_json_payload_normalized(client, monkeypatch):
    """顶层 JSON 为数组/字符串时应归一为空 dict（视图字段访问走 KeyError → 400 而非 500）"""
    from flask_server import app
    monkeypatch.setitem(app.config, 'TESTING', False)   # 让 500 走 errorhandler

    def missing_key():
        return request.payload['username']

    teardown = _probe(app, missing_key)
    try:
        r = client.post('/api/v1/echo', data='[1, 2, 3]', content_type='application/json')
        assert r.status_code == 400
        assert r.get_json()['code'] == 1001

        r = client.post('/api/v1/echo', data='"hello"', content_type='application/json')
        assert r.status_code == 400
        assert r.get_json()['code'] == 1001
    finally:
        teardown()
        monkeypatch.setitem(app.config, 'TESTING', True)


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


def test_json_response_passes_through_response():
    """json_response 应原样透传 Response 对象（send_file/redirect 场景），不得序列化"""
    from flask import Response
    from flask_server.app import json_response

    @json_response
    def returns_response():
        return Response(b'file-content', status=200, mimetype='application/octet-stream')

    @json_response
    def returns_response_tuple():
        return Response('created', status=201), 201

    resp = returns_response()
    assert isinstance(resp, Response)
    assert resp.get_data() == b'file-content'

    resp, status = returns_response_tuple()
    assert isinstance(resp, Response)
    assert status == 201


def test_json_response_three_tuple():
    """json_response 应支持 Flask 3 元组 (data, status, headers)"""
    from flask import Response
    from flask_server.app import json_response

    @json_response
    def returns_three_tuple():
        return {'a': 1}, 201, {'X-Custom': 'v'}

    @json_response
    def returns_three_tuple_with_response():
        return Response('ok'), 202, {'X-Custom': 'v2'}

    data, status, headers = returns_three_tuple()
    assert data == {'a': 1}
    assert status == 201
    assert headers == {'X-Custom': 'v'}

    resp, status, headers = returns_three_tuple_with_response()
    assert isinstance(resp, Response)
    assert status == 202
    assert headers == {'X-Custom': 'v2'}


def test_500_hides_internal_detail_in_production(client, monkeypatch):
    """非 development 环境 500 不应向客户端回显内部异常详情"""
    from flask_server import app, config
    monkeypatch.setattr(config, 'app_env', 'production')
    monkeypatch.setitem(app.config, 'TESTING', False)   # TESTING 下异常会传播，不走 500

    def boom():
        raise ValueError('secret-db-connection-string')

    teardown = _probe(app, boom)
    try:
        resp = client.get('/hello')
        assert resp.status_code == 500
        body = resp.get_json()
        assert body['code'] == -1
        assert 'secret-db-connection-string' not in body['data']
        assert body['data'] == '服务器内部错误'
    finally:
        teardown()
        monkeypatch.setitem(app.config, 'TESTING', True)


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

    class _FakeCache:
        def ping(self):
            raise ConnectionError('mock redis down')

    # 默认 redis_cache 包属性为 None（未配置），此处注入假实例触发故障路径
    monkeypatch.setattr('flask_server.module.redis_cache', _FakeCache())
    monkeypatch.setattr(config, 'redis_url', 'redis://localhost:6379/0')
    resp = client.get('/api/v1/readyz')
    assert resp.status_code == 503
    assert resp.get_json()['data']['redis'] == 'error'


# ------------------------- 健康检查故障/成功分支补测 -------------------------


class _FakeDbOk:
    """engine.connect 正常的假 db"""

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, *args, **kwargs):
            return None

    class _Engine:
        @staticmethod
        def connect():
            return _FakeDbOk._Conn()

    engine = _Engine()

    @staticmethod
    def text(s):
        return s


class _FakeRedisOk:
    """ping 正常的假 redis_cache（模拟 RedisCache.ping 返回 True）"""

    @staticmethod
    def ping():
        return True


def _patch_db(monkeypatch, fake):
    """注入假 db（替换 sqlalchemy 子模块的 db 变量，sqlalchemy() 返回它）"""
    from importlib import import_module
    sa_mod = import_module('flask_server.module.sqlalchemy')
    monkeypatch.setattr(sa_mod, 'db', fake)


def test_health_db_failure(client, monkeypatch):
    """health 的 DB 检查故障分支：data.db 含 error"""
    from importlib import import_module
    sa_mod = import_module('flask_server.module.sqlalchemy')

    class _BrokenEngine:
        @staticmethod
        def connect():
            raise RuntimeError('mock db down')

    class _BrokenDb:
        engine = _BrokenEngine()

        @staticmethod
        def text(s):
            return s

    monkeypatch.setattr(sa_mod, 'db', _BrokenDb())
    resp = client.get('/api/v1/health')
    body = resp.get_json()['data']
    assert body['db'].startswith('error'), body


def test_health_all_ok(client, monkeypatch):
    """health 的 DB/Redis 检查成功分支：db=ok、redis=ok"""
    _patch_db(monkeypatch, _FakeDbOk())
    monkeypatch.setattr('flask_server.module.redis_cache', _FakeRedisOk())
    resp = client.get('/api/v1/health')
    body = resp.get_json()['data']
    assert body['db'] == 'ok', body
    assert body['redis'] == 'ok', body


def test_health_redis_failure(client, monkeypatch):
    """health 的 Redis 检查故障分支：data.redis 含 error"""
    _patch_db(monkeypatch, _FakeDbOk())

    class _BrokenCache:
        def ping(self):
            raise ConnectionError('mock redis down')

    monkeypatch.setattr('flask_server.module.redis_cache', _BrokenCache())
    resp = client.get('/api/v1/health')
    body = resp.get_json()['data']
    assert body['redis'].startswith('error'), body


def test_readyz_db_ok(client, monkeypatch):
    """readyz 的 DB 检查成功路径：200 + db=ok"""
    _patch_db(monkeypatch, _FakeDbOk())
    resp = client.get('/api/v1/readyz')
    assert resp.status_code == 200
    assert resp.get_json()['data']['db'] == 'ok'


def test_readyz_redis_ok(client, monkeypatch):
    """readyz 的 Redis 检查成功路径：200 + redis=ok"""
    _patch_db(monkeypatch, _FakeDbOk())
    monkeypatch.setattr('flask_server.module.redis_cache', _FakeRedisOk())
    resp = client.get('/api/v1/readyz')
    assert resp.status_code == 200
    assert resp.get_json()['data']['redis'] == 'ok'


# ------------------------- 防御分支直接调用补测 -------------------------


def test_init_request_info_preserves_existing(client):
    """request.info 已存在时不覆盖（hasattr 分支）"""
    from flask import request
    from flask_server.app import init_request_info

    with client.application.test_request_context('/x'):
        request.info = {'existing': True}
        init_request_info()
        assert request.info['existing'] is True
        assert 'request_id' in request.info


def test_set_request_id_header_without_rid(client):
    """无 request_id 时不回写 X-Request-Id（else 分支）"""
    from flask_server.app import set_request_id_header
    from werkzeug.wrappers import Response

    with client.application.test_request_context('/x'):
        resp = set_request_id_header(Response())
        assert 'X-Request-Id' not in resp.headers


def test_all_exception_handler_http_exception_fallback(client):
    """HTTPException 落入兜底 handler 时保留原状态码（209-210 分支）"""
    from flask_server.app import all_exception_handler
    from flask_server.util import GraceResult
    from werkzeug.exceptions import NotFound

    with client.application.test_request_context('/x'):
        obj, status = all_exception_handler(NotFound())
        assert status == 404
        assert obj['code'] == GraceResult.INNER_ERROR
