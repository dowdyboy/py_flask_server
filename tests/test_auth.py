"""认证模块测试：注册/登录/登出/me + 全局拦截器逻辑"""

import pytest

from flask_server.component.auth import AuthService, auth_interceptor


@pytest.fixture
def fresh_store():
    """每个用例重置内存用户存储，避免用例间污染"""
    AuthService._store = None
    yield
    AuthService._store = None
    # 清理残留 token
    from flask_server.module import memory_cache
    for k in list(memory_cache.cache.keys()):
        if k.startswith('auth:token:'):
            memory_cache.delete(k)


def test_register_and_login(client, fresh_store):
    r = client.post('/api/v1/auth/register', json={'username': 'bob', 'password': 'secret123'})
    assert r.status_code == 200
    assert r.get_json()['code'] == 0
    assert 'uid' in r.get_json()['data']

    r = client.post('/api/v1/auth/login', json={'username': 'bob', 'password': 'secret123'})
    assert r.status_code == 200
    data = r.get_json()['data']
    assert data['username'] == 'bob'
    assert len(data['token']) == 32


def test_register_duplicate_username(client, fresh_store):
    client.post('/api/v1/auth/register', json={'username': 'carol', 'password': 'secret123'})
    r = client.post('/api/v1/auth/register', json={'username': 'carol', 'password': 'other456'})
    assert r.status_code == 400
    assert r.get_json()['code'] == 4001
    assert r.get_json()['msg'] == '用户名已存在'


def test_login_wrong_password(client, fresh_store):
    client.post('/api/v1/auth/register', json={'username': 'dave', 'password': 'secret123'})
    r = client.post('/api/v1/auth/login', json={'username': 'dave', 'password': 'wrong-pass'})
    assert r.status_code == 401
    assert r.get_json()['code'] == 4001


def test_login_unknown_user(client, fresh_store):
    r = client.post('/api/v1/auth/login', json={'username': 'nobody', 'password': 'whatever'})
    assert r.status_code == 401


def test_me_requires_token(client, fresh_store):
    r = client.get('/api/v1/auth/me')
    assert r.status_code == 401
    assert r.get_json()['code'] == 4002


def test_me_with_token(client, fresh_store):
    client.post('/api/v1/auth/register', json={'username': 'erin', 'password': 'secret123'})
    token = client.post('/api/v1/auth/login', json={'username': 'erin', 'password': 'secret123'}).get_json()['data']['token']
    r = client.get('/api/v1/auth/me', headers={'X-AUTH-TOKEN': token})
    assert r.status_code == 200
    data = r.get_json()['data']
    assert data['username'] == 'erin'
    # 绝不能泄露密码哈希
    assert 'passwd' not in data


def test_logout_invalidates_token(client, fresh_store):
    client.post('/api/v1/auth/register', json={'username': 'frank', 'password': 'secret123'})
    token = client.post('/api/v1/auth/login', json={'username': 'frank', 'password': 'secret123'}).get_json()['data']['token']
    assert client.get('/api/v1/auth/me', headers={'X-AUTH-TOKEN': token}).status_code == 200
    client.post('/api/v1/auth/logout', headers={'X-AUTH-TOKEN': token})
    assert client.get('/api/v1/auth/me', headers={'X-AUTH-TOKEN': token}).status_code == 401


def test_auth_interceptor_blocks_unexempt_api(client, monkeypatch, fresh_store):
    """全局拦截：未豁免 /api/ 路径无 token → 401；豁免路径放行"""
    from flask_server import app

    client.post('/api/v1/auth/register', json={'username': 'grace', 'password': 'secret123'})
    token = client.post('/api/v1/auth/login', json={'username': 'grace', 'password': 'secret123'}).get_json()['data']['token']

    # 手动挂载拦截器（等价于 AUTH_ENABLED=true 时注册的效果）
    app.before_request_funcs.setdefault(None, []).append(auth_interceptor)
    try:
        # 未豁免的 /api/ 路径：无 token → 401
        r = client.post('/api/v1/echo', json={'message': 'hi'})
        assert r.status_code == 401
        assert r.get_json()['code'] == 4002
        # 未豁免的 /api/ 路径：带 token → 放行
        r = client.post('/api/v1/echo', json={'message': 'hi'}, headers={'X-AUTH-TOKEN': token})
        assert r.status_code == 200
        # 豁免路径（auth/健康检查端点）无 token 也放行
        r = client.get('/api/v1/readyz')
        assert r.status_code == 200
        r = client.get('/docs')
        assert r.status_code == 200
    finally:
        app.before_request_funcs[None].remove(auth_interceptor)


def test_login_required_decorator(fresh_store):
    """login_required：无 token 时返回 401 响应"""
    from flask_server.app import app
    from flask_server.component.auth import login_required

    @login_required
    def protected():
        return 'ok'

    with app.test_request_context('/x'):
        resp, status = protected()
        assert status == 401
