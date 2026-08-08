from functools import wraps
import threading
from datetime import datetime
from flask import request, jsonify

from flask_server import app, config
from flask_server.module import memory_cache
from flask_server.util import DataEncryptUtil, RandomGenerator, CommonUtil, Logger, GraceResult

# 认证模块骨架
#
# 功能：
#   - 注册/登录/登出 + Token 签发（token 存缓存，TTL 可配）
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
        from flask_server.model import UserPO
        return UserPO

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
        UserPO = SqlAlchemyAuthStore._get_po()
        user = UserPO.query.filter(UserPO.username == username).first()
        if user is None:
            return None
        return AuthUser(user.uid, user.username, user.passwd, user.create_time)

    @staticmethod
    def get_by_uid(uid):
        UserPO = SqlAlchemyAuthStore._get_po()
        user = UserPO.query.filter(UserPO.uid == uid).first()
        if user is None:
            return None
        return AuthUser(user.uid, user.username, user.passwd, user.create_time)


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
    def login(cls, username, password):
        """校验用户名密码，成功返回 token，失败返回 None"""
        store = cls._get_store()
        user = store.get_by_username(username)
        if user is None:
            return None
        if not DataEncryptUtil.verify_pbkdf2(password, user.passwd):
            return None
        token = RandomGenerator.secrets_token(32)
        memory_cache.set(f'{_TOKEN_KEY_PREFIX}{token}', user.uid, ttl=config.auth_token_ttl)
        Logger.info(f'AuthService login: uid={user.uid}')
        return token

    @classmethod
    def logout(cls, token):
        """登出：删除 token"""
        if token:
            memory_cache.delete(f'{_TOKEN_KEY_PREFIX}{token}')

    @classmethod
    def get_user_by_token(cls, token):
        """按 token 获取用户（未登录返回 None）"""
        if not token:
            return None
        uid = memory_cache.get(f'{_TOKEN_KEY_PREFIX}{token}')
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


def auth_interceptor():
    """全局认证拦截（AUTH_ENABLED=true 时注册为 before_request）：
    对 /api/ 下未豁免路径要求有效 token"""
    path = request.path
    if not path.startswith('/api/'):
        return None
    for exempt in AUTH_EXEMPT_PATHS:
        if path.startswith(exempt):
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
