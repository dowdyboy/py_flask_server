"""SQLAlchemy 事务与 CRUD 集成测试（使用 SQLite 临时库 + 测试内声明式 Model）"""

import pytest
from flask import Flask
from sqlalchemy import text
from importlib import import_module

from flask_server import config

# 注意：flask_server.module 包的 sqlalchemy 属性是函数（遮蔽了子模块），
# 必须用 importlib 获取真实子模块
sa_module = import_module('flask_server.module.sqlalchemy')


@pytest.fixture
def db_env(monkeypatch, tmp_path):
    """初始化 SQLAlchemy（sqlite 临时库）+ 测试模型，测试后清理"""
    db_file = tmp_path / 'it.db'
    monkeypatch.setattr(config, 'sqlalchemy_uri', f'sqlite:///{db_file.as_posix()}')
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
