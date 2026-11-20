from flask_server.module.sqlalchemy import sqlalchemy

# 用户持久化对象（认证模块 sqlalchemy 存储模式使用）
# 启用方式：配置 SQLALCHEMY_URI + AUTH_STORE=sqlalchemy，然后：
#   flask db migrate -m "create user table" && flask db upgrade
# 未配置数据库时导入本文件不会报错（占位实现），配置后自动切换为真实 ORM 模型

db = sqlalchemy()

# 下面两个分支是"二选一"的运行期切换，静态类型检查无法表达（db 在未配置时是 None，
# 因此 `db.Model` 对检查器不可见；同名类又在两个分支各定义一次）。用 type: ignore 显式豁免，
# 而不是让检查器把这里当成错误——这一模式在本模板里是刻意的（同 flask_server/module/sqlalchemy.py
# 注释所述：配置 SQLALCHEMY_URI 前后 UserPO 的数据源不同）。
if db is not None:
    class UserPO(db.Model):   # type: ignore[name-defined]
        __tablename__ = 'user'

        uid = db.Column(db.String(64), primary_key=True)
        username = db.Column(db.String(128), unique=True, nullable=False, index=True)
        passwd = db.Column(db.String(256), nullable=False)
        create_time = db.Column(db.DateTime, nullable=False)
else:
    class UserPO:   # type: ignore[no-redef]
        """未配置数据库时的占位模型；配置 SQLALCHEMY_URI 后自动切换为真实 ORM 模型"""
