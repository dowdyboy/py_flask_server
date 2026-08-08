"""SQLAlchemy 事务与 CRUD 集成测试（默认 SQLite 临时库；设置 TEST_DB_URI 环境变量可跑真实 MySQL）"""

import os

import pytest
from flask import Flask
from importlib import import_module

from flask_server import config

# 注意：flask_server.module 包的 sqlalchemy 属性是函数（遮蔽了子模块），
# 必须用 importlib 获取真实子模块
sa_module = import_module('flask_server.module.sqlalchemy')

# CI 中设置 TEST_DB_URI 时跑真实 MySQL（如 mysql+pymysql://root:root@127.0.0.1:3306/testdb?charset=utf8mb4）
_TEST_DB_URI = os.environ.get('TEST_DB_URI')


def _ensure_mysql_database(uri):
    """SQLAlchemy 不自动建库：经 pymysql 直连（不指定库）创建目标数据库"""
    import pymysql
    from sqlalchemy.engine import make_url

    url = make_url(uri)
    conn = pymysql.connect(
        host=url.host, port=url.port or 3306,
        user=url.username, password=url.password or '',
        charset='utf8mb4',
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                f'CREATE DATABASE IF NOT EXISTS `{url.database}` '
                f'CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def db_env(monkeypatch, tmp_path):
    """初始化 SQLAlchemy + 测试模型，测试后清理（默认 sqlite；TEST_DB_URI 时用 MySQL）"""
    if _TEST_DB_URI:
        _ensure_mysql_database(_TEST_DB_URI)
        uri = _TEST_DB_URI
    else:
        db_file = tmp_path / 'it.db'
        uri = f'sqlite:///{db_file.as_posix()}'
    monkeypatch.setattr(config, 'sqlalchemy_uri', uri)
    monkeypatch.setattr(config, 'db_reflect_on_start', False)
    monkeypatch.setattr(sa_module, 'db', None)

    app = Flask(__name__)
    sa_module.init_SQLAlchemy(app)
    db = sa_module.sqlalchemy()

    class ItemPO(db.Model):
        __tablename__ = 'item'
        id = db.Column(db.Integer, primary_key=True, autoincrement=True)
        name = db.Column(db.String(64), nullable=False)

    with app.app_context():
        db.create_all()
    yield db, ItemPO, app
    with app.app_context():
        db.session.remove()
        db.engine.dispose()
    monkeypatch.setattr(sa_module, 'db', None)


def test_trans_commit_persists(db_env):
    """sqlalchemy_trans：函数正常返回则提交（装饰器自动管理 app context）"""
    db, ItemPO, app = db_env

    @sa_module.sqlalchemy_trans
    def add_item(name):
        item = ItemPO(name=name)
        db.session.add(item)

    add_item('apple')
    with app.app_context():
        assert ItemPO.query.filter_by(name='apple').first() is not None


def test_trans_rollback_on_error(db_env):
    """sqlalchemy_trans：函数抛异常则回滚，数据不落库"""
    db, ItemPO, app = db_env

    @sa_module.sqlalchemy_trans
    def add_item_bad(name):
        item = ItemPO(name=name)
        db.session.add(item)
        raise RuntimeError('mock business failure')

    with pytest.raises(RuntimeError):
        add_item_bad('banana')
    with app.app_context():
        assert ItemPO.query.filter_by(name='banana').first() is None


def test_crud_roundtrip(db_env):
    """完整 CRUD 流程"""
    db, ItemPO, app = db_env
    with app.app_context():
        item = ItemPO(name='cherry')
        db.session.add(item)
        db.session.commit()
        fetched = ItemPO.query.filter_by(name='cherry').first()
        assert fetched is not None
        fetched.name = 'cherry2'
        db.session.commit()
        assert ItemPO.query.filter_by(name='cherry2').first() is not None
        db.session.delete(fetched)
        db.session.commit()
        assert ItemPO.query.filter_by(name='cherry2').first() is None
