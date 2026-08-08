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
