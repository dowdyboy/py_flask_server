from functools import wraps
import threading
import time
from datetime import datetime
from flask import request, jsonify

from ..config import config, CONFIG_EXT_KEY
from ..module import memory_cache, redis_cache
from ..util import DataEncryptUtil, RandomGenerator, CommonUtil, Logger, GraceResult, ErrorCode
from . import metrics

# 认证模块骨架
#
# 功能：
#   - 注册/登录/登出 + Access/Refresh Token 双令牌（refresh 轮换，单次使用）
#   - 登录防爆破：连续失败 N 次锁定 M 秒
#   - @login_required 装饰器（保护单个视图）
#   - AUTH_ENABLED=true 时全局拦截 /api/ 下未豁免路径（默认豁免 auth/docs/健康检查）
#
# 存储：
#   - AUTH_STORE=memory（默认）：进程内用户表，零配置可跑，重启即失
#   - AUTH_STORE=sqlalchemy：复用 model/po/user.py 的 UserPO（需配置 DB 并迁移建表）

# 全局拦截默认豁免路径
AUTH_EXEMPT_PATHS = (
    '/api/v1/auth/',
    '/docs',
    '/openapi.json',
    '/api/v1/health',
    '/api/v1/healthz',
    '/api/v1/readyz',
)

_TOKEN_KEY_PREFIX = 'auth:token:'
_REFRESH_TOKEN_KEY_PREFIX = 'auth:refresh:'
_LOGIN_FAIL_KEY_PREFIX = 'auth:login_fail:'
_LOGIN_LOCK_KEY_PREFIX = 'auth:login_lock:'
_LOGIN_FAIL_GLOBAL_PREFIX = 'auth:login_fail_global:'
# 按用户维护的已签发令牌索引：支持"改密后旧会话全部失效"/"登出所有设备"。
# 索引本身也是缓存值（JSON 列表），进程内更新加锁；多进程下为尽力而为（新登录通常
# 落在同一 worker，改密/全端登出会在当前 worker 读到自己的列表）。
_USER_ACCESS_KEY_PREFIX = 'auth:user_access:'
_USER_REFRESH_KEY_PREFIX = 'auth:user_refresh:'
_REFRESH_USED_KEY_PREFIX = 'auth:refresh_used:'
_TOKEN_INDEX_LOCK = threading.Lock()
_MAX_TOKENS_PER_USER = 20
# 每个 app 独立的用户存储（create_app 配置注入时 AUTH_STORE 可不同）
_AUTH_STORE_EXT_KEY = 'flask_server.auth_store'
# 惰性创建 store 的锁：避免并发首次请求各自 build 一个 MemoryAuthStore，
# 后写覆盖先写，导致先注册的用户静默丢失（check-then-act 竞态）。
_AUTH_STORE_CREATE_LOCK = threading.Lock()

# 同一用户名跨 IP 的失败总数达到该值即告警（只观测不阻断）：
# 按 (用户名, IP) 锁定可防止单点把账号锁死，但分布式撞库仍会绕过单 IP 阈值，
# 这里给出可观测信号，真正的拦截交给限流/WAF
_GLOBAL_FAIL_WARN_THRESHOLD = 50

# 时序均衡：用户不存在时也执行一次 PBKDF2 校验（虚拟哈希），
# 消除"用户名是否存在"的可观测时序差（防用户名枚举）。
#
# 虚拟哈希必须与**当前配置**的迭代次数一致：若在导入期按默认配置算死，
# 而请求期通过 create_app(framework_config=Config({'auth_pbkdf2_iterations': 10000}))
# 注入了更小的迭代数，那么"用户不存在"会烧 600k 次 PBKDF2、比真实校验慢几十倍——
# 时序差不但没消除，反而变成了可放大的 CPU 消耗点。这里按迭代数缓存、懒计算。
_DUMMY_HASH_LOCK = threading.Lock()
_DUMMY_HASH_CACHE: dict = {}


def _dummy_password_hash():
    """按当前 AUTH_PBKDF2_ITERATIONS 取（并缓存）虚拟哈希"""
    iterations = int(config.auth_pbkdf2_iterations)
    cached = _DUMMY_HASH_CACHE.get(iterations)
    if cached is not None:
        return cached
    with _DUMMY_HASH_LOCK:
        cached = _DUMMY_HASH_CACHE.get(iterations)
        if cached is None:
            cached = DataEncryptUtil.pbkdf2_hmac(
                'dummy-timing-equalizer', salt='0' * 32, iterations=iterations,
            )
            _DUMMY_HASH_CACHE[iterations] = cached
    return cached


def _client_ip():
    """当前请求的来源 IP（无请求上下文时返回 'unknown'）。

    用于把登录失败计数/锁定按 (用户名, IP) 隔离：
    若只按用户名计数，攻击者遍历已知用户名各打 N 次错误密码即可把账号逐个锁死
    （锁定型 DoS）——受害者在自己 IP 上也会被 429 拒绝。
    """
    from flask import has_request_context
    if not has_request_context():
        return 'unknown'
    return CommonUtil.get_real_ip(request, config.trusted_proxies) or 'unknown'


def _login_fail_key(username, client_ip):
    return f'{_LOGIN_FAIL_KEY_PREFIX}{username}:{client_ip}'


def _login_lock_key(username, client_ip):
    return f'{_LOGIN_LOCK_KEY_PREFIX}{username}:{client_ip}'


def _token_cache():
    """认证令牌/防爆破计数的首选存储：配置 REDIS_URL 时用 Redis（多实例共享），否则进程内内存。"""
    if config.redis_url is not None and redis_cache is not None:
        return redis_cache
    return memory_cache


def _cache_set(key, value, ttl=None):
    """写入认证缓存（带降级兜底）。

    Redis 不可达时（redis_cache 冷却期，set 返回 False）回退写入内存缓存，
    保证单实例下登录/防爆破仍可用；Redis 恢复后新写入自动回到 Redis。
    降级不逐条告警（RedisCache 已在故障开始/恢复时告警过，避免冷却期内刷屏）。
    """
    cache = _token_cache()
    if cache.set(key, value, ttl=ttl):
        return True
    if cache is memory_cache:
        return False   # 内存写入失败（不应发生），避免自兜底死循环
    return memory_cache.set(key, value, ttl=ttl)


def _cache_get(key):
    """读取认证缓存：主存储 miss 时查内存兜底（与 _cache_set 的回退对称）。"""
    cache = _token_cache()
    value = cache.get(key)
    if value is not None or cache is memory_cache:
        return value
    return memory_cache.get(key)


def _cache_getdel(key):
    """原子读取并删除（refresh token 单次使用轮换）。

    主存储为 Redis 时 miss 会查内存兜底（与 _cache_set 的回退对称）并同步清理。
    """
    cache = _token_cache()
    value = cache.getdel(key)
    if value is not None or cache is memory_cache:
        return value
    return memory_cache.getdel(key)


def _cache_incr(key, ttl=None):
    """原子自增（防爆破计数）：Redis 主存储不可用时回退内存计数（单实例安全）"""
    cache = _token_cache()
    count = cache.incr(key, ttl=ttl)
    if count is not None or cache is memory_cache:
        return count
    return memory_cache.incr(key, ttl=ttl)


def _cache_delete(key, what='认证缓存'):
    """删除认证缓存：主存储与内存兜底都清理，防止轮换/登出后残留。

    登出/refresh 吊销对失败零容忍：RedisCache.delete 在冷却期内也会真实尝试，
    但连接故障时仍可能失败（返回 False）。此时必须告警——否则旧令牌会在 Redis
    恢复后重新生效（TTL 7/30 天），表现为"登出没生效"。

    Args:
        key (str): 缓存键（含 token 明文，日志只输出前缀，避免泄漏）
        what (str): 用途描述（access token / refresh token 等），用于告警文案

    Returns:
        bool: 主存储删除是否成功；内存缓存不可失败，恒为 True
    """
    cache = _token_cache()
    ok = cache.delete(key)
    if config.redis_url is not None:
        memory_cache.delete(key)
    if cache is memory_cache:
        return True
    if not ok:
        Logger.warn(f'AuthService: {what} revocation did not reach primary store '
                    f'(key prefix {key.rsplit(":", 1)[0]}); it may stay valid until TTL')
    return bool(ok)

def _append_token_index(index_key, token, token_key_prefix, ttl):
    """把已签发 token 记入用户索引（调用方持锁）；超过上限时淘汰最旧并真正删除其 token"""
    tokens = _cache_get(index_key)
    if not isinstance(tokens, list):
        tokens = []
    tokens.append(token)
    while len(tokens) > _MAX_TOKENS_PER_USER:
        stale = tokens.pop(0)
        _cache_delete(f'{token_key_prefix}{stale}', what='evicted token')
    _cache_set(index_key, tokens, ttl=ttl)


def _remember_issued_tokens(uid, access, refresh):
    """登录/续期后登记 token（用于改密与"登出所有设备"时批量吊销）"""
    if not uid:
        return
    with _TOKEN_INDEX_LOCK:
        _append_token_index(f'{_USER_ACCESS_KEY_PREFIX}{uid}', access,
                            _TOKEN_KEY_PREFIX, config.auth_token_ttl)
        _append_token_index(f'{_USER_REFRESH_KEY_PREFIX}{uid}', refresh,
                            _REFRESH_TOKEN_KEY_PREFIX, config.auth_refresh_token_ttl)


def _forget_issued_token(uid, token, index_prefix):
    if not uid or not token:
        return
    with _TOKEN_INDEX_LOCK:
        key = f'{index_prefix}{uid}'
        tokens = _cache_get(key)
        if isinstance(tokens, list) and token in tokens:
            tokens.remove(token)
            _cache_set(key, tokens)


def _revoke_all_tokens(uid):
    """吊销某用户的全部 access/refresh token（改密、登出所有设备）"""
    with _TOKEN_INDEX_LOCK:
        for index_prefix, token_prefix in (
                (_USER_ACCESS_KEY_PREFIX, _TOKEN_KEY_PREFIX),
                (_USER_REFRESH_KEY_PREFIX, _REFRESH_TOKEN_KEY_PREFIX)):
            key = f'{index_prefix}{uid}'
            tokens = _cache_get(key)
            if isinstance(tokens, list):
                for token in tokens:
                    _cache_delete(f'{token_prefix}{token}', what='revoked token')
            _cache_delete(key)



# ------------------------- AuthUser -------------------------


class AuthUser:
    """认证用户（与存储层解耦的简单对象）"""

    def __init__(self, uid, username, passwd, create_time):
        self.uid = uid
        self.username = username
        self.passwd = passwd
        self.create_time = create_time


# ------------------------- AuthStore -------------------------


class MemoryAuthStore:
    """进程内用户存储（默认，无需数据库）"""

    def __init__(self):
        self._users = {}   # username -> AuthUser
        self._by_uid = {}  # uid -> AuthUser
        self._lock = threading.Lock()

    def create(self, username, passwd_hash):
        with self._lock:
            if username in self._users:
                raise ValueError('username already exists')
            uid = RandomGenerator.secrets_token(16)
            user = AuthUser(uid, username, passwd_hash, datetime.now())
            self._users[username] = user
            self._by_uid[uid] = user
            return uid

    def get_by_username(self, username):
        return self._users.get(username)

    def get_by_uid(self, uid):
        return self._by_uid.get(uid)

    def update_password(self, username, passwd_hash):
        """更新密码哈希；用户不存在返回 False"""
        with self._lock:
            user = self._users.get(username)
            if user is None:
                return False
            # AuthUser 对象同时被 _by_uid 引用，原地更新即可
            user.passwd = passwd_hash
            return True


# UserPO 占位类 → 真实 ORM 模型的重绑锁：并发首次认证请求只允许一个线程执行 reload
_PO_RELOAD_LOCK = threading.Lock()


class SqlAlchemyAuthStore:
    """SQLAlchemy 用户存储（AUTH_STORE=sqlalchemy，需配置 SQLALCHEMY_URI）"""

    @staticmethod
    def _get_po():
        """取当前可用的 UserPO（占位类会在 db 就绪后惰性重绑为真实 ORM 模型）。

        flask_server/model/po/user.py 在**首次导入**时按 `sqlalchemy()` 的返回值决定定义：
        db 为 None（进程默认未配置 SQLALCHEMY_URI）时定义的是没有 query 的占位类。
        之后 create_app(framework_config=...) 注入 DB 配置并初始化 db 时，该模块早已
        被 import 缓存，import_module 只会返回旧的占位类，不会重新执行模块代码。

        因此这里检测到占位类后用 importlib.reload 重新执行模块（此时 sqlalchemy() 已返回
        真实 db，UserPO 变成 ORM 模型）。reload 后仍是占位类说明 DB 确实未就绪，抛
        RuntimeError 给出明确原因，而不是等到 `.query` 时 AttributeError → 500。
        """
        import importlib

        from flask_server.module.sqlalchemy import sqlalchemy
        from flask_server.module.container import get_db

        user_module = importlib.import_module('flask_server.model.po.user')

        def _bound_to_db():
            # 不能用 hasattr(UserPO, 'query') 判断：真实模型的 query 是 Flask-SQLAlchemy
            # 的描述符，在 app 上下文外访问会抛 RuntimeError（本方法常在 app 上下文外调用）
            db = get_db(default=sqlalchemy())
            po = user_module.UserPO
            return db is not None and isinstance(po, type) and issubclass(po, db.Model)

        if _bound_to_db():
            return user_module.UserPO
        with _PO_RELOAD_LOCK:
            if not _bound_to_db():      # 等锁期间可能已被其它线程重载
                importlib.reload(user_module)
        if not _bound_to_db():
            raise RuntimeError(
                'UserPO 仍是占位模型：SQLAlchemy 未就绪'
                '（请配置 SQLALCHEMY_URI，并确保 create_app/init_SQLAlchemy 已执行）')
        return user_module.UserPO

    @staticmethod
    def create(username, passwd_hash):
        from flask_server.module.sqlalchemy import sqlalchemy, sqlalchemy_trans
        from sqlalchemy.exc import IntegrityError
        UserPO = SqlAlchemyAuthStore._get_po()

        @sqlalchemy_trans
        def _do_create():
            existing = UserPO.query.filter(UserPO.username == username).first()
            if existing is not None:
                raise ValueError('username already exists')
            uid = RandomGenerator.secrets_token(16)
            user = UserPO()
            user.uid = uid
            user.username = username
            user.passwd = passwd_hash
            user.create_time = datetime.now()
            sqlalchemy().session.add(user)
            return uid

        try:
            return _do_create()
        except IntegrityError:
            # 并发重复注册：先查重后插入存在 TOCTOU 竞态，唯一索引兜底
            # （@sqlalchemy_trans 已回滚），映射为与预检一致的重复注册语义
            raise ValueError('username already exists')

    @staticmethod
    def get_by_username(username):
        from flask_server.module.sqlalchemy import in_app_context
        UserPO = SqlAlchemyAuthStore._get_po()

        def _query():
            user = UserPO.query.filter(UserPO.username == username).first()
            if user is None:
                return None
            return AuthUser(user.uid, user.username, user.passwd, user.create_time)

        return in_app_context(_query)

    @staticmethod
    def get_by_uid(uid):
        from flask_server.module.sqlalchemy import in_app_context
        UserPO = SqlAlchemyAuthStore._get_po()

        def _query():
            user = UserPO.query.filter(UserPO.uid == uid).first()
            if user is None:
                return None
            return AuthUser(user.uid, user.username, user.passwd, user.create_time)

        return in_app_context(_query)

    @staticmethod
    def update_password(username, passwd_hash):
        from flask_server.module.sqlalchemy import sqlalchemy_trans
        UserPO = SqlAlchemyAuthStore._get_po()

        @sqlalchemy_trans
        def _do():
            user = UserPO.query.filter(UserPO.username == username).first()
            if user is None:
                return False
            user.passwd = passwd_hash
            return True

        return _do()


# ------------------------- AuthService -------------------------

class AuthService:

    # 进程默认用户存储（无 app 上下文时使用，如 CLI/后台线程/测试直调）。
    # 有 app 上下文时优先使用 app.extensions 中按该 app 配置构建的 store，
    # 避免同进程多个 create_app(framework_config=...) 共用 AUTH_STORE。
    _store = None

    @classmethod
    def _build_store(cls, cfg):
        """按配置构建用户存储；AUTH_STORE=sqlalchemy 漏配 URI 时给出清晰报错。"""
        if cfg.auth_store == 'sqlalchemy':
            if cfg.sqlalchemy_uri is None:
                raise RuntimeError(
                    'AUTH_STORE=sqlalchemy 需要配置 SQLALCHEMY_URI（并执行 flask db upgrade 建表）')
            return SqlAlchemyAuthStore()
        return MemoryAuthStore()

    @classmethod
    def _get_store(cls):
        # 请求/任务 app 上下文内：使用该 app 的 store（按 app 配置隔离）
        try:
            from flask import current_app, has_app_context
            if has_app_context():
                extensions = current_app.extensions
                store = extensions.get(_AUTH_STORE_EXT_KEY)
                if store is not None:
                    return store
                cfg = extensions.get(CONFIG_EXT_KEY)
                if cfg is not None:
                    with _AUTH_STORE_CREATE_LOCK:
                        store = extensions.get(_AUTH_STORE_EXT_KEY)
                        if store is None:
                            store = cls._build_store(cfg)
                            extensions[_AUTH_STORE_EXT_KEY] = store
                    return store
        except ImportError:      # Flask 未安装的极端场景：回退进程默认
            pass

        # 无 app 上下文（CLI/后台线程/服务层直测）：使用进程默认 store
        if cls._store is None:
            from flask_server.config import config as config_proxy
            cls._store = cls._build_store(config_proxy)
        return cls._store

    @classmethod
    def register(cls, username, password):
        """注册用户，返回 uid；用户名重复抛 ValueError"""
        store = cls._get_store()
        passwd_hash = DataEncryptUtil.pbkdf2_hmac(password)
        uid = store.create(username, passwd_hash)
        Logger.info(f'AuthService register: uid={uid}, username={username}')
        return uid

    @classmethod
    def _is_login_locked(cls, username, client_ip):
        """该 (用户名, IP) 是否处于登录锁定状态（连续失败达阈值）"""
        lock_key = _login_lock_key(username, client_ip)
        lock_until = _cache_get(lock_key)
        if lock_until is None:
            return False
        if time.time() < lock_until:
            return True
        # 锁定已过期，清除
        _cache_delete(lock_key)
        return False

    @classmethod
    def login_lock_remaining(cls, username, client_ip=None):
        """该 (用户名, IP) 的锁定剩余秒数；未锁定时返回 None（供 429 的 Retry-After 使用）"""
        client_ip = client_ip or _client_ip()
        lock_until = _cache_get(_login_lock_key(username, client_ip))
        if lock_until is None:
            return None
        remaining = int(float(lock_until) - time.time())
        return max(1, remaining)

    @classmethod
    def _record_login_fail(cls, username, client_ip):
        """记录一次登录失败（原子自增，incr 已持久化并带 TTL），达阈值则锁定该 (user, ip)"""
        fail_key = _login_fail_key(username, client_ip)
        count = _cache_incr(fail_key, ttl=config.auth_login_lock_seconds)
        if count is None:
            count = 0   # 缓存不可用（不应发生）：跳过计数，不阻塞登录流程
        # 全局（跨 IP）失败计数：仅用于观测分布式撞库，不参与阻断
        global_count = _cache_incr(f'{_LOGIN_FAIL_GLOBAL_PREFIX}{username}',
                                   ttl=config.auth_login_lock_seconds)
        if global_count is not None and global_count == _GLOBAL_FAIL_WARN_THRESHOLD:
            Logger.warn(f'AuthService: {username} accumulated {global_count} login failures '
                        f'across different IPs in {config.auth_login_lock_seconds}s '
                        '(possible distributed brute force; check RATE_LIMIT/WAF)')
        if count >= config.auth_login_max_fails:
            _cache_set(_login_lock_key(username, client_ip),
                       time.time() + config.auth_login_lock_seconds,
                       ttl=config.auth_login_lock_seconds)
            _cache_delete(fail_key)

    @classmethod
    def _clear_login_fail(cls, username, client_ip):
        _cache_delete(_login_fail_key(username, client_ip))

    @classmethod
    def login(cls, username, password, client_ip=None):
        """校验用户名密码，返回 `(tokens, err)` 二元组。

        Returns:
            tuple: `((access_token, refresh_token), None)` 表示成功；
            `(None, 'locked')` 表示已被锁定（调用方应返回 429 / code 4003）；
            `(None, 'invalid')` 表示用户名或密码错误（应返回 401 / code 4001）。
            统一返回二元组是为了让控制器区分"锁定"和"密码错"——早期实现只返回
            `None`，无法区分两种失败原因。

        防爆破：同一 (用户名, 来源 IP) 连续失败 AUTH_LOGIN_MAX_FAILS 次后锁定
        AUTH_LOGIN_LOCK_SECONDS 秒，锁定期间即使密码正确也拒绝。
        按 IP 隔离是为了避免"攻击者故意打错密码把受害者账号锁死"（锁定型 DoS）。

        Args:
            username (str): 用户名
            password (str): 明文密码
            client_ip (str, optional): 来源 IP。为 None 时从当前请求上下文取
        """
        client_ip = client_ip or _client_ip()
        if cls._is_login_locked(username, client_ip):
            Logger.warn(f'AuthService login blocked (locked): {username} ip={client_ip}')
            # 只计数不按用户名/IP 打标签：账号信息不适合进指标，且会造成高基数
            metrics.inc_login_locked()
            return None, 'locked'
        store = cls._get_store()
        user = store.get_by_username(username)
        if user is None:
            # 时序均衡：用户不存在也执行等价 PBKDF2 校验（防用户名枚举时序侧信道）
            DataEncryptUtil.verify_pbkdf2(password, _dummy_password_hash())
            cls._record_login_fail(username, client_ip)
            return None, 'invalid'
        if not DataEncryptUtil.verify_pbkdf2(password, user.passwd):
            cls._record_login_fail(username, client_ip)
            return None, 'invalid'
        cls._clear_login_fail(username, client_ip)
        access = RandomGenerator.secrets_token(32)
        refresh = RandomGenerator.secrets_token(32)
        _cache_set(f'{_TOKEN_KEY_PREFIX}{access}', user.uid, ttl=config.auth_token_ttl)
        _cache_set(f'{_REFRESH_TOKEN_KEY_PREFIX}{refresh}', user.uid, ttl=config.auth_refresh_token_ttl)
        _remember_issued_tokens(user.uid, access, refresh)
        Logger.info(f'AuthService login: uid={user.uid}')
        return (access, refresh), None

    @classmethod
    def refresh_access_token(cls, refresh_token):
        """用 refresh_token 换取新令牌（轮换：旧 refresh 原子作废，单次使用）

        重用检测：已使用过的 refresh_token 若再次出现（旧值被窃取/重放），视为令牌泄露，
        吊销该用户全部会话（access + refresh），返回 None。客户端网络重试造成的一次
        误判会要求重新登录，这是安全优先的取舍。
        """
        if not refresh_token:
            return None
        used_key = f'{_REFRESH_USED_KEY_PREFIX}{refresh_token}'
        # 原子读取并删除（GETDEL）：并发使用同一 refresh_token 时仅一次成功
        uid = _cache_getdel(f'{_REFRESH_TOKEN_KEY_PREFIX}{refresh_token}')
        if uid is None:
            replayed_uid = _cache_get(used_key)
            if replayed_uid is not None:
                Logger.warn(f'AuthService refresh token reuse detected for uid={replayed_uid}; '
                            'revoking all sessions of this user')
                _revoke_all_tokens(replayed_uid)
            return None
        # 记录"该 refresh 已被使用"，用于识别后续重放（窗口取 access token TTL 的较小值）
        _cache_set(used_key, uid, ttl=300)
        # 用户已被删除（或存储被重置）时不再签发新令牌：否则调用方按 uid 查库得到 None，
        # 会在取 user.uid 时抛 AttributeError → 500（历史缺陷）
        if cls.get_user_by_uid(uid) is None:
            Logger.warn(f'AuthService refresh rejected: uid={uid} no longer exists')
            return None
        access = RandomGenerator.secrets_token(32)
        new_refresh = RandomGenerator.secrets_token(32)
        _cache_set(f'{_TOKEN_KEY_PREFIX}{access}', uid, ttl=config.auth_token_ttl)
        _cache_set(f'{_REFRESH_TOKEN_KEY_PREFIX}{new_refresh}', uid, ttl=config.auth_refresh_token_ttl)
        _forget_issued_token(uid, refresh_token, _USER_REFRESH_KEY_PREFIX)
        _remember_issued_tokens(uid, access, new_refresh)
        Logger.info(f'AuthService refresh: uid={uid}')
        return access, new_refresh

    @classmethod
    def logout(cls, token, refresh_token=None):
        """登出：删除 access token；传入 refresh_token 时一并吊销（否则其到期自然失效）。

        方案 A：logout 请求可携带可选 refresh_token，吊销后旧 refresh 无法再换取新令牌——
        "登出"后会话真正结束（设备丢失/账号被盗场景可踢下线）。

        吊销失败（Redis 故障）由 _cache_delete 告警，但不阻塞登出响应：
        本地会话状态已不可恢复，报错也帮不上用户，告警用于运维发现"登出未生效"。
        """
        if token:
            uid = _cache_get(f'{_TOKEN_KEY_PREFIX}{token}')
            _cache_delete(f'{_TOKEN_KEY_PREFIX}{token}', what='access token')
            _forget_issued_token(uid, token, _USER_ACCESS_KEY_PREFIX)
        if refresh_token:
            uid = _cache_get(f'{_REFRESH_TOKEN_KEY_PREFIX}{refresh_token}')
            _cache_delete(f'{_REFRESH_TOKEN_KEY_PREFIX}{refresh_token}', what='refresh token')
            _forget_issued_token(uid, refresh_token, _USER_REFRESH_KEY_PREFIX)

    @classmethod
    def logout_all(cls, uid):
        """吊销该用户全部 access/refresh token（登出所有设备）"""
        if not uid:
            return
        _revoke_all_tokens(uid)
        Logger.info(f'AuthService logout all sessions: uid={uid}')

    @classmethod
    def change_password(cls, username, old_password, new_password):
        """校验旧密码后更新密码，并吊销该用户全部会话。

        Returns:
            tuple: `(True, None)` 成功；`(False, 'not_found' | 'invalid')` 失败
                （`invalid` = 旧密码错误；调用方建议返回 401，避免暴露账号是否存在）
        """
        store = cls._get_store()
        user = store.get_by_username(username)
        if user is None:
            # 与登录一致：用户不存在也做一次等价 PBKDF2，避免用户名枚举时序差
            DataEncryptUtil.verify_pbkdf2(old_password, _dummy_password_hash())
            return False, 'not_found'
        if not DataEncryptUtil.verify_pbkdf2(old_password, user.passwd):
            return False, 'invalid'
        update = getattr(store, 'update_password', None)
        if update is None:
            raise RuntimeError('当前用户存储不支持修改密码（缺少 update_password）')
        new_hash = DataEncryptUtil.pbkdf2_hmac(new_password)
        if not update(username, new_hash):
            return False, 'not_found'
        _revoke_all_tokens(user.uid)
        Logger.info(f'AuthService password changed: uid={user.uid}')
        return True, None

    @classmethod
    def get_user_by_token(cls, token):
        """按 token 获取用户（未登录返回 None）"""
        if not token:
            return None
        uid = _cache_get(f'{_TOKEN_KEY_PREFIX}{token}')
        if uid is None:
            return None
        return cls.get_user_by_uid(uid)

    @classmethod
    def get_user_by_uid(cls, uid):
        """按 uid 获取用户（不存在返回 None）"""
        if not uid:
            return None
        return cls._get_store().get_by_uid(uid)


# ------------------------- 装饰器与拦截器 -------------------------

def _unauthorized_response():
    return jsonify(CommonUtil.obj_to_dict(
        GraceResult.business_error(ErrorCode.UNAUTHORIZED, '未登录或 Token 已过期'))), 401


def login_required(func):
    """视图保护装饰器：校验 X-AUTH-TOKEN，通过后写入 request.info['uid']"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        user = AuthService.get_user_by_token(request.headers.get('X-AUTH-TOKEN'))
        if user is None:
            return _unauthorized_response()
        request.info['uid'] = user.uid
        request.info['username'] = user.username
        return func(*args, **kwargs)

    return wrapper


def _is_exempt_path(path):
    """豁免路径精确匹配：整段路径匹配或子路径（exempt/xxx）匹配（大小写不敏感）。

    修复前用 startswith 前缀匹配，`/docsanything`、`/api/v1/authx/...` 也会被误豁免。
    """
    path_lower = path.lower()
    for exempt in AUTH_EXEMPT_PATHS:
        e = exempt.rstrip('/').lower()
        if path_lower == e or path_lower.startswith(e + '/'):
            return True
    return False


def auth_interceptor():
    """全局认证拦截（AUTH_ENABLED=true 时注册为 before_request）：
    对 /api/ 下未豁免路径要求有效 token"""
    # CORS 预检（OPTIONS）不携带凭据，必须放行由 CORS 层处理，
    # 否则跨域前端的所有 API 调用都会在 preflight 阶段被 401 拦截
    if request.method == 'OPTIONS':
        return None
    # 前缀匹配大小写不敏感：防止 /API/... 之类变体绕过全局认证
    path = request.path
    if not path.lower().startswith('/api/'):
        return None
    if _is_exempt_path(path):
        return None
    user = AuthService.get_user_by_token(request.headers.get('X-AUTH-TOKEN'))
    if user is None:
        return _unauthorized_response()
    request.info['uid'] = user.uid
    request.info['username'] = user.username
    return None


# AUTH_ENABLED=true 时启用全局认证保护（由 create_app → init_auth 注册）
def init_auth(app, cfg=None):
    """把全局认证拦截器注册到指定 app（AUTH_ENABLED=false 时不注册）

    启动期会**立即构建用户存储**：AUTH_STORE=sqlalchemy 漏配 SQLALCHEMY_URI 时
    直接让 create_app 失败，而不是等到第一个注册/登录请求才 500（并把错误推迟到
    流量进来之后）。同时把 store 预置到 app.extensions，消除首次并发请求的建店竞态。

    Args:
        app (Flask): 目标应用
        cfg (Config, optional): 应用的框架配置；缺省用当前 config 代理解析
    """
    cfg = cfg if cfg is not None else config
    if not cfg.auth_enabled:
        return
    try:
        app.extensions[_AUTH_STORE_EXT_KEY] = AuthService._build_store(cfg)
    except RuntimeError as e:
        # 配置错误在启动期暴露：容器直接失败退出，比线上首个登录 500 容易定位得多
        raise RuntimeError(f'认证模块初始化失败：{e}') from e
    app.before_request(auth_interceptor)
    Logger.info('Auth module: global auth interceptor enabled (AUTH_ENABLED=true)')
