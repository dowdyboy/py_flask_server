import pickle
import threading
from importlib import import_module

import pytest

from flask_server.module.simple_memory_cache import SimpleMemoryCache

# 模块级 `time` 可被替换为假时钟：TTL 断言不依赖真实等待
memory_cache_module = import_module('flask_server.module.simple_memory_cache')


def test_set_get():
    c = SimpleMemoryCache()
    c.set('k', 'v')
    assert c.get('k') == 'v'


def test_set_returns_true():
    """set 返回 True（与 RedisCache 契约一致，供认证降级逻辑判断写入成功）"""
    c = SimpleMemoryCache()
    assert c.set('k', 'v') is True


def test_get_missing():
    c = SimpleMemoryCache()
    assert c.get('nope') is None


def test_delete():
    c = SimpleMemoryCache()
    c.set('k', 'v')
    c.delete('k')
    assert c.get('k') is None
    assert not c.exists('k')


def test_exists():
    c = SimpleMemoryCache()
    c.set('k', 'v')
    assert c.exists('k')
    assert not c.exists('nope')


def test_ttl_expiry(fake_clock, monkeypatch):
    monkeypatch.setattr(memory_cache_module, 'time', fake_clock)
    c = SimpleMemoryCache()
    c.set('k', 'v', ttl=0.01)
    fake_clock.advance(0.02)          # 注入时钟，不真实 sleep
    assert c.get('k') is None
    assert not c.exists('k')


def test_expire(fake_clock, monkeypatch):
    monkeypatch.setattr(memory_cache_module, 'time', fake_clock)
    c = SimpleMemoryCache()
    c.set('k', 'v')
    c.expire('k', 0.01)
    fake_clock.advance(0.02)
    assert c.get('k') is None


def test_expire_none_ttl_noop():
    """expire(ttl=None) 直接返回，不抛异常（与 RedisCache 行为一致）"""
    c = SimpleMemoryCache()
    c.set('k', 'v')
    c.expire('k', None)
    assert c.get('k') == 'v'   # 未设置过期时间，值仍可读


def test_clear_expired(fake_clock, monkeypatch):
    monkeypatch.setattr(memory_cache_module, 'time', fake_clock)
    c = SimpleMemoryCache()
    c.set('a', 1, ttl=0.01)
    c.set('b', 2)
    fake_clock.advance(0.02)
    n = c.clear_expired()
    assert n == 1
    assert c.get('a') is None
    assert c.get('b') == 2


def test_overwrite():
    c = SimpleMemoryCache()
    c.set('k', 'v1')
    c.set('k', 'v2')
    assert c.get('k') == 'v2'


def test_cleanup_thread_running():
    """模块级缓存应有后台 daemon 清理线程（防 TTL 键内存泄漏）"""
    names = [t.name for t in threading.enumerate()]
    assert 'memory-cache-cleanup' in names


def test_corrupted_value_self_heals():
    """pickle 损坏的值：get 返回 None 并自愈删除该键"""
    c = SimpleMemoryCache()
    c.cache['bad'] = b'\x80\x05not-a-valid-pickle'
    assert c.get('bad') is None
    assert 'bad' not in c.cache


def test_getdel_consumes_once():
    """getdel 原子读取并删除（refresh 轮换单次使用语义）"""
    c = SimpleMemoryCache()
    c.set('k', {'uid': 'u1'})
    assert c.getdel('k') == {'uid': 'u1'}
    assert c.getdel('k') is None
    assert 'k' not in c.cache


def test_getdel_expired_returns_none():
    """getdel 对已过期键返回 None 并清理"""
    c = SimpleMemoryCache()
    c.set('k', 'v', ttl=-10)
    assert c.getdel('k') is None
    assert 'k' not in c.cache


def test_incr_atomic_counter():
    """incr 原子自增：不存在从 1 开始，连续自增累计"""
    c = SimpleMemoryCache()
    assert c.incr('cnt', ttl=60) == 1
    assert c.incr('cnt', ttl=60) == 2
    assert c.incr('cnt') == 3
    assert c.get('cnt') == 3


def test_incr_expired_restarts():
    """incr 对已过期键从 1 重新计数"""
    c = SimpleMemoryCache()
    c.set('cnt', 5, ttl=-10)
    assert c.incr('cnt', ttl=60) == 1


def test_incr_corrupted_value_restarts():
    """R 回归：incr 遇到损坏/非数字值按 0 重计，不抛异常（防登录 500）"""
    c = SimpleMemoryCache()
    c.cache['cnt'] = pickle.dumps('not-a-number')
    assert c.incr('cnt') == 1
    assert c.get('cnt') == 1


# ------------------------- 容量上限（防内存无界增长） -------------------------


def test_default_max_entries():
    """默认容量上限适用于长期运行的进程级缓存"""
    from flask_server.module.simple_memory_cache import DEFAULT_MAX_ENTRIES
    assert SimpleMemoryCache().max_entries == DEFAULT_MAX_ENTRIES


def test_max_entries_must_be_positive():
    """max_entries <= 0 非法：不能配置出"永不淘汰的 0 容量"歧义"""
    for bad in (0, -1):
        with pytest.raises(ValueError, match='max_entries'):
            SimpleMemoryCache(max_entries=bad)


def test_max_entries_evicts_least_recently_written():
    """超限时淘汰最久未写入的键；写入顺序 = LRW 顺序"""
    c = SimpleMemoryCache(max_entries=3)
    for key in ('a', 'b', 'c'):
        c.set(key, key)
    assert list(c.cache) == ['a', 'b', 'c']

    c.set('d', 'd')                  # 超限 → 淘汰 a
    assert set(c.cache) == {'b', 'c', 'd'}
    assert list(c._order) == ['b', 'c', 'd']
    assert len(c.cache) <= c.max_entries


def test_overwrite_refreshes_eviction_order():
    """覆盖写也算"最近写入"：刚覆盖的键不能成为本次插入的淘汰对象"""
    c = SimpleMemoryCache(max_entries=3)
    for key in ('a', 'b', 'c'):
        c.set(key, key)
    c.set('a', 'a2')                 # 刷新 a → 顺序 b, c, a
    c.set('d', 'd')                  # 超限 → 淘汰 b 而不是 a
    assert set(c.cache) == {'a', 'c', 'd'}
    assert list(c._order) == ['c', 'a', 'd']
    assert c.get('a') == 'a2'


def test_get_does_not_change_eviction_order():
    """get 只读：不刷新写入顺序（LRW 而非 LRU，注释里已明确该语义）"""
    c = SimpleMemoryCache(max_entries=3)
    for key in ('a', 'b', 'c'):
        c.set(key, key)
    assert c.get('a') == 'a'
    c.set('d', 'd')
    assert 'a' not in c.cache, 'get 不应影响淘汰顺序'


def test_eviction_warning_is_rate_limited(monkeypatch):
    """淘汰告警按 EVICTION_WARN_INTERVAL 限频，避免每次插入刷日志"""
    from flask_server.util import Logger

    monkeypatch.setattr(SimpleMemoryCache, 'EVICTION_WARN_INTERVAL', 2)
    warnings = []
    monkeypatch.setattr(Logger, 'warn', lambda msg, **kwargs: warnings.append(msg))

    c = SimpleMemoryCache(max_entries=1)
    c.set('a', 1)
    c.set('b', 2)                    # 第 1 次淘汰：未达告警阈值
    assert warnings == []
    c.set('c', 3)                    # 第 2 次淘汰：告警一次并清零
    assert len(warnings) == 1
    assert 'max_entries=1' in warnings[0]
    c.set('d', 4)                    # 第 3 次淘汰：重新累计，不再告警
    assert len(warnings) == 1
