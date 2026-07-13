from flask_server import app
from flask_server.module.sqlalchemy import sqlalchemy

# ⚠️ 反射式 Model（不推荐）
# 此写法在 import 时即执行 db.Table(..., autoload_with=db.engine) 反射表结构，
# 未配数据库或表不存在会启动报错；高并发或需单测隔离的场景也不适用。
# 推荐使用声明式 Model，参考 examples/model/user_declared.py，配合 Flask-Migrate 管理建表。

db = sqlalchemy()

class ArticlePO(db.Model):

    with app.app_context():
        __table__ = db.Table('article', db.metadata, autoload_with=db.engine)

