import json
import time
from typing import Any, Optional
from ..config import config
from ..util import Logger, CommonUtil

# Redis 缓存模块，用于替代内存缓存（多进程/多实例场景）
# 各操作有异常降级：Redis 不可达时 Logger.warn 并返回安全默认值，不中断业务
# 例外：delete() 是登出/令牌吊销等"必须送达"的操作，冷却期内也绕过降级、真实执行并返回 bool
# 探测策略：正常时零探测直通（操作成功即证明可用）；操作失败进入冷却期，
# 冷却结束后首次操作前 ping 探测恢复，成功即恢复可用


class RedisCache:

    def __init__(self, url: str) -> None:
        import redis
        # socket 超时：防止 Redis 网络黑洞时请求无限挂起
        self.client = redis.Redis.from_url(
            url, decode_responses=True,
            socket_connect_timeout=3, socket_timeout=3,
        )
        self._retry_cooldown = 30
        self._unavailable_until = 0.0
        self._need_recovery = False
        Logger.info(f'RedisCache initialized : {CommonUtil.mask_uri(url)}')

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
        """设置键值对，value 会被 JSON 序列化"""
        if not self._check():
            return False
        try:
            data = json.dumps(value)
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
            return json.loads(data)
        except Exception as e:
            Logger.warn(f'RedisCache get parse failed key={key}: {e}')
            try:
                self.client.delete(key)
            except Exception as del_err:
                # 自愈清理失败（连接在 GET 与 DELETE 之间断开）不能抛出：
                # get 的契约是"异常时返回 None"，不能把缓存故障升级成业务 500
                Logger.warn(f'RedisCache get cleanup failed key={key}: {del_err}')
            return None

    def delete(self, key: str) -> bool:
        """删除键（必须送达的一次性操作，绕过冷却期降级）

        登出/refresh token 吊销等场景对失败零容忍：若冷却期内直接跳过 DELETE，
        Redis 恢复后旧令牌会重新生效（TTL 7/30 天）。因此这里不调用 `_check()`，
        始终尝试真实删除，并把结果返回给调用方决定是否告警/降级。

        Returns:
            bool: True 表示删除命令已成功执行；False 表示 Redis 异常（未真正删除）
        """
        try:
            self.client.delete(key)
            return True
        except Exception as e:
            Logger.warn(f'RedisCache delete failed key={key}: {e}')
            self._mark_unavailable()
            return False

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
            return json.loads(data)
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
            return self.client.exists(key) > 0
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
