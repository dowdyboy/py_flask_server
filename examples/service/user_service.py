from flask_server.module import sqlalchemy, sqlalchemy_trans
from flask_server.util import DataEncryptUtil, RandomGenerator, Logger
from flask_server.module import memory_cache
from datetime import datetime

# 样例服务
# 展示了如何使用SQLAlchemy访问数据库
# 展示了如何使用内存缓存
# 展示了如何使用加密工具
# 展示了如何使用随机数生成工具
# 展示了如何使用日志工具
# 注意：接入工程前需在 flask_server/service/__init__.py 中导出 UserService，
#       并在 flask_server/model/po/__init__.py 中导出 UserPO

# 样例 token 有效期（秒）。生产请直接对齐配置里的 AUTH_TOKEN_TTL。
TOKEN_TTL_SECONDS = 7 * 24 * 3600


def _get_user_po():
    """延迟导入 UserPO（声明式定义见 examples/model/user_declared.py）"""
    from examples.model.user_declared import UserPO
    return UserPO


class UserService:

    @staticmethod
    @sqlalchemy_trans
    def login(username, password):
        UserPO = _get_user_po()
        Logger.info(f'UserService login: username({username})')
        # 密码按用户名查询后逐个校验（PBKDF2 盐值每用户不同，无法在 SQL 中比对）
        user = UserPO.query.filter(UserPO.username == username).first()
        if user is None or not DataEncryptUtil.verify_pbkdf2(password, user.passwd):
            return None
        user.last_login_time = datetime.now()
        token = RandomGenerator.secrets_token(32)
        # ⚠️ 必须带 TTL：不设过期时间的缓存键永不过期，登录量一大就把内存撑爆
        # （memory_cache 现在有上限并在超限时淘汰最旧项，但 TTL 才是正确的做法）
        memory_cache.set(token, user.uid, ttl=TOKEN_TTL_SECONDS)
        # ⚠️ 绝不把 token 原文写日志：日志会被集中采集/长期留存，等于泄露会话凭据。
        # 需要关联排查时只打印前缀与 uid。
        Logger.info(f'token issued for uid={user.uid} (token prefix={token[:6]}…)')
        return token

    @staticmethod
    @sqlalchemy_trans
    def list():
        UserPO = _get_user_po()
        users = UserPO.query.all()
        Logger.info(f'UserService list: {len(users)} users')
        return users

    @staticmethod
    @sqlalchemy_trans
    def add():
        UserPO = _get_user_po()
        u = UserPO()
        u.uid = '1'
        u.username = 'admin'
        u.passwd = DataEncryptUtil.pbkdf2_hmac('change-this-password')
        u.last_login_time = datetime.now()
        u.create_time = datetime.now()
        sqlalchemy().session.add(u)
