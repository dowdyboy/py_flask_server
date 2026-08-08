from functools import wraps
import threading
import time
from datetime import datetime
from flask import request, jsonify

from flask_server import app, config
from flask_server.module import memory_cache, redis_cache
from flask_server.util import DataEncryptUtil, RandomGenerator, CommonUtil, Logger, GraceResult

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


def _token_cache():
    """认证令牌/防爆破计数的首选存储：配置 REDIS_URL 时用 Redis（多实例共享），否则进程内内存。"""
    if config.redis_url is not None and redis_cache is not None:
        return redis_cache
    return memory_cache


def _cache_set(key, value, ttl=None):
    """写入认证缓存（带降级兜底）。

    Redis 不可达时（redis_cache 冷却期，set 返回 False）回退写入内存缓存，
    保证单实例下登录/防爆破仍可用；Redis 恢复后新写入自动回到 Redis。
    """
    cache = _token_cache()
    if cache.set(key, value, ttl=ttl):
        return True
    if cache is memory_cache:
        return False   # 内存写入失败（不应发生），避免自兜底死循环
    Logger.warn(f'Auth cache set failed, falling back to memory ({key.rsplit(":", 1)[0]}:*)')
    return memory_cache.set(key, value, ttl=ttl)


def _cache_get(key):
    """读取认证缓存：主存储 miss 时查内存兜底（与 _cache_set 的回退对称）。"""
    cache = _token_cache()
    value = cache.get(key)
    if value is not None or cache is memory_cache:
        return value
    return memory_cache.get(key)


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
        # 动态获取：占位模型会在 DB 初始化后 reload 为真实 ORM 模型，
        # 每次调用取最新定义，避免模块加载时机问题
        import importlib
        return importlib.import_module('flask_server.model.po.user').UserPO

    @staticmethod
    def create(username, passwd_hash):
        from flask_server.module.sqlalchemy import sqlalchemy, sqlalchemy_trans
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

        return _do_create()

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
    def _is_login_locked(cls, username):
        """是否处于登录锁定状态（连续失败达阈值）"""
        lock_until = _cache_get(f'{_LOGIN_LOCK_KEY_PREFIX}{username}')
        if lock_until is None:
            return False
        if time.time() < lock_until:
            return True
        # 锁定已过期，清除
        _cache_delete(f'{_LOGIN_LOCK_KEY_PREFIX}{username}')
        return False

    @classmethod
    def _record_login_fail(cls, username):
        """记录一次登录失败，达阈值则锁定"""
        fail_key = f'{_LOGIN_FAIL_KEY_PREFIX}{username}'
        count = (_cache_get(fail_key) or 0) + 1
        if count >= config.auth_login_max_fails:
            _cache_set(f'{_LOGIN_LOCK_KEY_PREFIX}{username}',
                       time.time() + config.auth_login_lock_seconds,
                       ttl=config.auth_login_lock_seconds)
            _cache_delete(fail_key)
        else:
            _cache_set(fail_key, count, ttl=config.auth_login_lock_seconds)

    @classmethod
    def _clear_login_fail(cls, username):
        _cache_delete(f'{_LOGIN_FAIL_KEY_PREFIX}{username}')

    @classmethod
    def login(cls, username, password):
        """校验用户名密码，成功返回 (access_token, refresh_token)，失败返回 None。

        防爆破：连续失败 AUTH_LOGIN_MAX_FAILS 次后锁定 AUTH_LOGIN_LOCK_SECONDS 秒，
        锁定期间即使密码正确也拒绝。
        """
        if cls._is_login_locked(username):
            Logger.warn(f'AuthService login blocked (locked): {username}')
            return None, 'locked'
        store = cls._get_store()
        user = store.get_by_username(username)
        if user is None or not DataEncryptUtil.verify_pbkdf2(password, user.passwd):
            cls._record_login_fail(username)
            return None, 'invalid'
        cls._clear_login_fail(username)
        access = RandomGenerator.secrets_token(32)
        refresh = RandomGenerator.secrets_token(32)
        _cache_set(f'{_TOKEN_KEY_PREFIX}{access}', user.uid, ttl=config.auth_token_ttl)
        _cache_set(f'{_REFRESH_TOKEN_KEY_PREFIX}{refresh}', user.uid, ttl=config.auth_refresh_token_ttl)
        Logger.info(f'AuthService login: uid={user.uid}')
        return (access, refresh), None

    @classmethod
    def refresh_access_token(cls, refresh_token):
        """用 refresh_token 换取新令牌（轮换：旧 refresh 作废，单次使用）"""
        if not refresh_token:
            return None
        uid = _cache_get(f'{_REFRESH_TOKEN_KEY_PREFIX}{refresh_token}')
        if uid is None:
            return None
        _cache_delete(f'{_REFRESH_TOKEN_KEY_PREFIX}{refresh_token}')
        access = RandomGenerator.secrets_token(32)
        new_refresh = RandomGenerator.secrets_token(32)
        _cache_set(f'{_TOKEN_KEY_PREFIX}{access}', uid, ttl=config.auth_token_ttl)
        _cache_set(f'{_REFRESH_TOKEN_KEY_PREFIX}{new_refresh}', uid, ttl=config.auth_refresh_token_ttl)
        Logger.info(f'AuthService refresh: uid={uid}')
        return access, new_refresh

    @classmethod
    def logout(cls, token):
        """登出：删除 access token（refresh token 到期自然失效）"""
        if token:
            _cache_delete(f'{_TOKEN_KEY_PREFIX}{token}')

    @classmethod
    def get_user_by_token(cls, token):
        """按 token 获取用户（未登录返回 None）"""
        if not token:
            return None
        uid = _cache_get(f'{_TOKEN_KEY_PREFIX}{token}')
        if uid is None:
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
    """豁免路径精确匹配：整段路径匹配或子路径（exempt/xxx）匹配。

    修复前用 startswith 前缀匹配，`/docsanything`、`/api/v1/authx/...` 也会被误豁免。
    """
    for exempt in AUTH_EXEMPT_PATHS:
        e = exempt.rstrip('/')
        if path == e or path.startswith(e + '/'):
            return True
    return False


def auth_interceptor():
    """全局认证拦截（AUTH_ENABLED=true 时注册为 before_request）：
    对 /api/ 下未豁免路径要求有效 token"""
    path = request.path
    if not path.startswith('/api/'):
        return None
    if _is_exempt_path(path):
        return None
    user = AuthService.get_user_by_token(request.headers.get('X-AUTH-TOKEN'))
    if user is None:
        return _unauthorized_response()
    request.info['uid'] = user.uid
    request.info['username'] = user.username
    return None


# AUTH_ENABLED=true 时启用全局认证保护
if config.auth_enabled:
    app.before_request(auth_interceptor)
    Logger.info('Auth module: global auth interceptor enabled (AUTH_ENABLED=true)')
