"""SQLite 模块单元测试：limit=0 语义、NULL 值处理、真实连接 CRUD"""

import logging
import sqlite3
import sys

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


def test_identifier_injection_rejected():
    """表名/列名白名单：拼接型标识符注入必须被拒绝（无法用 ? 参数化）"""
    for evil in ("users; DROP TABLE user--", "users`", "users WHERE 1=1",
                 "1table", "", "a b", "tbl'--"):
        with pytest.raises(ValueError):
            SQLite._parse_table_name(evil)
    with pytest.raises(ValueError):
        SQLite._parse_columns(['ok', "x`; DROP TABLE t--"])
    with pytest.raises(ValueError):
        SQLite.insert('tbl', ['bad col'], [1])


def test_identifier_valid_names_accepted():
    """常见标识符（含下划线/数字/$）应正常引用"""
    assert SQLite._parse_table_name('user_profile2') == '`user_profile2`'
    assert SQLite._parse_column('_id') == '`_id`'
    assert SQLite._parse_table_name('t$1') == '`t$1`'


def test_select_limit_non_integer_rejected():
    """limit 必须是整数：防止把用户输入直接拼进 SQL"""
    for bad in ('10; DROP TABLE t', 1.5, True, -1, [10]):
        with pytest.raises(ValueError):
            SQLite.select('tbl', limit=bad)


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


def test_debug_sql_printing_path(monkeypatch, capsys):
    """DEBUG_SQL 开启时真实执行路径应把 SQL 打印到日志输出（不是绕开 fetch 的空覆盖）"""
    from flask_server import config

    monkeypatch.setattr(config, 'debug', True)
    monkeypatch.setattr(config, 'debug_sql', True)

    executed = []

    class _FakeCur:
        def execute(self, sql, params=None):
            executed.append((sql, params))

        def fetchall(self):
            return []

    class _FakeConn:
        def cursor(self):
            return _FakeCur()

    monkeypatch.setattr(SQLite, 'conn', _FakeConn())

    # 临时把日志复制一份到 capsys 能捕获的 stdout（flask_server logger 不向 root 传播）
    logger = logging.getLogger('flask_server')
    monkeypatch.setattr(logger, 'level', logging.INFO)
    stream = logging.StreamHandler(sys.stdout)
    logger.addHandler(stream)
    try:
        rows = SQLite.select('tbl', limit=10)
    finally:
        logger.removeHandler(stream)

    captured = capsys.readouterr().out
    assert rows == []
    executed_sql = executed[0][0]
    assert 'SELECT * FROM `tbl`' in executed_sql
    assert 'LIMIT 10' in executed_sql
    assert 'SELECT * FROM `tbl`' in captured, f'SQL 未打印: {captured!r}'
    assert 'LIMIT 10' in captured


def test_init_sqlite_db_executes_init_sql(monkeypatch):
    """init_sqlite_db 用 executescript 执行配置的初始化 SQL（整体执行，保留存储过程内分号）"""
    from flask_server import config
    from flask_server.module.sqlite import init_sqlite_db

    executed = []

    class _FakeCur:
        def executescript(self, sql):
            executed.append(sql)

    class _FakeConn:
        def cursor(self):
            return _FakeCur()

        def commit(self):
            pass

    monkeypatch.setattr(config, 'db_init_sql', 'CREATE TABLE t(x INT);\n-- comment with ;\nCREATE TABLE u(y INT);')
    monkeypatch.setattr(SQLite, 'conn', _FakeConn())
    init_sqlite_db()
    assert executed == ['CREATE TABLE t(x INT);\n-- comment with ;\nCREATE TABLE u(y INT);']


def test_init_sqlite_db_skipped_when_no_init_sql(monkeypatch):
    """未配置初始化 SQL 时 init_sqlite_db 为空操作"""
    from flask_server import config
    from flask_server.module.sqlite import init_sqlite_db

    executed = []

    class _FakeCur:
        def executescript(self, sql):
            executed.append(sql)

    class _FakeConn:
        def cursor(self):
            return _FakeCur()

        def commit(self):
            pass

    monkeypatch.setattr(config, 'db_init_sql', None)
    monkeypatch.setattr(SQLite, 'conn', _FakeConn())
    init_sqlite_db()
    assert executed == []


def test_execute_retries_on_database_locked(monkeypatch):
    """并发写锁冲突（database is locked）应重试后成功，而非直接抛异常"""
    import sqlite3
    from flask_server.module.sqlite import SQLite as S

    calls = {'n': 0}

    class _FakeCur:
        def execute(self, sql, params=None):
            calls['n'] += 1
            if calls['n'] <= 2:
                raise sqlite3.OperationalError('database is locked')

        lastrowid = 7

    class _FakeConn:
        def cursor(self):
            return _FakeCur()

        def commit(self):
            pass

        def rollback(self):
            pass

    conn = _FakeConn()
    monkeypatch.setattr(S, 'conn', conn)
    monkeypatch.setattr(S, '_LOCKED_RETRIES', 3)
    monkeypatch.setattr(S, '_LOCKED_RETRY_INTERVAL', 0)
    row_id = S.execute('INSERT INTO t VALUES (?)', [1], ret_row_id=True)
    assert row_id == 7
    assert calls['n'] == 3   # 2 次锁冲突 + 1 次成功


def test_execute_raises_after_retries_exhausted(monkeypatch):
    """锁冲突重试耗尽后仍应抛异常"""
    import sqlite3
    from flask_server.module.sqlite import SQLite as S

    class _FakeCur:
        def execute(self, sql, params=None):
            raise sqlite3.OperationalError('database is locked')

    class _FakeConn:
        def cursor(self):
            return _FakeCur()

        def commit(self):
            pass

        def rollback(self):
            pass

    monkeypatch.setattr(S, 'conn', _FakeConn())
    monkeypatch.setattr(S, '_LOCKED_RETRIES', 2)
    monkeypatch.setattr(S, '_LOCKED_RETRY_INTERVAL', 0)
    import pytest
    with pytest.raises(sqlite3.OperationalError):
        S.execute('INSERT INTO t VALUES (?)', [1])


# ------------------------- 0.8：失败路径 / 工作线程连接 -------------------------


class _RecordingCursor:
    def __init__(self, conn):
        self.conn = conn
        self.lastrowid = 7

    def execute(self, sql, params=None):
        self.conn.executed.append((sql, params))
        if self.conn.errors:
            raise self.conn.errors.pop(0)

    def fetchall(self):
        return [('row',)]


class _RecordingConn:
    """记录 execute/commit/rollback 的假连接（按顺序注入异常）"""

    def __init__(self, errors=None):
        self.errors = list(errors or [])
        self.executed = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return _RecordingCursor(self)

    def execute(self, sql, params=None):
        """sqlite3.Connection 也支持 execute（_get_conn 用它设置 PRAGMA）"""
        self.executed.append((sql, params))

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_execute_non_locked_error_rolls_back(monkeypatch):
    """非 locked 的 sqlite3.Error（唯一约束等）也必须先 rollback，避免持锁阻塞整库"""
    import sqlite3

    conn = _RecordingConn([sqlite3.IntegrityError('UNIQUE constraint failed')])
    monkeypatch.setattr(SQLite, 'conn', conn)
    with pytest.raises(sqlite3.IntegrityError):
        SQLite.execute('INSERT INTO t VALUES (?)', [1])
    assert conn.rollbacks == 1
    assert conn.commits == 0


def test_fetch_retries_on_locked_and_returns_rows(monkeypatch):
    import sqlite3

    conn = _RecordingConn([sqlite3.OperationalError('database is locked')])
    monkeypatch.setattr(SQLite, 'conn', conn)
    monkeypatch.setattr(SQLite, '_LOCKED_RETRY_INTERVAL', 0)
    assert SQLite.fetch('SELECT 1') == [('row',)]
    assert conn.rollbacks == 1
    assert len(conn.executed) == 2


def test_fetch_non_locked_error_rolls_back_and_raises(monkeypatch):
    import sqlite3

    conn = _RecordingConn([sqlite3.OperationalError('no such table: t')])
    monkeypatch.setattr(SQLite, 'conn', conn)
    with pytest.raises(sqlite3.OperationalError):
        SQLite.fetch('SELECT * FROM t')
    assert conn.rollbacks == 1


def test_sqlite_disabled_raises_clear_error(monkeypatch):
    monkeypatch.setattr(SQLite, 'conn', None)
    with pytest.raises(RuntimeError, match='SQLite 未启用'):
        SQLite.execute('SELECT 1')
    with pytest.raises(RuntimeError, match='SQLite 未启用'):
        SQLite.fetch('SELECT 1')


def test_debug_sql_logs_statement(monkeypatch):
    """DEBUG_SQL=true 时打印 SQL（开发排障用）"""
    import logging

    from flask_server import config

    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = Capture()
    logging.getLogger('flask_server').addHandler(handler)
    monkeypatch.setattr(config, 'debug', True)
    monkeypatch.setattr(config, 'debug_sql', True)
    monkeypatch.setattr(SQLite, 'conn', _RecordingConn())
    try:
        SQLite.execute('INSERT INTO t VALUES (?)', [1])
        SQLite.fetch('SELECT * FROM t')
    finally:
        logging.getLogger('flask_server').removeHandler(handler)
    assert any('INSERT INTO t' in msg for msg in records)
    assert any('SELECT * FROM t' in msg for msg in records)


def test_worker_thread_gets_own_connection_and_pragma(monkeypatch):
    """非主线程使用独立连接（sqlite3 连接非线程安全），并设置 busy_timeout"""
    import threading

    main_conn = _RecordingConn()
    worker_conn = _RecordingConn()
    monkeypatch.setattr(SQLite, 'conn', main_conn)
    monkeypatch.setattr(SQLite, '_local', threading.local())
    monkeypatch.setattr('flask_server.module.sqlite.config.db_file_path', 'worker.db')
    monkeypatch.setattr('flask_server.module.sqlite.sqlite3.connect',
                        lambda *a, **kw: worker_conn)

    result = {}

    def run():
        result['conn'] = SQLite._get_conn()

    t = threading.Thread(target=run)
    t.start()
    t.join(5)
    assert result['conn'] is worker_conn
    assert any('PRAGMA busy_timeout' in sql for sql, _ in worker_conn.executed)


def test_worker_connect_pragma_failure_is_ignored(monkeypatch):
    """PRAGMA 失败（老版本 SQLite/只读文件等）不应阻断连接创建"""
    import threading
    import sqlite3

    class _BrokenPragmaConn(_RecordingConn):
        def execute(self, sql, params=None):
            raise sqlite3.Error('pragma failed')

    monkeypatch.setattr(SQLite, 'conn', _RecordingConn())
    monkeypatch.setattr(SQLite, '_local', threading.local())
    monkeypatch.setattr('flask_server.module.sqlite.config.db_file_path', 'worker2.db')
    broken = _BrokenPragmaConn()
    monkeypatch.setattr('flask_server.module.sqlite.sqlite3.connect', lambda *a, **kw: broken)

    result = {}

    def run():
        result['conn'] = SQLite._get_conn()

    t = threading.Thread(target=run)
    t.start()
    t.join(5)
    assert result['conn'] is broken


def test_get_conn_without_configured_path_reuses_class_conn(monkeypatch):
    """未配置 SQLITE_DB_PATH 但外部注入了 conn 时，工作线程复用该连接（测试注入兼容）"""
    import threading

    injected = _RecordingConn()
    monkeypatch.setattr(SQLite, 'conn', injected)
    monkeypatch.setattr('flask_server.module.sqlite.config.db_file_path', None)

    result = {}

    def run():
        result['conn'] = SQLite._get_conn()

    t = threading.Thread(target=run)
    t.start()
    t.join(5)
    assert result['conn'] is injected


def test_init_sqlite_db_error_rolls_back_and_continues(monkeypatch):
    """初始化 SQL 出错：记录 ERROR、回滚、不向上抛（否则 import/启动即崩）"""
    import sqlite3

    from flask_server.module.sqlite import init_sqlite_db

    class _BadScriptCursor:
        lastrowid = 0

        def executescript(self, sql):
            raise sqlite3.Error('bad script')

    class _Conn:
        def __init__(self):
            self.rollbacks = 0

        def cursor(self):
            return _BadScriptCursor()

        def commit(self):
            pass

        def rollback(self):
            self.rollbacks += 1

    conn = _Conn()
    monkeypatch.setattr(SQLite, 'conn', conn)
    monkeypatch.setattr('flask_server.module.sqlite.config.db_init_sql', 'INVALID;')
    init_sqlite_db()
    assert conn.rollbacks == 1
