"""限流组件测试：顺延窗口计数，超限返回 429"""

import pytest

from flask_server import config
from flask_server.module import memory_cache


@pytest.fixture
def rate_limit_enabled(monkeypatch):
    monkeypatch.setattr(config, 'rate_limit_enabled', True)
    monkeypatch.setattr(config, 'rate_limit_per_minute', 3)
    yield
    # 清理测试产生的限流计数，避免污染其他用例
    for k in list(memory_cache.cache.keys()):
        if k.startswith('rate:'):
            memory_cache.delete(k)


def test_rate_limit_blocks_after_threshold(client, rate_limit_enabled):
    """同一 IP+路径 超过阈值后第 N+1 次请求返回 429"""
    for _ in range(3):
        resp = client.get('/hello')
        assert resp.status_code == 200
    resp = client.get('/hello')
    assert resp.status_code == 429


def test_rate_limit_disabled_by_default(client):
    """默认关闭时不限流"""
    assert config.rate_limit_enabled is False
    for _ in range(5):
        resp = client.get('/hello')
        assert resp.status_code == 200


def test_store_selection_memory():
    """RATE_LIMIT_STORE=memory 时使用内存缓存"""
    from flask_server.component import rate_limit as rl
    assert rl._get_store() is memory_cache


class _FakeRedisLike:
    """模拟 RedisCache：incr/get/set 委托 memory_cache，验证 redis 计数路径"""

    def get(self, key):
        return memory_cache.get(key)

    def set(self, key, value, ttl=None):
        return memory_cache.set(key, value, ttl=ttl)

    def incr(self, key, ttl=None):
        return memory_cache.incr(key, ttl=ttl)


def test_store_selection_redis(monkeypatch):
    """RATE_LIMIT_STORE=redis 且配置 REDIS_URL 时使用 Redis 缓存"""
    from flask_server.component import rate_limit as rl
    monkeypatch.setattr(config, 'rate_limit_store', 'redis')
    monkeypatch.setattr(config, 'redis_url', 'redis://localhost:6379/0')
    fake_cache = _FakeRedisLike()
    monkeypatch.setattr(rl, 'redis_cache', fake_cache)
    assert rl._get_store() is fake_cache


def test_store_selection_memory_when_redis_unset(monkeypatch):
    """RATE_LIMIT_STORE=redis 但未配置 REDIS_URL 时回退内存缓存"""
    from flask_server.component import rate_limit as rl
    monkeypatch.setattr(config, 'rate_limit_store', 'redis')
    monkeypatch.setattr(config, 'redis_url', None)
    monkeypatch.setattr(rl, 'redis_cache', None)
    assert rl._get_store() is memory_cache


def test_redis_store_rate_limit_path(client, monkeypatch):
    """redis 存储模式下限流计数仍生效（复用内存缓存模拟）"""
    from flask_server.component import rate_limit as rl
    monkeypatch.setattr(config, 'rate_limit_enabled', True)
    monkeypatch.setattr(config, 'rate_limit_per_minute', 2)
    monkeypatch.setattr(config, 'rate_limit_store', 'redis')
    monkeypatch.setattr(rl, 'redis_cache', _FakeRedisLike())
    try:
        for _ in range(2):
            assert client.get('/hello').status_code == 200
        assert client.get('/hello').status_code == 429
    finally:
        for k in list(memory_cache.cache.keys()):
            if k.startswith('rate:'):
                memory_cache.delete(k)


def test_rate_limit_ip_quota_blocks_path_randomization(client, rate_limit_enabled):
    """IP 级总配额兜底：随机路径无法绕过限流（rate_limit_per_minute=3）"""
    for i in range(3):
        assert client.get(f'/p{i}').status_code == 200
    # 第 4 个唯一路径：路径级配额未超限，但 IP 级总配额已超 → 429
    resp = client.get('/p3')
    assert resp.status_code == 429


def test_rate_limit_exempts_probes(client, monkeypatch):
    """探针/监控端点豁免限流（防编排系统 429 雪崩）"""
    from flask_server.component import rate_limit as rl
    monkeypatch.setattr(config, 'rate_limit_enabled', True)
    monkeypatch.setattr(config, 'rate_limit_per_minute', 2)
    monkeypatch.setattr(rl, 'redis_cache', None)
    try:
        for _ in range(5):
            assert client.get('/api/v1/healthz').status_code == 200
        for _ in range(5):
            assert client.get('/api/v1/readyz').status_code == 200
        for _ in range(5):
            assert client.get('/metrics').status_code == 200
        # 非豁免端点仍被限流
        for _ in range(2):
            assert client.get('/hello').status_code == 200
        assert client.get('/hello').status_code == 429
    finally:
        for k in list(memory_cache.cache.keys()):
            if k.startswith('rate:'):
                memory_cache.delete(k)


def test_rate_limit_exempts_options(client, monkeypatch):
    """CORS 预检（OPTIONS）不计数，避免大量 preflight 挤占配额"""
    from flask_server.component import rate_limit as rl
    monkeypatch.setattr(config, 'rate_limit_enabled', True)
    monkeypatch.setattr(config, 'rate_limit_per_minute', 2)
    monkeypatch.setattr(rl, 'redis_cache', None)
    try:
        for _ in range(5):
            r = client.options('/api/v1/echo', headers={
                'Origin': 'http://app.example.com',
                'Access-Control-Request-Method': 'POST',
            })
            assert r.status_code == 200
        # 非 OPTIONS 请求仍被限流
        for _ in range(2):
            assert client.get('/hello').status_code == 200
        assert client.get('/hello').status_code == 429
    finally:
        for k in list(memory_cache.cache.keys()):
            if k.startswith('rate:'):
                memory_cache.delete(k)


def test_rate_limit_ip_quota_and_path_quota_independent(client, monkeypatch):
    """路径级配额先于 IP 级配额触发（同一路径高频滥用仍被拦截）"""
    from flask_server.component import rate_limit as rl
    monkeypatch.setattr(config, 'rate_limit_enabled', True)
    monkeypatch.setattr(config, 'rate_limit_per_minute', 3)
    monkeypatch.setattr(rl, 'redis_cache', None)
    try:
        for _ in range(3):
            assert client.get('/hello').status_code == 200
        assert client.get('/hello').status_code == 429
    finally:
        for k in list(memory_cache.cache.keys()):
            if k.startswith('rate:'):
                memory_cache.delete(k)


def test_rate_limit_exempts_trailing_slash(client, monkeypatch):
    """尾斜杠探针路径同样豁免（/metrics/ /api/v1/healthz/），防采集端尾斜杠 URL 被误限流"""
    from flask_server.component import rate_limit as rl
    monkeypatch.setattr(config, 'rate_limit_enabled', True)
    monkeypatch.setattr(config, 'rate_limit_per_minute', 2)
    monkeypatch.setattr(rl, 'redis_cache', None)
    for k in list(memory_cache.cache.keys()):
        if k.startswith('rate:'):
            memory_cache.delete(k)
    try:
        for path in ('/metrics/', '/api/v1/healthz/'):
            for _ in range(5):
                resp = client.get(path)
                # 尾斜杠命中已注册路由 → 308 重定向；无论是否被跟随后处理，都不能计入限流
                assert resp.status_code == 308, f'{path} -> {resp.status_code}'

        # 豁免路径完全不产生限流计数键（这是"未计数"的直接证据）
        assert not [k for k in memory_cache.cache.keys() if k.startswith('rate:')], \
            '豁免路径不应写入限流计数'

        # 非豁免路径：2 次用满 IP 配额后，换一个从未访问过的路径仍应 429
        for _ in range(2):
            assert client.get('/hello').status_code == 200
        assert [k for k in memory_cache.cache.keys() if k.startswith('rate:')]
        resp = client.get('/different-path-not-yet-counted')
        assert resp.status_code == 429, 'IP 级总配额未生效：随机路径可绕过限流'
    finally:
        for k in list(memory_cache.cache.keys()):
            if k.startswith('rate:'):
                memory_cache.delete(k)


def test_rate_limit_long_path_key_truncated(client, monkeypatch):
    """超长路径的限流键被哈希截断，不会以原始超长 path 撑爆缓存键内存"""
    from flask_server.component import rate_limit as rl
    monkeypatch.setattr(config, 'rate_limit_enabled', True)
    monkeypatch.setattr(config, 'rate_limit_per_minute', 100)
    monkeypatch.setattr(rl, 'redis_cache', None)
    long_path = '/' + 'a' * 3000
    try:
        assert client.get(long_path).status_code == 200
        rate_keys = [k for k in memory_cache.cache.keys() if k.startswith('rate:')]
        assert rate_keys
        assert all(len(k) < 600 for k in rate_keys), f'cache key too long: {max(len(k) for k in rate_keys)}'
        assert all(long_path not in k for k in rate_keys)
    finally:
        for k in list(memory_cache.cache.keys()):
            if k.startswith('rate:'):
                memory_cache.delete(k)


def test_rate_limit_429_carries_retry_after(client, monkeypatch):
    """429 必须带 Retry-After（RFC 6585），客户端据此退避而不是盲目重试。

    秒数取限流窗口长度（RATE_LIMIT_WINDOW）；由 app.set_retry_after_header 统一写入，
    限流与登录锁定两条 429 路径共用。
    """
    from flask_server.component import rate_limit as rl
    monkeypatch.setattr(config, 'rate_limit_enabled', True)
    monkeypatch.setattr(config, 'rate_limit_per_minute', 1)
    monkeypatch.setattr(rl, 'redis_cache', None)
    try:
        assert client.get('/hello').status_code == 200
        resp = client.get('/hello')
        assert resp.status_code == 429
        assert resp.headers.get('Retry-After') == str(rl.RATE_LIMIT_WINDOW)
        assert resp.headers['Retry-After'].isdigit()
    finally:
        for k in list(memory_cache.cache.keys()):
            if k.startswith('rate:'):
                memory_cache.delete(k)


def test_non_429_has_no_retry_after(client):
    """非 429 响应不应带 Retry-After（避免误导客户端退避正常请求）"""
    assert client.get('/hello').headers.get('Retry-After') is None
    assert client.get('/api/v1/healthz').headers.get('Retry-After') is None


def test_limit_warning_is_throttled(monkeypatch):
    """429 告警必须限频：洪泛时逐条 WARN 会把日志/采集端打爆"""
    import logging

    from flask_server.component import rate_limit as rl

    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = Capture()
    logging.getLogger('flask_server').addHandler(handler)
    monkeypatch.setattr(rl, '_LIMIT_WARN_COOLDOWN', 10.0)
    monkeypatch.setattr(rl.time, 'time', lambda: 100.0)
    rl._limit_warn_state.update({'ts': 0.0, 'suppressed': 0})
    try:
        rl._warn_limited('first')
        rl._warn_limited('second')
        assert len(records) == 1, '冷却窗口内第二条告警不应输出'

        monkeypatch.setattr(rl.time, 'time', lambda: 200.0)
        rl._warn_limited('third')
        assert len(records) == 2
        assert 'similar suppressed' in records[-1].getMessage()
    finally:
        logging.getLogger('flask_server').removeHandler(handler)
