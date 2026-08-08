import threading
import time
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
