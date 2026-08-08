"""限流组件测试：固定窗口计数，超限返回 429"""

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
    """模拟 RedisCache：set/get 委托 memory_cache，验证 redis 计数路径"""

    def get(self, key):
        return memory_cache.get(key)

    def set(self, key, value, ttl=None):
        return memory_cache.set(key, value, ttl=ttl)


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
