from functools import wraps
import threading
import time
from datetime import datetime
from flask import request, jsonify

from ..config import config
from ..module import memory_cache, redis_cache
from ..util import DataEncryptUtil, RandomGenerator, CommonUtil, Logger, GraceResult

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

# 同一用户名跨 IP 的失败总数达到该值即告警（只观测不阻断）：
# 按 (用户名, IP) 锁定可防止单点把账号锁死，但分布式撞库仍会绕过单 IP 阈值，
# 这里给出可观测信号，真正的拦截交给限流/WAF
_GLOBAL_FAIL_WARN_THRESHOLD = 50

# 时序均衡：用户不存在时也执行一次 PBKDF2 校验（虚拟哈希），
# 消除"用户名是否存在"的可观测时序差（防用户名枚举）
_DUMMY_PASSWORD_HASH = DataEncryptUtil.pbkdf2_hmac(
    'dummy-timing-equalizer', salt='0' * 32,
)


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


def _cache_delete(key):
    """删除认证缓存：主存储与内存兜底都清理，防止轮换/登出后残留。"""
    _token_cache().delete(key)
    if config.redis_url is not None:
        memory_cache.delete(key)

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


class SqlAlchemyAuthStore:
    """SQLAlchemy 用户存储（AUTH_STORE=sqlalchemy，需配置 SQLALCHEMY_URI）"""

    @staticmethod
    def _get_po():
        # 每次调用取模块最新定义的 UserPO（flask_server/__init__.py 的加载顺序保证：
        # app → init_SQLAlchemy → model，配置了 SQLALCHEMY_URI 时导入的即为真实 ORM 模型）
        import importlib
        return importlib.import_module('flask_server.model.po.user').UserPO

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


# ------------------------- AuthService -------------------------

class AuthService:

    _store = None

    @classmethod
    def _get_store(cls):
        if cls._store is None:
            from flask_server.config import config
            if config.auth_store == 'sqlalchemy':
                # 配置校验：漏配 SQLALCHEMY_URI 时给出清晰报错，而非运行时 AttributeError
                if config.sqlalchemy_uri is None:
                    raise RuntimeError(
                        'AUTH_STORE=sqlalchemy 需要配置 SQLALCHEMY_URI（并执行 flask db upgrade 建表）')
                cls._store = SqlAlchemyAuthStore()
            else:
                cls._store = MemoryAuthStore()
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
        """校验用户名密码，成功返回 (access_token, refresh_token)，失败返回 None。

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
            return None, 'locked'
        store = cls._get_store()
        user = store.get_by_username(username)
        if user is None:
            # 时序均衡：用户不存在也执行等价 PBKDF2 校验（防用户名枚举时序侧信道）
            DataEncryptUtil.verify_pbkdf2(password, _DUMMY_PASSWORD_HASH)
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
        Logger.info(f'AuthService login: uid={user.uid}')
        return (access, refresh), None

    @classmethod
    def refresh_access_token(cls, refresh_token):
        """用 refresh_token 换取新令牌（轮换：旧 refresh 原子作废，单次使用）"""
        if not refresh_token:
            return None
        # 原子读取并删除（GETDEL）：并发使用同一 refresh_token 时仅一次成功
        uid = _cache_getdel(f'{_REFRESH_TOKEN_KEY_PREFIX}{refresh_token}')
        if uid is None:
            return None
        # 用户已被删除（或存储被重置）时不再签发新令牌：否则调用方按 uid 查库得到 None，
        # 会在取 user.uid 时抛 AttributeError → 500（历史缺陷）
        if cls.get_user_by_uid(uid) is None:
            Logger.warn(f'AuthService refresh rejected: uid={uid} no longer exists')
            return None
        access = RandomGenerator.secrets_token(32)
        new_refresh = RandomGenerator.secrets_token(32)
        _cache_set(f'{_TOKEN_KEY_PREFIX}{access}', uid, ttl=config.auth_token_ttl)
        _cache_set(f'{_REFRESH_TOKEN_KEY_PREFIX}{new_refresh}', uid, ttl=config.auth_refresh_token_ttl)
        Logger.info(f'AuthService refresh: uid={uid}')
        return access, new_refresh

    @classmethod
    def logout(cls, token, refresh_token=None):
        """登出：删除 access token；传入 refresh_token 时一并吊销（否则其到期自然失效）。

        方案 A：logout 请求可携带可选 refresh_token，吊销后旧 refresh 无法再换取新令牌——
        "登出"后会话真正结束（设备丢失/账号被盗场景可踢下线）。
        """
        if token:
            _cache_delete(f'{_TOKEN_KEY_PREFIX}{token}')
        if refresh_token:
            _cache_delete(f'{_REFRESH_TOKEN_KEY_PREFIX}{refresh_token}')

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
        GraceResult.business_error(4002, '未登录或 Token 已过期'))), 401


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
def init_auth(app):
    """把全局认证拦截器注册到指定 app（AUTH_ENABLED=false 时不注册）"""
    if not config.auth_enabled:
        return
    app.before_request(auth_interceptor)
    Logger.info('Auth module: global auth interceptor enabled (AUTH_ENABLED=true)')
