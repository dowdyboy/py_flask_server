"""真实环境自检脚本（scripts/verify_real_env.py）的安全护栏测试。

这些护栏对应第一轮 P0-5：脚本会创建/删除数据库与 migrations/，一旦参数误传
就可能破坏真实项目。用例只验证"拒绝逻辑"，不连接任何真实 MySQL/Redis。
"""

import importlib.util
import os
import sys

import pytest


def _load_module():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, 'scripts', 'verify_real_env.py')
    spec = importlib.util.spec_from_file_location('verify_real_env_under_test', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def vre():
    return _load_module()


def _run_main(monkeypatch, vre, argv):
    monkeypatch.setattr(sys, 'argv', ['verify_real_env.py'] + argv)
    with pytest.raises(SystemExit) as exc:
        vre.main()
    return exc.value.code


def test_missing_uri_exits_1(monkeypatch, vre, capsys):
    code = _run_main(monkeypatch, vre, [])
    assert code == 1
    assert '缺少 SQLALCHEMY_URI / REDIS_URL' in capsys.readouterr().out


def test_test_db_equal_app_db_is_rejected(monkeypatch, vre, capsys):
    """--test-db 传成应用库：会往真实库建表，必须直接拒绝"""
    code = _run_main(monkeypatch, vre, [
        '--mysql-uri', 'mysql+pymysql://u:p@127.0.0.1:1/appdb',
        '--redis-url', 'redis://127.0.0.1:1/0',
        '--test-db', 'appdb',
    ])
    assert code == 1
    assert '--test-db 不能等于应用库' in capsys.readouterr().out


def test_reflect_db_equal_app_db_is_rejected(monkeypatch, vre, capsys):
    """--reflect-db 传成应用库：脚本会 DROP DATABASE，必须直接拒绝"""
    code = _run_main(monkeypatch, vre, [
        '--mysql-uri', 'mysql+pymysql://u:p@127.0.0.1:1/appdb',
        '--redis-url', 'redis://127.0.0.1:1/0',
        '--reflect-db', 'appdb',
    ])
    assert code == 1
    assert '--reflect-db 不能等于应用库' in capsys.readouterr().out


def test_test_db_equal_reflect_db_is_rejected(monkeypatch, vre, capsys):
    code = _run_main(monkeypatch, vre, [
        '--mysql-uri', 'mysql+pymysql://u:p@127.0.0.1:1/appdb',
        '--redis-url', 'redis://127.0.0.1:1/0',
        '--test-db', 'same_db',
        '--reflect-db', 'same_db',
    ])
    assert code == 1
    assert '--test-db 与 --reflect-db 不能相同' in capsys.readouterr().out


def test_migrate_refuses_existing_history(monkeypatch, vre, tmp_path):
    """已有 migrations/ 时默认拒绝覆盖（第一轮 P0-5 回归），且不删除目录"""
    from types import SimpleNamespace

    (tmp_path / 'migrations').mkdir()
    monkeypatch.setattr(vre, 'PROJECT_ROOT', str(tmp_path))
    monkeypatch.setattr(vre, '_table_exists', lambda url, table: False)
    args = SimpleNamespace(
        mysql_uri='mysql+pymysql://u:p@127.0.0.1:1/appdb',
        force_migrations=False,
        keep_migrations=False,
    )

    with pytest.raises(RuntimeError, match='拒绝覆盖'):
        vre.step_migrate(args)
    assert (tmp_path / 'migrations').is_dir(), '拒绝路径不得删除已有迁移历史'
