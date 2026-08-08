from flask_server.module.sqlalchemy import sqlalchemy

# 用户持久化对象（认证模块 sqlalchemy 存储模式使用）
# 启用方式：配置 SQLALCHEMY_URI + AUTH_STORE=sqlalchemy，然后：
#   flask db migrate -m "create user table" && flask db upgrade
# 未配置数据库时导入本文件不会报错（占位实现），配置后自动切换为真实 ORM 模型

db = sqlalchemy()

if db is not None:
    class UserPO(db.Model):
        __tablename__ = 'user'

        uid = db.Column(db.String(64), primary_key=True)
        username = db.Column(db.String(128), unique=True, nullable=False, index=True)
        passwd = db.Column(db.String(256), nullable=False)
        create_time = db.Column(db.DateTime, nullable=False)
else:
    class UserPO:
        """未配置数据库时的占位模型；配置 SQLALCHEMY_URI 后自动切换为真实 ORM 模型"""
