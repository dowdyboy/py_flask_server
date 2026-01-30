import time
import pickle

# 简单的内存缓存

class SimpleMemoryCache:
    def __init__(self):
        self.cache = {}
        self.expiry_times = {}

    def set(self, key, value, ttl=None):
        """设置键值对，并可选地设置TTL（生存时间）"""
        self.cache[key] = pickle.dumps(value)
        if ttl:
            self.expiry_times[key] = time.time() + ttl

    def get(self, key):
        """获取键对应的值，如果键已过期则返回None"""
        if self._is_expired(key):
            self.delete(key)
            return None
        try:
            return pickle.loads(self.cache.get(key, None))
        except:
            return None

    def delete(self, key):
        """删除键"""
        if key in self.cache:
            del self.cache[key]
        if key in self.expiry_times:
            del self.expiry_times[key]

    def exists(self, key):
        """检查键是否存在，如果键已过期则返回False"""
        if self._is_expired(key):
            self.delete(key)
            return False
        return key in self.cache

    def expire(self, key, ttl):
        self.expiry_times[key] = time.time() + ttl

    def _is_expired(self, key):
        """检查键是否已过期"""
        if key in self.expiry_times:
            return time.time() > self.expiry_times[key]
        return False


memory_cache = SimpleMemoryCache()
