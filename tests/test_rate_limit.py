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
