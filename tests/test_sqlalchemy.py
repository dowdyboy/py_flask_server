"""SQLAlchemy 初始化容错测试：db.reflect 失败不应导致启动崩溃"""

from importlib import import_module

from flask import Flask

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

    from sqlalchemy import MetaData

    def boom(self, bind=None, **kwargs):
        raise RuntimeError('mock reflect failure')

    monkeypatch.setattr(MetaData, 'reflect', boom)

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


def test_sqlalchemy_trans_raises_when_app_none(monkeypatch):
    """db 已注入但 _app 未初始化时 sqlalchemy_trans 给出明确报错（而非 AttributeError）"""
    import pytest
    from flask_sqlalchemy import SQLAlchemy
    monkeypatch.setattr(sa_module, 'db', SQLAlchemy())
    monkeypatch.setattr(sa_module, '_app', None)
    @sa_module.sqlalchemy_trans
    def op():
        pass

    with pytest.raises(RuntimeError, match='未绑定 Flask app'):
        op()


def test_in_app_context_raises_when_app_none(monkeypatch):
    """_app 未初始化时 in_app_context 给出明确报错（而非 AttributeError）"""
    import pytest
    monkeypatch.setattr(sa_module, '_app', None)

    with pytest.raises(RuntimeError, match='未绑定 Flask app'):
        sa_module.in_app_context(lambda: 'ok')


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


def test_reflect_with_existing_user_table_imports_ok(tmp_path):
    """subprocess 回归：DB 已含 user 表（迁移建表后重启）+ 默认 DB_REFLECT_ON_START=true →
    导入 flask_server 不再抛 InvalidRequestError（修复前"建表后第二次启动必崩"）；
    声明式 user 表与反射的旧表共存于 metadata。
    """
    import os
    import sqlite3
    import subprocess
    import sys

    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    db_file = tmp_path / 'restart.db'
    conn = sqlite3.connect(str(db_file))
    conn.execute('CREATE TABLE user (uid VARCHAR(64) PRIMARY KEY, username VARCHAR(128), '
                 'passwd VARCHAR(256), create_time DATETIME)')
    conn.execute('CREATE TABLE legacy_orders (id INTEGER PRIMARY KEY, note TEXT)')
    conn.commit()
    conn.close()

    uri = f'sqlite:///{db_file.as_posix()}'
    code = (
        "import os, sys\n"
        f"os.environ['SQLALCHEMY_URI'] = {uri!r}\n"
        "os.environ['DB_REFLECT_ON_START'] = 'true'\n"
        "os.environ['REDIS_URL'] = ''\n"
        "os.environ['AUTH_ENABLED'] = 'false'\n"
        "os.environ['AUTH_STORE'] = 'memory'\n"
        "os.environ['LOG_FILE_PATH'] = ''\n"
        f"sys.path.insert(0, {PROJECT_ROOT!r})\n"
        "import flask_server\n"
        "from flask_server.module import sqlalchemy\n"
        "tables = set(sqlalchemy().metadata.tables.keys())\n"
        "assert 'user' in tables, tables\n"
        "assert 'legacy_orders' in tables, tables\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, '-c', code], capture_output=True, text=True,
        cwd=PROJECT_ROOT, timeout=120,
    )
    assert result.returncode == 0, f'import crashed, stderr:\n{result.stderr}'
    assert 'OK' in result.stdout
