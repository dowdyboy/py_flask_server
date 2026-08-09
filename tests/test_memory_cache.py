import threading
import time
import pickle
from flask_server.module.simple_memory_cache import SimpleMemoryCache


def test_set_get():
    c = SimpleMemoryCache()
    c.set('k', 'v')
    assert c.get('k') == 'v'


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


def test_ttl_expiry():
    c = SimpleMemoryCache()
    c.set('k', 'v', ttl=0.01)
    time.sleep(0.02)
    assert c.get('k') is None
    assert not c.exists('k')


def test_expire():
    c = SimpleMemoryCache()
    c.set('k', 'v')
    c.expire('k', 0.01)
    time.sleep(0.02)
    assert c.get('k') is None


def test_expire_none_ttl_noop():
    """expire(ttl=None) 直接返回，不抛异常（与 RedisCache 行为一致）"""
    c = SimpleMemoryCache()
    c.set('k', 'v')
    c.expire('k', None)
    assert c.get('k') == 'v'   # 未设置过期时间，值仍可读


def test_clear_expired():
    c = SimpleMemoryCache()
    c.set('a', 1, ttl=0.01)
    c.set('b', 2)
    time.sleep(0.02)
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
