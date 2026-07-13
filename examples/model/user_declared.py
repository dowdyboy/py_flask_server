from flask_server import app
from flask_server.module.sqlalchemy import sqlalchemy

# 声明式 Model 正例（推荐写法，对比反射式 article.py/user.py）
# 通过继承 db.Model 声明字段，配合 Flask-Migrate 自动生成建表迁移
# 接入工程前需配置 SQLALCHEMY_URI 并在 model/po/__init__.py 中导出

db = sqlalchemy()


class UserPO(db.Model):
    __tablename__ = 'user'

    uid = db.Column(db.String(64), primary_key=True)
    username = db.Column(db.String(128), unique=True, nullable=False)
    passwd = db.Column(db.String(128), nullable=False)
    last_login_time = db.Column(db.DateTime, nullable=True)
    create_time = db.Column(db.DateTime, nullable=False)
