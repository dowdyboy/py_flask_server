from flask_server.module.sqlalchemy import sqlalchemy

# 声明式 Model 正例（文章 + 购买记录）
# 原 examples/model/article.py 为反射式写法（import 即连库），本文件为推荐写法，
# 配合 Flask-Migrate 自动生成建表迁移。
# 接入工程前需配置 SQLALCHEMY_URI 并在 model/po/__init__.py 中导出。

db = sqlalchemy()

if db is not None:
    class ArticlePO(db.Model):
        __tablename__ = 'article'

        aid = db.Column(db.String(128), primary_key=True)
        title = db.Column(db.String(128), nullable=False)
        content = db.Column(db.Text, nullable=False)
        secret_content = db.Column(db.Text, nullable=False)
        money = db.Column(db.Integer, nullable=False)
        state = db.Column(db.Integer, nullable=False, default=1)
        access_count = db.Column(db.Integer, nullable=False, default=0)
        buy_count = db.Column(db.Integer, nullable=False, default=0)
        update_time = db.Column(db.DateTime, nullable=False)
        create_time = db.Column(db.DateTime, nullable=False)

    class BuyRecordPO(db.Model):
        __tablename__ = 'buy_record'

        id = db.Column(db.Integer, primary_key=True, autoincrement=True)
        user_key = db.Column(db.String(64), nullable=False, index=True)
        aid = db.Column(db.String(128), nullable=False)
        create_time = db.Column(db.DateTime, nullable=False)
else:
    class ArticlePO:
        """未配置数据库时的占位模型；配置 SQLALCHEMY_URI 后自动切换为真实 ORM 模型"""

    class BuyRecordPO:
        """未配置数据库时的占位模型；配置 SQLALCHEMY_URI 后自动切换为真实 ORM 模型"""
