import sqlite3
import threading
import os
from ..config import config
from ..util import Logger

# 使用SQLite作为数据库访问层，具体使用参考sqlite3库


class SQLite:

    # 线程锁：sqlite3 连接对象本身非线程安全，check_same_thread=False 仅绕过 Python 层检查
    _lock = threading.Lock()

    # 判断是否使用sqlite3数据库，如果使用，则初始化连接，否则为None
    if config.db_file_path is not None:
        Logger.info(f"Initializing SQLite : {config.db_file_path}")
        # 自动创建数据库文件所在目录，避免目录不存在时 import 即崩溃
        _db_dir = os.path.dirname(config.db_file_path)
        if _db_dir:
            os.makedirs(_db_dir, exist_ok=True)
        conn = sqlite3.connect(config.db_file_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
    else:
        conn = None

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
        with SQLite._lock:
            c = SQLite.conn.cursor()
            if config.debug and config.debug_sql:
                Logger.info(sql)
            c.execute(sql, params or [])
            if ret_row_id:
                row_id = c.lastrowid
            else:
                row_id = None
            SQLite.conn.commit()
            return row_id

    # 执行sql语句，返回结果，多用于select语句，用法示例：
    # SQLite.fetch("SELECT * FROM table WHERE id = ?", [1])
    @staticmethod
    def fetch(sql, params=None, ):
        with SQLite._lock:
            c = SQLite.conn.cursor()
            if config.debug and config.debug_sql:
                Logger.info(sql)
            c.execute(sql, params or [])
            return c.fetchall()

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
def init_sqlite_db():
    if SQLite.conn is not None:
        Logger.info('init_sqlite_db doing ... ... ... ')
        c = SQLite.conn.cursor()
        for sql in config.db_init_sql_list:
            c.execute(sql)
        SQLite.conn.commit()


init_sqlite_db()
