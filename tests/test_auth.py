"""认证模块测试：注册/登录/登出/me + 全局拦截器逻辑"""

import pytest

from flask_server.component.auth import AuthService, auth_interceptor


@pytest.fixture
def fresh_store():
    """每个用例重置内存用户存储与认证缓存，避免用例间污染"""
    _clear_auth_cache()
    AuthService._store = None
    yield
    AuthService._store = None
    # 清理残留 token / 失败计数 / 锁定标记
    _clear_auth_cache()


def _clear_auth_cache():
    """清空内存缓存中的认证相关键（sqlalchemy 存储测试直接调用 login 会残留 token）"""
    from flask_server.module import memory_cache
    for k in list(memory_cache.cache.keys()):
        if k.startswith(('auth:token:', 'auth:refresh:', 'auth:login_fail:', 'auth:login_lock:')):
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
    assert len(data['refresh_token']) == 32


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


def test_login_oversized_password_rejected(client, fresh_store):
    """P7 回归：登录密码超长（>128）返回 422，避免 PBKDF2 CPU DoS"""
    r = client.post('/api/v1/auth/login',
                    json={'username': 'nobody', 'password': 'x' * 10000})
    assert r.status_code == 422
    assert r.get_json()['code'] == 1001


def test_auth_exempt_path_exact_match(fresh_store):
    """P8 回归：豁免路径为整段匹配，前缀相似路径不得被误豁免"""
    from flask_server.component.auth import _is_exempt_path

    # 正常豁免
    assert _is_exempt_path('/api/v1/auth/login')
    assert _is_exempt_path('/api/v1/auth/refresh')
    assert _is_exempt_path('/api/v1/healthz')
    assert _is_exempt_path('/docs')
    assert _is_exempt_path('/docs/')
    assert _is_exempt_path('/openapi.json')
    # 前缀相似但非豁免：修复前被 startswith 误豁免
    assert not _is_exempt_path('/docsanything')
    assert not _is_exempt_path('/api/v1/authx/login')
    assert not _is_exempt_path('/api/v1/healthz_extra')
    assert not _is_exempt_path('/api/v1/healthx')
    assert not _is_exempt_path('/api/v1/health_extra')


def test_login_locks_after_max_fails(client, fresh_store, monkeypatch):
    """连续失败达阈值后锁定，锁定期间正确密码也拒绝"""
    from flask_server import config
    monkeypatch.setattr(config, 'auth_login_max_fails', 3)
    monkeypatch.setattr(config, 'auth_login_lock_seconds', 60)
    client.post('/api/v1/auth/register', json={'username': 'hank', 'password': 'secret123'})

    for _ in range(3):
        r = client.post('/api/v1/auth/login', json={'username': 'hank', 'password': 'wrong-pass'})
        assert r.status_code == 401
    # 已达阈值 → 锁定，即使密码正确也拒绝
    r = client.post('/api/v1/auth/login', json={'username': 'hank', 'password': 'secret123'})
    assert r.status_code == 429
    assert r.get_json()['code'] == 4003


def test_login_lock_expires(client, fresh_store, monkeypatch):
    """锁定过期后恢复登录"""
    from flask_server import config
    import time
    monkeypatch.setattr(config, 'auth_login_max_fails', 2)
    monkeypatch.setattr(config, 'auth_login_lock_seconds', 1)
    client.post('/api/v1/auth/register', json={'username': 'iris', 'password': 'secret123'})

    for _ in range(2):
        client.post('/api/v1/auth/login', json={'username': 'iris', 'password': 'wrong-pass'})
    assert client.post('/api/v1/auth/login', json={'username': 'iris', 'password': 'secret123'}).status_code == 429

    time.sleep(1.2)   # 等待锁定过期
    r = client.post('/api/v1/auth/login', json={'username': 'iris', 'password': 'secret123'})
    assert r.status_code == 200


def test_login_success_clears_fail_count(client, fresh_store, monkeypatch):
    """成功登录清除失败计数（未达阈值时）"""
    from flask_server import config
    monkeypatch.setattr(config, 'auth_login_max_fails', 5)
    client.post('/api/v1/auth/register', json={'username': 'james', 'password': 'secret123'})

    client.post('/api/v1/auth/login', json={'username': 'james', 'password': 'wrong-pass'})
    client.post('/api/v1/auth/login', json={'username': 'james', 'password': 'wrong-pass'})
    # 第三次正确 → 成功并清计数
    assert client.post('/api/v1/auth/login', json={'username': 'james', 'password': 'secret123'}).status_code == 200
    # 再错 2 次未达阈值（计数已清零，5 次才锁）
    for _ in range(2):
        client.post('/api/v1/auth/login', json={'username': 'james', 'password': 'wrong-pass'})
    r = client.post('/api/v1/auth/login', json={'username': 'james', 'password': 'secret123'})
    assert r.status_code == 200


def test_refresh_token_rotation(client, fresh_store):
    """refresh_token 换取新令牌：旧 refresh 作废（单次使用）"""
    client.post('/api/v1/auth/register', json={'username': 'kate', 'password': 'secret123'})
    login_data = client.post('/api/v1/auth/login', json={'username': 'kate', 'password': 'secret123'}).get_json()['data']
    old_refresh = login_data['refresh_token']

    r = client.post('/api/v1/auth/refresh', json={'refresh_token': old_refresh})
    assert r.status_code == 200
    data = r.get_json()['data']
    assert data['username'] == 'kate'
    assert len(data['token']) == 32
    assert data['refresh_token'] != old_refresh

    # 旧 refresh 已作废 → 再次使用返回 401
    r = client.post('/api/v1/auth/refresh', json={'refresh_token': old_refresh})
    assert r.status_code == 401
    assert r.get_json()['code'] == 4002

    # 新 refresh 仍可用
    r = client.post('/api/v1/auth/refresh', json={'refresh_token': data['refresh_token']})
    assert r.status_code == 200


def test_refresh_invalid_token(client, fresh_store):
    r = client.post('/api/v1/auth/refresh', json={'refresh_token': 'invalid-token'})
    assert r.status_code == 401
    assert r.get_json()['code'] == 4002


@pytest.fixture
def auth_db_env(monkeypatch, tmp_path):
    """SQLAlchemy 认证存储测试环境：临时库 + reload 占位模型 → 真实 ORM"""
    from importlib import import_module, reload
    from flask import Flask
    from flask_server import config

    sa_module = import_module('flask_server.module.sqlalchemy')
    user_module = import_module('flask_server.model.po.user')

    monkeypatch.setattr(config, 'sqlalchemy_uri', f'sqlite:///{tmp_path / "auth_it.db"}')
    monkeypatch.setattr(config, 'db_reflect_on_start', False)
    monkeypatch.setattr(config, 'auth_store', 'sqlalchemy')
    monkeypatch.setattr(sa_module, 'db', None)
    AuthService._store = None

    app = Flask(__name__)
    sa_module.init_SQLAlchemy(app)
    db = sa_module.sqlalchemy()
    reload(user_module)      # db 就绪后重载：UserPO 从占位类变为真实 ORM 模型
    with app.app_context():
        db.create_all()

    yield db, app

    with app.app_context():
        db.session.remove()
        db.engine.dispose()
    monkeypatch.setattr(sa_module, 'db', None)
    AuthService._store = None


def test_auth_sqlalchemy_register_login(auth_db_env):
    """sqlalchemy 存储：注册/登录/查询全流程（服务层直测）"""
    db, app = auth_db_env
    uid = AuthService.register('liam', 'secret123')
    tokens, err = AuthService.login('liam', 'secret123')
    assert err is None and tokens is not None
    user = AuthService.get_user_by_token(tokens[0])
    assert user is not None
    assert user.uid == uid
    assert user.username == 'liam'


def test_auth_sqlalchemy_wrong_password(auth_db_env):
    """sqlalchemy 存储：密码错误登录失败"""
    db, app = auth_db_env
    AuthService.register('mia', 'secret123')
    tokens, err = AuthService.login('mia', 'wrong-pass')
    assert tokens is None
    assert err == 'invalid'


def test_auth_sqlalchemy_persisted(auth_db_env):
    """sqlalchemy 存储：用户数据落库（非进程内）"""
    db, app = auth_db_env
    AuthService.register('mia', 'secret123')
    # 直接从数据库查询验证持久化
    store = AuthService._get_store()
    assert store.get_by_username('mia') is not None


def test_auth_sqlalchemy_duplicate_rejected(auth_db_env):
    """sqlalchemy 存储：重复用户名注册被拒绝（事务回滚）"""
    db, app = auth_db_env
    AuthService.register('noah', 'secret123')
    with pytest.raises(ValueError, match='username already exists'):
        AuthService.register('noah', 'other456')


# ------------------------- P3: token 存储自动降级 -------------------------


class _FakeRedisClient:
    """模拟 Redis 客户端（与 test_redis_cache 的假客户端同构）"""

    def __init__(self):
        self.store = {}

    def ping(self):
        return True

    def set(self, key, data):
        self.store[key] = data

    def setex(self, key, ttl, data):
        self.store[key] = data

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        self.store.pop(key, None)

    def exists(self, key):
        return key in self.store

    def expire(self, key, ttl):
        pass


def _fake_redis_cache(client):
    """绕过 __init__ 构造 RedisCache 实例（避免真实连接）"""
    from flask_server.module.redis_cache import RedisCache
    cache = RedisCache.__new__(RedisCache)
    cache.client = client
    cache._retry_cooldown = 30
    cache._unavailable_until = 0.0
    cache._need_recovery = False
    return cache


def test_tokens_in_redis_when_configured(client, fresh_store, monkeypatch):
    """P3 回归：配置 REDIS_URL 时认证 token/防爆破计数落 Redis（多 worker 可共享）"""
    from flask_server.component import auth as auth_module
    from flask_server.module import memory_cache

    fake = _FakeRedisClient()
    monkeypatch.setattr(auth_module.config, 'redis_url', 'redis://fake:6379/0')
    monkeypatch.setattr(auth_module, 'redis_cache', _fake_redis_cache(fake))

    client.post('/api/v1/auth/register', json={'username': 'redisuser', 'password': 'secret123'})
    data = client.post('/api/v1/auth/login', json={'username': 'redisuser', 'password': 'secret123'}).get_json()['data']
    token = data['token']

    assert any(k.startswith('auth:token:') for k in fake.store), 'token 应写入 Redis'
    assert not any(k.startswith(('auth:token:', 'auth:refresh:', 'auth:login_fail:', 'auth:login_lock:'))
                   for k in memory_cache.cache.keys()), 'token 不应写入内存缓存'

    # 经 Redis 存储完成完整校验闭环
    r = client.get('/api/v1/auth/me', headers={'X-AUTH-TOKEN': token})
    assert r.status_code == 200
    assert r.get_json()['data']['username'] == 'redisuser'


def test_tokens_in_memory_when_no_redis(client, fresh_store, monkeypatch):
    """P3 回归：未配置 REDIS_URL 时回退内存缓存（默认行为）"""
    from flask_server.component import auth as auth_module
    from flask_server.module import memory_cache

    monkeypatch.setattr(auth_module.config, 'redis_url', None)
    monkeypatch.setattr(auth_module, 'redis_cache', None)

    client.post('/api/v1/auth/register', json={'username': 'memuser', 'password': 'secret123'})
    data = client.post('/api/v1/auth/login', json={'username': 'memuser', 'password': 'secret123'}).get_json()['data']
    token = data['token']

    assert any(k.startswith('auth:token:') for k in memory_cache.cache.keys()), '未配置 Redis 时应写入内存'
    assert client.get('/api/v1/auth/me', headers={'X-AUTH-TOKEN': token}).status_code == 200
