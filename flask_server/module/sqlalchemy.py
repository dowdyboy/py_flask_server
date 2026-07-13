from functools import wraps
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from ..config import config
from ..util import Logger

# 使用SQLAlchemy作为数据库访问层，具体使用参考sqlalchemy库
# 本文件中的函数用于初始化SQLAlchemy对象，以及提供一个装饰器用于事务处理

# 脱敏数据库 URI 中的密码部分，避免明文密码写入日志
def _mask_uri(uri):
    if uri is None:
        return None
    # 匹配 scheme://user:password@host 格式，将 password 替换为 ***
    import re
    return re.sub(r'(://[^:]+:)[^@]+(@)', r'\1***\2', uri)

Logger.info(f'SQLALCHEMY_DATABASE_URI : {_mask_uri(config.sqlalchemy_uri)}')

db: SQLAlchemy = None
migrate: Migrate = None


# 初始化SQLAlchemy对象，并根据配置决定是否反射数据库表结构
def init_SQLAlchemy(app):
    global db
    if config.sqlalchemy_uri is not None and db is None:
        Logger.info(f'init_SQLAlchemy : {_mask_uri(config.sqlalchemy_uri)}')
        app.config['SQLALCHEMY_DATABASE_URI'] = config.sqlalchemy_uri
        app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = config.sqlalchemy_track_modify
        # S3: 数据库连接池参数
        app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
            'pool_size': config.db_pool_size,
            'pool_recycle': config.db_pool_recycle,
            'pool_pre_ping': config.db_pool_pre_ping,
            'pool_timeout': config.db_pool_timeout,
        }
        db = SQLAlchemy(app)
        if config.db_reflect_on_start:
            with app.app_context():
                db.reflect()
    else:
        db = None


# 初始化 Flask-Migrate（数据库迁移），仅 db 非None时启用
def init_Migrate(app):
    global migrate
    if db is not None and migrate is None:
        migrate = Migrate(app, db)
        Logger.info('init_Migrate : Flask-Migrate enabled')


# 获取SQLAlchemy对象
def sqlalchemy():
    global db
    return db


# 获取Migrate对象
def get_migrate():
    global migrate
    return migrate


# 事务装饰器，用于处理事务
def sqlalchemy_trans(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if db is None:
            raise RuntimeError('SQLAlchemy 未初始化，请配置 SQLALCHEMY_URI')
        try:
            ret = func(*args, **kwargs)
            db.session.commit()
            return ret
        except Exception as e:
            db.session.rollback()
            Logger.error(f'sqlalchemy_trans : {e}')
            raise
    return wrapper
