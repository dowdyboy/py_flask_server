import sqlite3
import threading
import os
import time
from ..config import config
from ..util import Logger

# 使用SQLite作为数据库访问层，具体使用参考sqlite3库


class SQLite:

    # 每线程独立连接（threading.local）：sqlite3 连接非线程安全，
    # 单连接 + 全局锁虽正确但并发上限低（长查询会阻塞全部操作），
    # per-thread 连接各线程互不阻塞，写操作由 SQLite 文件锁串行化。
    _local = threading.local()

    # 并发写冲突重试：per-thread 连接下多个线程同时写可能触发
    # OperationalError: database is locked，此处做有限次重试（退避等待），
    # 配合连接级 busy_timeout，显著降低写冲突导致 500 的概率。
    _LOCKED_RETRIES = 3
    _LOCKED_RETRY_INTERVAL = 0.1   # 秒
    # sqlite3.connect 的 timeout：默认 5s，这里放宽到 30s（配合 busy_timeout 生效）
    _CONNECT_TIMEOUT = 30

    # 判断是否使用sqlite3数据库，如果使用，则初始化连接，否则为None
    if config.db_file_path is not None:
        Logger.info(f"Initializing SQLite : {config.db_file_path}")
        # 自动创建数据库文件所在目录，避免目录不存在时 import 即崩溃
        _db_dir = os.path.dirname(config.db_file_path)
        if _db_dir:
            os.makedirs(_db_dir, exist_ok=True)
        conn = sqlite3.connect(config.db_file_path, timeout=_CONNECT_TIMEOUT, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # 提升并发写容错：WAL 模式允许读不阻塞写、写不阻塞读（单写者）
        try:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA busy_timeout=30000')
        except sqlite3.Error as e:
            Logger.warn(f'SQLite PRAGMA failed: {e}')
    else:
        conn = None

    @classmethod
    def _get_conn(cls):
        """获取当前线程的连接。

        主线程复用模块级连接（保持测试注入 SQLite.conn 的兼容性）；
        工作线程惰性创建专属连接（sqlite3 连接非线程安全）。
        未配置 SQLITE_DB_PATH 时（conn 被外部注入的场景）工作线程复用模块级连接。
        """
        if cls.conn is None:
            return None
        if threading.current_thread() is threading.main_thread() or not config.db_file_path:
            return cls.conn
        c = getattr(cls._local, 'conn', None)
        if c is None:
            c = sqlite3.connect(config.db_file_path, timeout=cls._CONNECT_TIMEOUT, check_same_thread=False)
            c.row_factory = sqlite3.Row
            try:
                c.execute('PRAGMA busy_timeout=30000')
            except sqlite3.Error:
                pass
            cls._local.conn = c
        return c

    # 将值转换为字符串，用于sql语句中
    # 注意：仅支持 ? 占位符参数化查询，禁止将值直接拼接进 SQL（防注入）
    # （_parse_value 曾被用于拼接字面量且未转义，已移除）

    # 转换表名为sqlite3语法，用于sql语句中
    @staticmethod
    def _parse_table_name(table_name):
        return f"`{table_name}`"

    # 转换列名为sqlite3语法，用于sql语句中
    @staticmethod
    def _parse_column(column):
        return f"`{column}`"

    # 转换列名列表为sqlite3语法，用于sql语句中
    @staticmethod
    def _parse_columns(columns):
        if columns is None:
            return '*'
        return ','.join([f"`{c}`" for c in columns])

    # 执行sql语句，返回row_id，多用于insert语句
    # 用法示例：
    # SQLite.execute("INSERT INTO table (column1, column2) VALUES (?, ?)", [1, 2])
    @staticmethod
    def execute(sql, params=None, ret_row_id=False):
        conn = SQLite._get_conn()
        if conn is None:
            raise RuntimeError('SQLite 未启用，请配置 SQLITE_DB_PATH')
        if config.debug and config.debug_sql:
            Logger.info(sql)
        last_error = None
        for attempt in range(SQLite._LOCKED_RETRIES + 1):
            try:
                cur = conn.cursor()
                cur.execute(sql, params or [])
                if ret_row_id:
                    row_id = cur.lastrowid
                else:
                    row_id = None
                conn.commit()
                return row_id
            except sqlite3.OperationalError as e:
                if 'locked' not in str(e).lower() or attempt >= SQLite._LOCKED_RETRIES:
                    raise
                # 回滚未提交事务再重试，避免 commit 锁冲突后重试造成重复写入
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                last_error = e
                time.sleep(SQLite._LOCKED_RETRY_INTERVAL * (attempt + 1))
        raise last_error

    # 执行sql语句，返回结果，多用于select语句，用法示例：
    # SQLite.fetch("SELECT * FROM table WHERE id = ?", [1])
    @staticmethod
    def fetch(sql, params=None, ):
        conn = SQLite._get_conn()
        if conn is None:
            raise RuntimeError('SQLite 未启用，请配置 SQLITE_DB_PATH')
        if config.debug and config.debug_sql:
            Logger.info(sql)
        cur = conn.cursor()
        cur.execute(sql, params or [])
        return cur.fetchall()

    # 插入数据，用法示例：
    # SQLite.insert("table", ["column1", "column2"], [1, 2])
    @staticmethod
    def insert(table, columns, values, ret_row_id=True):
        sql = (f"INSERT INTO {SQLite._parse_table_name(table)} "
               f"({SQLite._parse_columns(columns)}) "
               f"VALUES ({','.join(['?' for _ in values])})")
        return SQLite.execute(sql, params=values, ret_row_id=ret_row_id)

    # 查询数据，用法示例：
    # SQLite.select("table", ["column1", "column2"], "column1 = ?", params=[1])
    @staticmethod
    def select(table, columns=None, conditions=None, params=None, order_by=None, limit=None, ):
        sql = (f"SELECT {SQLite._parse_columns(columns)} "
               f"FROM {SQLite._parse_table_name(table)} "
               f"{'WHERE ' + conditions if conditions else ''} "
               f"{'ORDER BY ' + order_by if order_by else ''} "
               f"{'LIMIT ' + str(limit) if limit is not None else ''}")
        return SQLite.fetch(sql, params=params)

    # 查询所有数据，用法示例：
    # SQLite.select_all("table")
    @staticmethod
    def select_all(table):
        sql = f"SELECT * FROM {SQLite._parse_table_name(table)}"
        return SQLite.fetch(sql)

    # 更新数据，用法示例：
    # SQLite.update("table", ["column1", "column2"], [1, 2], "column1 = ?", condition_params=[3])
    @staticmethod
    def update(table, columns, values, conditions=None, condition_params=None):
        sql = f"UPDATE {SQLite._parse_table_name(table)} SET " \
              f"{','.join([f'{SQLite._parse_column(columns[i])}=?' for i in range(len(columns))])} " \
              f"{'WHERE ' + conditions if conditions else ''}"
        SQLite.execute(sql, params=list(values) + (condition_params or []))

    # 删除数据，用法示例：
    # SQLite.delete("table", "column1 = ?", params=[1])
    @staticmethod
    def delete(table, conditions=None, params=None):
        sql = (f"DELETE FROM {SQLite._parse_table_name(table)} "
               f"{'WHERE ' + conditions if conditions else ''}")
        SQLite.execute(sql, params=params)

# 初始化sqlite数据库，用于创建表和插入初始数据
# 使用 executescript 执行整个 SQL 文件（SQLite 自带解析器处理分号/注释/引号内分号，
# 优于 Python 按 ';' 拆分——后者会破坏存储过程与字符串字面量）
def init_sqlite_db():
    if SQLite.conn is not None and config.db_init_sql:
        Logger.info('init_sqlite_db doing ... ... ... ')
        conn = SQLite.conn
        cur = conn.cursor()
        cur.executescript(config.db_init_sql)
        conn.commit()


init_sqlite_db()
