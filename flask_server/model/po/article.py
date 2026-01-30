from flask_server import app
from flask_server.module.sqlalchemy import sqlalchemy

# 定义数据库表映射类，具体使用参考sqlalchemy库

db = sqlalchemy()

class ArticlePO(db.Model):

    with app.app_context():
        __table__ = db.Table('hupi_article', db.metadata, autoload_with=db.engine)

