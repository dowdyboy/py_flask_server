"""SQLAlchemy 初始化容错测试：db.reflect 失败不应导致启动崩溃"""

from importlib import import_module

from flask import Flask
from flask_sqlalchemy import SQLAlchemy

from flask_server import config

# 注意：flask_server.module 包的 sqlalchemy 属性是函数（遮蔽了子模块），
# 必须用 importlib 获取真实子模块
sa_module = import_module('flask_server.module.sqlalchemy')


def test_init_sqlalchemy_reflect_failure_tolerated(monkeypatch, tmp_path):
    """反射失败仅告警，不向上抛异常，应用可正常继续初始化"""
    # 注：不能用 sqlite:///:memory:（配置的 pool_size 与内存库不兼容）
    db_file = tmp_path / 'test.db'
    monkeypatch.setattr(config, 'sqlalchemy_uri', f'sqlite:///{db_file.as_posix()}')
    monkeypatch.setattr(config, 'db_reflect_on_start', True)
    monkeypatch.setattr(sa_module, 'db', None)

    def boom(self):
        raise RuntimeError('mock reflect failure')

    monkeypatch.setattr(SQLAlchemy, 'reflect', boom)

    app = Flask(__name__)
    sa_module.init_SQLAlchemy(app)
    assert sa_module.sqlalchemy() is not None


def test_init_sqlalchemy_no_uri_keeps_none(monkeypatch):
    """未配置 SQLALCHEMY_URI 时 db 保持 None，不初始化"""
    monkeypatch.setattr(config, 'sqlalchemy_uri', None)
    monkeypatch.setattr(sa_module, 'db', None)
    app = Flask(__name__)
    sa_module.init_SQLAlchemy(app)
    assert sa_module.sqlalchemy() is None


def test_sqlalchemy_trans_raises_when_db_none(monkeypatch):
    """db 未初始化时 sqlalchemy_trans 给出明确报错"""
    import pytest
    monkeypatch.setattr(sa_module, 'db', None)

    @sa_module.sqlalchemy_trans
    def op():
        pass

    with pytest.raises(RuntimeError, match='未初始化'):
        op()


def test_get_migrate_returns_none_when_disabled(monkeypatch):
    """未启用 Flask-Migrate 时 get_migrate 返回 None"""
    monkeypatch.setattr(sa_module, 'migrate', None)
    assert sa_module.get_migrate() is None


def test_in_app_context_creates_context_when_missing(monkeypatch, tmp_path):
    """无 app context 时 in_app_context 自动创建（_app.app_context 包裹）"""
    db_file = tmp_path / 'ctx.db'
    monkeypatch.setattr(config, 'sqlalchemy_uri', f'sqlite:///{db_file.as_posix()}')
    monkeypatch.setattr(sa_module, 'db', None)

    app = Flask(__name__)
    sa_module.init_SQLAlchemy(app)
    assert sa_module.in_app_context(lambda: 'ok') == 'ok'
