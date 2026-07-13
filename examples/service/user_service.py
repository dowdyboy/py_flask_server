from flask_server.module import sqlalchemy, sqlalchemy_trans
from sqlalchemy import and_
from flask_server.model import UserPO
from flask_server.util import DataEncryptUtil, GraceResult, RandomGenerator, Logger
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

class UserService:

    @staticmethod
    @sqlalchemy_trans
    def login(username, password):
        password = DataEncryptUtil.sha256(password)
        Logger.info(f'UserService login: username({username}), password({password})')
        user = UserPO.query.filter(and_(
            UserPO.username == username, UserPO.passwd == password
        )).first()
        if user is None:
            return None
        user.last_login_time = datetime.now()
        token = RandomGenerator.secrets_token(32)
        memory_cache.set(token, user.uid)
        Logger.info(f'token set ({token}, {user.uid})')
        return token

    @staticmethod
    @sqlalchemy_trans
    def list():
        users = UserPO.query.all()
        Logger.info(f'UserService list: {len(users)} users')
        return users

    @staticmethod
    @sqlalchemy_trans
    def add():
        u = UserPO()
        u.uid = '1'
        u.username = 'admin'
        u.passwd = DataEncryptUtil.sha256('change-this-password')
        u.last_login_time = datetime.now()
        u.create_time = datetime.now()
        sqlalchemy().session.add(u)

