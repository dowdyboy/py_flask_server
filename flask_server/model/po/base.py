
# 声明式 Model 基类示例（推荐，替代反射式 autoload）
# 启用数据库后（配置 SQLALCHEMY_URI），继承 db.Model 声明字段，配合 Flask-Migrate 生成建表迁移：
#   flask db init        # 首次初始化迁移目录
#   flask db migrate     # 生成迁移脚本
#   flask db upgrade     # 执行迁移
#
# 示例：
# db = sqlalchemy()
#
# class UserPO(db.Model):
#     __tablename__ = 'user'
#     uid = db.Column(db.String(64), primary_key=True)
#     username = db.Column(db.String(128), unique=True, nullable=False)
#     passwd = db.Column(db.String(128), nullable=False)
#     create_time = db.Column(db.DateTime, nullable=False)
