from functools import wraps
from flask_sqlalchemy import SQLAlchemy
from ..config import config
from ..util import Logger

# 使用SQLAlchemy作为数据库访问层，具体使用参考sqlalchemy库
# 本文件中的函数用于初始化SQLAlchemy对象，以及提供一个装饰器用于事务处理

Logger.info(f'SQLALCHEMY_DATABASE_URI : {config.sqlalchemy_uri}')

db: SQLAlchemy = None


# 初始化SQLAlchemy对象，并反射数据库表结构
def init_SQLAlchemy(app):
    global db
    if config.sqlalchemy_uri is not None and db is None:
        Logger.info(f'init_SQLAlchemy : {config.sqlalchemy_uri}')
        app.config['SQLALCHEMY_DATABASE_URI'] = config.sqlalchemy_uri
        app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = config.sqlalchemy_track_modify
        db = SQLAlchemy(app)
        with app.app_context():
            db.reflect()
    else:
        db = None


# 获取SQLAlchemy对象
def sqlalchemy():
    global db
    return db


# 事务装饰器，用于处理事务
def sqlalchemy_trans(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            ret = func(*args, **kwargs)
            db.session.commit()
            return ret
        except Exception as e:
            db.session.rollback()
            Logger.error(f'sqlalchemy_trans : {e}')
            raise Exception(f'{func} sqlalchemy_trans error, rollback')
        finally:
            db.session.close()
    return wrapper
