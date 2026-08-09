import time
import pickle
import threading
from ..util import Logger

# 简单的内存缓存
# 注意：waitress 为多线程 WSGI 服务器，本缓存已加线程锁保证安全
#
# 安全边界：值经 pickle 序列化存储，仅限存放框架/业务内部数据（自产自消）。
# 切勿将外部不可信输入直接作为 value 写入（pickle 反序列化存在代码执行风险）；
# 需要跨实例共享或持久化时请使用 redis_cache（配置 REDIS_URL）。


class SimpleMemoryCache:

    CLEANUP_INTERVAL = 60   # 后台清理线程的执行间隔（秒）

    def __init__(self):
        self.cache = {}
        self.expiry_times = {}
        self._lock = threading.Lock()

    def set(self, key, value, ttl=None):
        """设置键值对，并可选地设置TTL（生存时间）"""
        with self._lock:
            self.cache[key] = pickle.dumps(value)
            if ttl:
                self.expiry_times[key] = time.time() + ttl
            else:
                # 清除可能存在的过期时间
                self.expiry_times.pop(key, None)

    def get(self, key):
        """获取键对应的值，如果键已过期则返回None"""
        with self._lock:
            if self._is_expired(key):
                self._delete(key)
                return None
            if key not in self.cache:
                return None
            try:
                return pickle.loads(self.cache[key])
            except Exception as e:
                Logger.warn(f'SimpleMemoryCache get failed key={key}: {e}')
                self._delete(key)
                return None

    def delete(self, key):
        """删除键"""
        with self._lock:
            self._delete(key)

    def getdel(self, key):
        """原子读取并删除（供 refresh token 轮换等单次使用场景）"""
        with self._lock:
            if self._is_expired(key):
                self._delete(key)
                return None
            if key not in self.cache:
                return None
            value = self.cache.pop(key)
            self.expiry_times.pop(key, None)
            try:
                return pickle.loads(value)
            except Exception as e:
                Logger.warn(f'SimpleMemoryCache getdel failed key={key}: {e}')
                return None

    def incr(self, key, ttl=None):
        """原子自增计数（供防爆破计数等场景；值不存在时从 1 开始），返回新值"""
        with self._lock:
            if self._is_expired(key):
                self._delete(key)
            try:
                cur = int(pickle.loads(self.cache[key])) if key in self.cache else 0
            except Exception:
                cur = 0   # 损坏/非数字值：按 0 重新计数，不抛异常
            new = cur + 1
            self.cache[key] = pickle.dumps(new)
            if ttl:
                self.expiry_times[key] = time.time() + ttl
            else:
                self.expiry_times.pop(key, None)
            return new

    def _delete(self, key):
        """内部删除方法（调用方需持有锁）"""
        if key in self.cache:
            del self.cache[key]
        if key in self.expiry_times:
            del self.expiry_times[key]

    def exists(self, key):
        """检查键是否存在，如果键已过期则返回False"""
        with self._lock:
            if self._is_expired(key):
                self._delete(key)
                return False
            return key in self.cache

    def expire(self, key, ttl):
        """为键设置过期时间"""
        if ttl is None:
            return   # 无 ttl 无意义，直接返回（与 RedisCache.expire 行为一致）
        with self._lock:
            self.expiry_times[key] = time.time() + ttl

    def clear_expired(self):
        """主动清理所有已过期但尚未被访问的键"""
        with self._lock:
            now = time.time()
            # 遍历时用 list 快照，避免其他线程修改 dict 导致 RuntimeError
            expired_keys = [k for k, exp in list(self.expiry_times.items()) if now > exp]
            for k in expired_keys:
                self._delete(k)
            return len(expired_keys)

    def _is_expired(self, key):
        """检查键是否已过期（调用方需持有锁）"""
        if key in self.expiry_times:
            return time.time() > self.expiry_times[key]
        return False


memory_cache = SimpleMemoryCache()


def _start_cleanup_thread():
    """后台守护线程：定期清理已过期的键，防止 TTL 键长期占用内存"""
    def _sweep():
        while True:
            time.sleep(SimpleMemoryCache.CLEANUP_INTERVAL)
            try:
                memory_cache.clear_expired()
            except Exception as e:
                Logger.warn(f'SimpleMemoryCache cleanup failed: {e}')

    t = threading.Thread(target=_sweep, daemon=True, name='memory-cache-cleanup')
    t.start()


_start_cleanup_thread()
