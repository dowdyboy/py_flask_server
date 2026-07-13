import json
from ..config import config
from ..util import Logger

# Redis 缓存模块，用于替代内存缓存（多进程/多实例场景）
# 各操作有异常降级：Redis 不可达时 Logger.warn 并返回安全默认值，不中断业务


class RedisCache:

    def __init__(self, url):
        import redis
        self.client = redis.Redis.from_url(url, decode_responses=True)
        self._available = True
        Logger.info(f'RedisCache initialized : {url}')

    def _check(self):
        """检查 Redis 连通性，失败则标记不可用"""
        if not self._available:
            return False
        try:
            self.client.ping()
            return True
        except Exception as e:
            Logger.warn(f'RedisCache unavailable, degrading: {e}')
            self._available = False
            return False

    def set(self, key, value, ttl=None):
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
            self._available = False
            return False

    def get(self, key):
        """获取键对应的值（JSON 反序列化），不存在或异常返回 None"""
        if not self._check():
            return None
        try:
            data = self.client.get(key)
        except Exception as e:
            Logger.warn(f'RedisCache get failed key={key}: {e}')
            self._available = False
            return None
        if data is None:
            return None
        try:
            return json.loads(data)
        except Exception as e:
            Logger.warn(f'RedisCache get parse failed key={key}: {e}')
            self.client.delete(key)
            return None

    def delete(self, key):
        """删除键"""
        if not self._check():
            return
        try:
            self.client.delete(key)
        except Exception as e:
            Logger.warn(f'RedisCache delete failed key={key}: {e}')
            self._available = False

    def exists(self, key):
        """检查键是否存在"""
        if not self._check():
            return False
        try:
            return self.client.exists(key) > 0
        except Exception as e:
            Logger.warn(f'RedisCache exists failed key={key}: {e}')
            self._available = False
            return False

    def expire(self, key, ttl):
        """为键设置过期时间"""
        if not self._check():
            return
        try:
            self.client.expire(key, ttl)
        except Exception as e:
            Logger.warn(f'RedisCache expire failed key={key}: {e}')
            self._available = False


# 未配置 REDIS_URL 时为 None，使用内存缓存替代
redis_cache = RedisCache(config.redis_url) if config.redis_url else None
