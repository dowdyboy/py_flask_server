"""SQLite 模块单元测试：limit=0 语义、NULL 值处理、真实连接 CRUD"""

import sqlite3

import pytest

from flask_server.module.sqlite import SQLite


def _capture(monkeypatch):
    """拦截 fetch，捕获生成的 SQL"""
    captured = {}

    def fake_fetch(sql, params=None):
        captured['sql'] = sql
        captured['params'] = params
        return []

    monkeypatch.setattr(SQLite, 'fetch', fake_fetch)
    return captured


def test_select_limit_zero(monkeypatch):
    """limit=0 应生成 LIMIT 0 子句（而非被省略）"""
    captured = _capture(monkeypatch)
    SQLite.select('tbl', columns=['a'], limit=0)
    assert 'LIMIT 0' in captured['sql']


def test_select_no_limit(monkeypatch):
    """未指定 limit 时不生成 LIMIT 子句"""
    captured = _capture(monkeypatch)
    SQLite.select('tbl')
    assert 'LIMIT' not in captured['sql']


def test_select_limit_value(monkeypatch):
    captured = _capture(monkeypatch)
    SQLite.select('tbl', limit=10)
    assert 'LIMIT 10' in captured['sql']


def test_no_parse_value_method():
    """_parse_value/_parse_values 已移除（未转义字面量拼接是注入隐患），
    值必须走 ? 占位符参数化"""
    assert not hasattr(SQLite, '_parse_value')
    assert not hasattr(SQLite, '_parse_values')


@pytest.fixture
def real_conn(monkeypatch, tmp_path):
    """真实 SQLite 连接（临时文件库），测试后自动恢复"""
    conn = sqlite3.connect(str(tmp_path / 'test.db'))
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(SQLite, 'conn', conn)
    conn.execute('CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)')
    conn.commit()
    yield conn
    conn.close()


def test_real_crud_roundtrip(real_conn):
    """insert → select → update → delete 全流程"""
    row_id = SQLite.insert('users', ['name'], ['alice'], ret_row_id=True)
    assert row_id is not None

    rows = SQLite.select('users', columns=['id', 'name'], conditions='id = ?', params=[row_id])
    assert len(rows) == 1
    assert rows[0]['name'] == 'alice'

    SQLite.update('users', ['name'], ['bob'], conditions='id = ?', condition_params=[row_id])
    rows = SQLite.select('users', conditions='id = ?', params=[row_id])
    assert rows[0]['name'] == 'bob'

    SQLite.delete('users', conditions='id = ?', params=[row_id])
    assert SQLite.select_all('users') == []


def test_real_select_limit(real_conn):
    for name in ['a', 'b', 'c']:
        SQLite.insert('users', ['name'], [name])
    rows = SQLite.select('users', limit=2)
    assert len(rows) == 2
    rows0 = SQLite.select('users', limit=0)
    assert len(rows0) == 0


def test_debug_sql_printing_path(monkeypatch):
    """DEBUG_SQL 开启时执行路径不抛异常（打印分支覆盖）"""
    from flask_server import config
    monkeypatch.setattr(config, 'debug', True)
    monkeypatch.setattr(config, 'debug_sql', True)
    captured = _capture(monkeypatch)
    SQLite.select('tbl', limit=10)
    assert 'LIMIT 10' in captured['sql']


def test_init_sqlite_db_executes_init_sql(monkeypatch):
    """init_sqlite_db 执行配置的初始化 SQL 列表"""
    from flask_server import config
    from flask_server.module.sqlite import init_sqlite_db

    executed = []

    class _FakeConn:
        def cursor(self):
            return self

        def execute(self, sql):
            executed.append(sql)

        def commit(self):
            pass

    monkeypatch.setattr(config, 'db_init_sql_list', ['CREATE TABLE t(x INT)'])
    monkeypatch.setattr(SQLite, 'conn', _FakeConn())
    init_sqlite_db()
    assert executed == ['CREATE TABLE t(x INT)']
