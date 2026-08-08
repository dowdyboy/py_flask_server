"""RedisCache 单元测试：使用假客户端验证降级与冷却式自动恢复"""

import time

from flask_server.module.redis_cache import RedisCache


class _FakeClient:
    """模拟 redis 客户端：fail=True 时所有操作抛异常（模拟 Redis 不可达），并统计 ping 次数"""

    def __init__(self, fail=False):
        self.fail = fail
        self.ping_count = 0
        self.store = {}

    def _raise_if_fail(self):
        if self.fail:
            raise ConnectionError('mock redis down')

    def ping(self):
        self.ping_count += 1
        self._raise_if_fail()
        return True

    def set(self, key, data):
        self._raise_if_fail()
        self.store[key] = data

    def setex(self, key, ttl, data):
        self._raise_if_fail()
        self.store[key] = data

    def get(self, key):
        self._raise_if_fail()
        return self.store.get(key)

    def delete(self, key):
        self._raise_if_fail()
        self.store.pop(key, None)

    def exists(self, key):
        self._raise_if_fail()
        return key in self.store

    def expire(self, key, ttl):
        self._raise_if_fail()


def _make_cache(client=None, cooldown=0.05):
    """绕过 __init__（避免真实连接），手工构造实例"""
    cache = RedisCache.__new__(RedisCache)
    cache.client = client or _FakeClient()
    cache._retry_cooldown = cooldown
    cache._unavailable_until = 0.0
    cache._need_recovery = False
    return cache


def test_no_ping_when_healthy():
    """正常时零探测直通：连续操作不产生 ping（无多余 RTT）"""
    client = _FakeClient()
    c = _make_cache(client)
    for _ in range(5):
        c.set('k', 1)
        c.get('k')
    assert client.ping_count == 0


def test_set_get_json_roundtrip():
    c = _make_cache()
    assert c.set('k', {'a': 1, 'b': [1, 2]})
    assert c.get('k') == {'a': 1, 'b': [1, 2]}


def test_get_missing():
    c = _make_cache()
    assert c.get('nope') is None


def test_exists_delete():
    c = _make_cache()
    c.set('k', 'v')
    assert c.exists('k')
    c.delete('k')
    assert not c.exists('k')


def test_degrade_then_recover():
    """操作失败触发降级（进入冷却期），冷却结束自动恢复（不再永久不可用）"""
    client = _FakeClient()
    c = _make_cache(client, cooldown=0.05)

    client.fail = True
    assert c.get('k') is None          # 操作抛异常 → 进入冷却期降级
    assert c.set('k2', 1) is False     # 冷却期内仍不可用
    assert c.get('k') is None

    client.fail = False
    time.sleep(0.06)                   # 等待冷却结束
    assert c.set('k3', 1) is True      # 冷却后首次操作先 ping 探测，成功即恢复
    assert c.get('k3') == 1


def test_set_failure_enters_cooldown():
    """set 抛异常后应进入冷却期（后续调用短路返回）"""
    client = _FakeClient()
    c = _make_cache(client, cooldown=0.05)

    orig_set = client.set

    def broken_set(key, data):
        raise RuntimeError('mock set failure')

    client.set = broken_set
    assert c.set('k', 'v') is False
    client.set = orig_set
    assert c.set('k2', 'v') is False   # 冷却期内仍失败（未真正执行 set）


def test_set_with_ttl_uses_setex():
    """设置 ttl 时应走 setex 分支"""
    client = _FakeClient()
    c = _make_cache(client)
    assert c.set('k', 'v', ttl=60) is True
    assert c.get('k') == 'v'


def test_expire():
    client = _FakeClient()
    c = _make_cache(client)
    c.set('k', 'v')
    c.expire('k', 60)
    assert c.exists('k')
