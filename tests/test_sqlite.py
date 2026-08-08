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


def test_parse_value_none():
    assert SQLite._parse_value(None) == 'NULL'


def test_parse_value_types():
    assert SQLite._parse_value('abc') == "'abc'"
    assert SQLite._parse_value(1) == '1'
    assert SQLite._parse_value(1.5) == '1.5'


def test_parse_value_unsupported():
    import pytest
    with pytest.raises(Exception):
        SQLite._parse_value(b'bytes')


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
