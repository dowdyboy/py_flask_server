"""RedisCache 单元测试：使用假客户端验证降级与冷却式自动恢复"""

import time
from importlib import import_module

from flask_server.module.redis_cache import RedisCache

# 模块级 `time` 被替换为假时钟后，RedisCache 的冷却期判断完全可控
redis_cache_module = import_module('flask_server.module.redis_cache')


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

    def getdel(self, key):
        self._raise_if_fail()
        return self.store.pop(key, None)

    def incr(self, key):
        self._raise_if_fail()
        new = int(self.store.get(key, 0)) + 1
        self.store[key] = str(new)
        return new

    def pipeline(self):
        return _FakePipeline(self)

    def delete(self, key):
        self._raise_if_fail()
        self.store.pop(key, None)

    def exists(self, key):
        self._raise_if_fail()
        return key in self.store

    def expire(self, key, ttl):
        self._raise_if_fail()


class _FakePipeline:
    """极简 pipeline 假实现：入队 incr/expire，execute 依序执行"""

    def __init__(self, client):
        self._client = client
        self._cmds = []

    def incr(self, key):
        self._cmds.append(('incr', key))
        return self

    def expire(self, key, ttl):
        self._cmds.append(('expire', key, ttl))
        return self

    def execute(self):
        results = []
        for cmd in self._cmds:
            if cmd[0] == 'incr':
                results.append(self._client.incr(cmd[1]))
            else:
                self._client.expire(cmd[1], cmd[2])
                results.append(True)
        return results


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
    assert c.delete('k') is True
    assert not c.exists('k')


def test_degrade_then_recover(fake_clock, monkeypatch):
    """操作失败触发降级（进入冷却期），冷却结束自动恢复（不再永久不可用）"""
    monkeypatch.setattr(redis_cache_module, 'time', fake_clock)
    client = _FakeClient()
    c = _make_cache(client, cooldown=0.05)

    client.fail = True
    assert c.get('k') is None          # 操作抛异常 → 进入冷却期降级
    assert c.set('k2', 1) is False     # 冷却期内仍不可用
    assert c.get('k') is None

    client.fail = False
    fake_clock.advance(0.1)            # 冷却期结束（注入时钟，不真实 sleep）
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


def test_recovery_ping_success():
    """冷却结束后首次操作先 ping 探测：成功即恢复（_check 恢复分支）"""
    client = _FakeClient()
    c = _make_cache(client)
    c._need_recovery = True
    c._unavailable_until = 0.0
    assert c.get('k') is None        # 触发恢复探测
    assert client.ping_count == 1
    assert c._need_recovery is False
    assert c.set('k2', 'v') is True  # 恢复后直通


def test_recovery_ping_failure_stays_degraded():
    """恢复探测失败时继续冷却降级"""
    client = _FakeClient(fail=True)
    c = _make_cache(client, cooldown=0.05)
    c._need_recovery = True
    c._unavailable_until = 0.0
    assert c.get('k') is None
    assert client.ping_count == 1
    assert c._unavailable_until > 0


def test_get_parse_failure_deletes_key():
    """get 解析失败（脏数据）时删除该键并返回 None"""
    client = _FakeClient()
    c = _make_cache(client)
    client.store['bad'] = 'not-json{{{'
    assert c.get('bad') is None
    assert 'bad' not in client.store


def test_get_cleanup_delete_error_still_returns_none():
    """自愈 DELETE 失败（GET 与 DELETE 之间连接断开）不能破坏"异常返回 None"的契约"""
    client = _FakeClient()
    c = _make_cache(client)
    client.store['bad'] = 'not-json{{{'

    def broken_delete(key):
        raise ConnectionError('down between GET and DELETE')

    client.delete = broken_delete
    assert c.get('bad') is None      # 不抛异常


def test_delete_failure_enters_cooldown():
    client = _FakeClient(fail=True)
    c = _make_cache(client)
    assert c.delete('k') is False    # 不抛异常，但必须如实报告失败
    assert c._unavailable_until > 0


def test_delete_bypasses_cooldown_and_still_reaches_redis():
    """P0 回归：冷却期内 delete 不能短路——登出/吊销必须每次都真实送达主存储

    修复前 delete 先 `_check()`，冷却期直接 return，登出静默失效：
    Redis 恢复后旧 access/refresh token（TTL 7/30 天）重新生效。
    """
    client = _FakeClient()
    c = _make_cache(client, cooldown=30)
    client.store['auth:token:t'] = '"uid"'
    c._unavailable_until = time.time() + 30      # 模拟刚发生故障、处于冷却期
    c._need_recovery = True

    assert c.delete('auth:token:t') is True
    assert 'auth:token:t' not in client.store


def test_delete_success_returns_true():
    client = _FakeClient()
    c = _make_cache(client)
    c.set('k', 'v')
    assert c.delete('k') is True


def test_exists_failure_enters_cooldown():
    client = _FakeClient(fail=True)
    c = _make_cache(client)
    assert c.exists('k') is False
    assert c._unavailable_until > 0


def test_expire_failure_enters_cooldown():
    client = _FakeClient(fail=True)
    c = _make_cache(client)
    c.expire('k', 60)                # 不抛异常
    assert c._unavailable_until > 0


def test_ping_healthy():
    """正常时 ping 走真实探测并返回 True"""
    client = _FakeClient()
    c = _make_cache(client)
    assert c.ping() is True
    assert client.ping_count == 1


def test_ping_during_cooldown_no_connection():
    """冷却期内 ping 快速返回 False，不真实连接（健康检查不被 Redis 黑洞拖慢）"""
    client = _FakeClient()
    c = _make_cache(client)
    c._unavailable_until = time.time() + 30
    assert c.ping() is False
    assert client.ping_count == 0


def test_ping_failure_enters_cooldown():
    """真实 ping 失败时进入冷却并返回 False"""
    client = _FakeClient(fail=True)
    c = _make_cache(client)
    assert c.ping() is False
    assert c._unavailable_until > 0


def test_ping_recovers_after_cooldown(fake_clock, monkeypatch):
    """冷却结束后 ping 触发恢复探测：成功即返回 True 并解除降级"""
    monkeypatch.setattr(redis_cache_module, 'time', fake_clock)
    client = _FakeClient(fail=True)
    c = _make_cache(client, cooldown=0.05)
    assert c.ping() is False
    client.fail = False
    fake_clock.advance(0.1)           # 冷却期结束（注入时钟，不真实 sleep）
    assert c.ping() is True
    assert c._need_recovery is False


def test_getdel_consumes_once():
    """getdel 原子读取并删除（refresh 轮换单次使用语义）"""
    client = _FakeClient()
    c = _make_cache(client)
    c.set('k', {'uid': 'u1'})
    assert c.getdel('k') == {'uid': 'u1'}
    assert c.getdel('k') is None
    assert 'k' not in client.store


def test_getdel_failure_enters_cooldown():
    client = _FakeClient(fail=True)
    c = _make_cache(client)
    assert c.getdel('k') is None
    assert c._unavailable_until > 0


def test_incr_atomic_counter():
    """incr 原子自增并返回新值；ttl 顺延"""
    client = _FakeClient()
    c = _make_cache(client)
    assert c.incr('cnt', ttl=60) == 1
    assert c.incr('cnt', ttl=60) == 2
    assert client.store['cnt'] == '2'


def test_incr_failure_enters_cooldown():
    client = _FakeClient(fail=True)
    c = _make_cache(client)
    assert c.incr('cnt') is None
    assert c._unavailable_until > 0


def test_incr_corrupted_value_cleaned():
    """R 回归：INCR 返回非数字（数据损坏）时清理该键并返回 None，不抛异常"""
    class _WeirdPipeline:
        def incr(self, key):
            return self

        def expire(self, key, ttl):
            return self

        def execute(self):
            return ['not-a-number']

    client = _FakeClient()
    c = _make_cache(client)
    client.pipeline = lambda: _WeirdPipeline()
    assert c.incr('cnt') is None
    assert 'cnt' not in client.store


def test_incr_cleanup_delete_error_still_returns_none():
    """INCR 损坏值的自愈 DELETE 失败也不能抛出（契约：失败返回 None）"""
    class _WeirdPipeline:
        def incr(self, key):
            return self

        def expire(self, key, ttl):
            return self

        def execute(self):
            return ['not-a-number']

    client = _FakeClient()
    c = _make_cache(client)
    client.pipeline = lambda: _WeirdPipeline()

    def broken_delete(key):
        raise ConnectionError('down between INCR and DELETE')

    client.delete = broken_delete
    assert c.incr('cnt') is None


def test_incr_without_ttl_skips_expire():
    """incr 未传 ttl 时不执行 EXPIRE"""
    client = _FakeClient()
    c = _make_cache(client)
    assert c.incr('cnt') == 1


def test_getdel_parse_failure_returns_none():
    """getdel 对非 JSON 值返回 None，并清除损坏键（与 get 自愈行为一致）"""
    client = _FakeClient()
    c = _make_cache(client)
    client.store['bad'] = 'not-json{{{'
    assert c.getdel('bad') is None
    assert 'bad' not in client.store   # 损坏值已清除，调用方按 miss 处理


def test_expire_none_ttl_noop():
    """expire(ttl=None) 直接返回，不向 Redis 发送非法 EXPIRE"""
    client = _FakeClient()
    c = _make_cache(client)
    c.expire('k', None)
    assert c._unavailable_until == 0.0   # 未被误判降级


def test_delete_uses_short_timeout_revocation_client():
    """delete 绕过冷却期但用独立短超时客户端：Redis 黑洞时登出最多等 ~1s，而不是 6s"""
    from flask_server.module.redis_cache import RedisCache

    c = RedisCache('redis://127.0.0.1:1/0')
    try:
        fast = c._revocation_client()
        assert fast is not c.client
        kwargs = fast.connection_pool.connection_kwargs
        assert kwargs['socket_timeout'] == 0.5
        assert kwargs['socket_connect_timeout'] == 0.5
    finally:
        c.close()


def test_revocation_client_falls_back_to_injected_client():
    """测试注入的实例没有 _url：吊销退回 self.client（保持既有单测可用）"""
    client = _FakeClient()
    c = _make_cache(client)
    assert c._revocation_client() is client
