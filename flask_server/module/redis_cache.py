import json
import time
from typing import Any, Optional
from ..config import config
from ..util import Logger

# Redis 缓存模块，用于替代内存缓存（多进程/多实例场景）
# 各操作有异常降级：Redis 不可达时 Logger.warn 并返回安全默认值，不中断业务
# 例外：delete() 是登出/令牌吊销等"必须送达"的操作，冷却期内也绕过降级、真实执行并返回 bool
# 探测策略：正常时零探测直通（操作成功即证明可用）；操作失败进入冷却期，
# 冷却结束后首次操作前 ping 探测恢复，成功即恢复可用


def _as_json_text(value):
    """把 redis 返回值收窄成 json.loads 可接受的类型。

    `decode_responses=True` 时正常返回 str；但 redis-py 的类型标注是
    `Awaitable[Any] | Any`（同一套 API 兼容 asyncio 客户端），静态检查无法收窄。
    这里做一次显式转换：非 str/bytes/bytearray 一律转字符串——既满足类型检查，
    也让"假客户端返回意外类型"时走 json.loads 的异常分支（按脏数据处理），而不是抛 TypeError。
    """
    if isinstance(value, (str, bytes, bytearray)):
        return value
    return str(value)


class RedisCache:

    def __init__(self, url: str) -> None:
        import redis
        self._url = url
        # socket 超时：防止 Redis 网络黑洞时请求无限挂起
        self.client = redis.Redis.from_url(
            url, decode_responses=True,
            socket_connect_timeout=3, socket_timeout=3,
        )
        # 吊销专用客户端（短超时）：delete() 对失败零容忍、必须绕过冷却期真实执行，
        # 但正常客户端的 3s connect + 3s read 会让 Redis 黑洞时的登出阻塞 6s，
        # 少量并发登出就能占满 waitress 线程池。这里单独用 0.5s 超时快速失败。
        self._fast_client: Optional[Any] = None
        self._retry_cooldown = 30
        self._unavailable_until = 0.0
        self._need_recovery = False
        # 不在导入期打印初始化日志：那会提前初始化进程默认 logger，导致 app logger
        # 与默认 logger 双写（0.9 起日志按 app 隔离）。需要时由调用方/首次使用记录。

    def _mark_unavailable(self):
        """标记不可用，进入冷却期（冷却结束后自动重试恢复）"""
        self._unavailable_until = time.time() + self._retry_cooldown
        self._need_recovery = True

    def _check(self):
        """检查是否可用：冷却期内降级；冷却结束后首次调用做恢复探测；其余直通"""
        now = time.time()
        if now < self._unavailable_until:
            return False
        if self._need_recovery:
            try:
                self.client.ping()
                self._unavailable_until = 0.0
                self._need_recovery = False
                return True
            except Exception as e:
                Logger.warn(f'RedisCache unavailable, degrading for {self._retry_cooldown}s: {e}')
                self._mark_unavailable()
                return False
        return True

    def ping(self) -> bool:
        """连通性探测（供健康检查使用）：冷却期内快速返回 False，不真实连接；
        冷却期外执行真实 ping，失败则进入冷却并返回 False"""
        if not self._check():
            return False
        try:
            ok = bool(self.client.ping())
            if not ok:
                self._mark_unavailable()
            return ok
        except Exception as e:
            Logger.warn(f'RedisCache ping failed: {e}')
            self._mark_unavailable()
            return False

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """设置键值对，value 会被 JSON 序列化。

        返回 True 表示写入成功；False 表示失败（Redis 不可用，或 value 不可 JSON 序列化）。
        **不可序列化的 value 不算 Redis 故障**，不会触发冷却降级——修复前 json.dumps
        放在同一个 try 里，传一个 datetime/set/ORM 对象就会让整个缓存降级 30s，
        期间登录态、限流计数、防爆破计数全部失效，看起来却像"Redis 挂了"。
        """
        if not self._check():
            return False
        try:
            data = json.dumps(value)
        except (TypeError, ValueError) as e:
            # 参数错误（调用方传了不可序列化的值）：只告警，不标记不可用
            Logger.warn(f'RedisCache set skipped (value not JSON serializable) key={key}: {e}')
            return False
        try:
            if ttl:
                self.client.setex(key, ttl, data)
            else:
                self.client.set(key, data)
            return True
        except Exception as e:
            Logger.warn(f'RedisCache set failed key={key}: {e}')
            self._mark_unavailable()
            return False

    def get(self, key: str) -> Any:
        """获取键对应的值（JSON 反序列化），不存在或异常返回 None"""
        if not self._check():
            return None
        try:
            data = self.client.get(key)
        except Exception as e:
            Logger.warn(f'RedisCache get failed key={key}: {e}')
            self._mark_unavailable()
            return None
        if data is None:
            return None
        try:
            return json.loads(_as_json_text(data))
        except Exception as e:
            Logger.warn(f'RedisCache get parse failed key={key}: {e}')
            try:
                self.client.delete(key)
            except Exception as del_err:
                # 自愈清理失败（连接在 GET 与 DELETE 之间断开）不能抛出：
                # get 的契约是"异常时返回 None"，不能把缓存故障升级成业务 500
                Logger.warn(f'RedisCache get cleanup failed key={key}: {del_err}')
            return None

    def _revocation_client(self):
        """吊销专用客户端（惰性创建，0.5s 超时）；测试注入的实例退回 self.client"""
        fast = getattr(self, '_fast_client', None)
        if fast is not None:
            return fast
        url = getattr(self, '_url', None)
        if url is None:
            return self.client
        import redis
        fast = redis.Redis.from_url(
            url, decode_responses=True,
            socket_connect_timeout=0.5, socket_timeout=0.5,
        )
        self._fast_client = fast
        return fast

    def delete(self, key: str) -> bool:
        """删除键（必须送达的一次性操作，绕过冷却期降级）

        登出/refresh token 吊销等场景对失败零容忍：若冷却期内直接跳过 DELETE，
        Redis 恢复后旧令牌会重新生效（TTL 7/30 天）。因此这里不调用 `_check()`，
        始终尝试真实删除，并把结果返回给调用方决定是否告警/降级。

        使用独立的短超时客户端：Redis 网络黑洞（SYN 丢包/不响应）时最多阻塞约 1s，
        不会像普通客户端那样 3s+3s 地占住请求线程。

        Returns:
            bool: True 表示删除命令已成功执行；False 表示 Redis 异常（未真正删除）
        """
        try:
            self._revocation_client().delete(key)
            return True
        except Exception as e:
            Logger.warn(f'RedisCache delete failed key={key}: {e}')
            self._mark_unavailable()
            return False

    def close(self) -> None:
        """关闭主客户端与吊销客户端（幂等；测试替身可能没有 close）"""
        for client in (getattr(self, '_fast_client', None), self.client):
            if client is None:
                continue
            try:
                client.close()
            except Exception:
                pass
        self._fast_client = None

    def getdel(self, key: str) -> Any:
        """原子读取并删除（GETDEL，供 refresh token 轮换等单次使用场景）"""
        if not self._check():
            return None
        try:
            data = self.client.getdel(key)
        except Exception as e:
            Logger.warn(f'RedisCache getdel failed key={key}: {e}')
            self._mark_unavailable()
            return None
        if data is None:
            return None
        try:
            return json.loads(_as_json_text(data))
        except Exception as e:
            Logger.warn(f'RedisCache getdel parse failed key={key}: {e}')
            # 与 get 一致：损坏值视为脏数据，清除后由调用方按 miss 处理
            try:
                self.client.delete(key)
            except Exception:
                pass
            return None

    def incr(self, key: str, ttl: Optional[int] = None) -> Optional[int]:
        """原子自增计数（INCR，供防爆破计数等场景），返回新值；失败返回 None"""
        if not self._check():
            return None
        try:
            pipe = self.client.pipeline()
            pipe.incr(key)
            # 每次自增顺延过期时间：活动期内计数窗口不断刷新（与内存实现一致），
            # 防止攻击者等待窗口结束后再爆破
            if ttl:
                pipe.expire(key, ttl)
            results = pipe.execute()
        except Exception as e:
            Logger.warn(f'RedisCache incr failed key={key}: {e}')
            self._mark_unavailable()
            return None
        try:
            return int(results[0])
        except (TypeError, ValueError):
            # 损坏/非数字值：视为计数丢失，由调用方按 0 重计，不抛异常
            try:
                self.client.delete(key)
            except Exception as del_err:
                # 同 get/getdel：清理失败不能违反"失败返回 None"的契约
                Logger.warn(f'RedisCache incr cleanup failed key={key}: {del_err}')
            return None

    def exists(self, key: str) -> bool:
        """检查键是否存在"""
        if not self._check():
            return False
        try:
            # 同 _as_json_text：redis-py 的返回类型是 `Awaitable[Any] | Any`，需要显式收窄
            found: Any = self.client.exists(key)
            return int(found) > 0
        except Exception as e:
            Logger.warn(f'RedisCache exists failed key={key}: {e}')
            self._mark_unavailable()
            return False

    def expire(self, key: str, ttl: int) -> None:
        """为键设置过期时间"""
        if ttl is None:
            return   # 无 ttl 无意义，直接返回（避免向 Redis 发送非法 EXPIRE 误判降级）
        if not self._check():
            return
        try:
            self.client.expire(key, ttl)
        except Exception as e:
            Logger.warn(f'RedisCache expire failed key={key}: {e}')
            self._mark_unavailable()


# 未配置 REDIS_URL 时为 None，使用内存缓存替代
redis_cache = RedisCache(config.redis_url) if config.redis_url else None
