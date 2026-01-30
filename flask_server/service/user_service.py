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

class UserService:

    @staticmethod
    @sqlalchemy_trans
    def login(username, password):
        password = DataEncryptUtil.sha1(password)
        Logger.info(f'UserService login: username({username}), password({password})')
        user = UserPO.query.filter(and_(
            UserPO.username == username, UserPO.passwd == password
        )).first()
        if user is None:
            return None
        user.last_login_time = datetime.now()
        token = RandomGenerator.random_string(32)
        memory_cache.set(token, user.uid)
        Logger.info(f'token set ({token}, {user.uid})')
        return token

    @staticmethod
    def list():
        print(UserPO.query.all())

    @staticmethod
    @sqlalchemy_trans
    def add():
        u = UserPO()
        u.uid = '1'
        u.username = 'admin'
        u.passwd = DataEncryptUtil.sha1('710671007lf')
        u.last_login_time = datetime.now()
        u.create_time = datetime.now()
        sqlalchemy().session.add(u)

