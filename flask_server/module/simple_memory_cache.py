"""简单的进程内缓存（线程安全、TTL + 容量上限）

容量上限：
    - 模块级默认 `DEFAULT_MAX_ENTRIES = 10_000`，可用 `SimpleMemoryCache(max_entries=...)`
      为单个缓存实例覆盖；模块级单例 `memory_cache` 使用默认值。
    - 每次写入后若条目数超过上限，按**写入顺序**从最旧开始淘汰，直到回到上限以内。
      写入/覆盖都会把该键移到队尾（least-recently-written），因此刚写入的键不会被
      本次插入挤掉；`get` 只读取、不改变淘汰顺序。
    - 淘汰是限频告警的（每累计 `EVICTION_WARN_INTERVAL` 个键打一条 `Logger.warn`），
      运维可据此判断缓存是否长期欠配，而不是被每条插入的告警刷屏。
    - TTL 键仍由后台清理线程（每 `CLEANUP_INTERVAL` 秒调用 `clear_expired()`）回收；
      容量淘汰与 TTL 过期是两套独立机制，互不替代。

安全边界：值经 pickle 序列化存储，仅限存放框架/业务内部数据（自产自消）。
切勿将外部不可信输入直接作为 value 写入（pickle 反序列化存在代码执行风险）；
需要跨实例共享或持久化时请使用 redis_cache（配置 REDIS_URL）。
"""

import time
import pickle
import threading
from collections import OrderedDict

from ..util import Logger

# 内存缓存默认容量上限：未配置 TTL 的键不会永久增长（超出后淘汰最久未写入的键）
DEFAULT_MAX_ENTRIES = 10_000


class SimpleMemoryCache:

    CLEANUP_INTERVAL = 60   # 后台清理线程的执行间隔（秒）
    # 每累计淘汰多少个键告警一次：只观测、不逐条刷屏（见 _evict_if_needed）
    EVICTION_WARN_INTERVAL = 1000

    def __init__(self, max_entries=None):
        """
        Args:
            max_entries (int, optional): 条目数上限（默认 DEFAULT_MAX_ENTRIES，须 > 0）
        """
        self.max_entries = DEFAULT_MAX_ENTRIES if max_entries is None else int(max_entries)
        if self.max_entries <= 0:
            raise ValueError('max_entries 必须 > 0（不限容量请传入足够大的值）')
        self.cache = {}
        self.expiry_times = {}
        # 写入顺序（最旧在队首）：淘汰从队首取键；每次写入/覆盖把键移到队尾，
        # 保证"刚写入的键"永远不会成为本次插入的淘汰对象。
        self._order = OrderedDict()
        self._lock = threading.Lock()
        self._evicted_total = 0
        self._evicted_since_warn = 0

    def set(self, key, value, ttl=None):
        """设置键值对，并可选地设置TTL（生存时间）；返回 True 表示写入成功
        （与 RedisCache.set 契约一致，供调用方判断缓存可用性/降级）"""
        with self._lock:
            self.cache[key] = pickle.dumps(value)
            self._touch(key)
            if ttl:
                self.expiry_times[key] = time.time() + ttl
            else:
                # 清除可能存在的过期时间
                self.expiry_times.pop(key, None)
            self._evict_if_needed()
        return True

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
            self._delete(key)     # 同步清理 expiry_times 与写入顺序
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
            self._touch(key)
            if ttl:
                self.expiry_times[key] = time.time() + ttl
            else:
                self.expiry_times.pop(key, None)
            self._evict_if_needed()
            return new

    def _touch(self, key):
        """记录/刷新写入顺序（调用方需持有锁）：覆盖写也要移到队尾，避免被自己淘汰"""
        self._order[key] = None
        self._order.move_to_end(key)

    def _evict_if_needed(self):
        """容量超限时淘汰最久未写入的键（调用方需持有锁）

        刚写入的键已由 `_touch` 移到队尾，因此不会出现在本次淘汰范围内；
        淘汰只删除缓存本体，不影响其它键的 TTL。
        """
        evicted = 0
        while len(self.cache) > self.max_entries:
            if self._order:
                key, _ = self._order.popitem(last=False)
            else:
                # 兜底：cache 被直接写入（测试注入/历史遗留）时 _order 可能不同步，
                # 此时按 dict 的插入顺序兜底淘汰，保证容量上限仍然生效。
                key = next(iter(self.cache))
            self.cache.pop(key, None)
            self.expiry_times.pop(key, None)
            evicted += 1
        if evicted:
            self._evicted_total += evicted
            self._evicted_since_warn += evicted
            if self._evicted_since_warn >= self.EVICTION_WARN_INTERVAL:
                self._evicted_since_warn = 0
                # 限频告警：能看出容量欠配，又不会每次插入都刷日志
                Logger.warn(
                    f'SimpleMemoryCache 容量已满（max_entries={self.max_entries}），'
                    f'累计淘汰 {self._evicted_total} 个最久未写入的键；'
                    '请缩短 TTL 或调大 max_entries'
                )

    def _delete(self, key):
        """内部删除方法（调用方需持有锁）"""
        if key in self.cache:
            del self.cache[key]
        if key in self.expiry_times:
            del self.expiry_times[key]
        self._order.pop(key, None)

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
