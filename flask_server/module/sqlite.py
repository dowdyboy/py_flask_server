import sqlite3
from ..config import config
from ..util import Logger

# 使用SQLite作为数据库访问层，具体使用参考sqlite3库


class SQLite:

    # 判断是否使用sqlite3数据库，如果使用，则初始化连接，否则为None
    if config.db_file_path is not None:
        Logger.info(f"Initializing SQLite : {config.db_file_path}")
        conn = sqlite3.connect(config.db_file_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
    else:
        conn = None

    # 将值转换为字符串，用于sql语句中
    @staticmethod
    def _parse_value(value):
        if isinstance(value, str):
            return f"'{value}'"
        elif isinstance(value, int):
            return f"{value}"
        elif isinstance(value, float):
            return f"{value}"
        else:
            raise Exception(f"Unsupported value type: {type(value)}")

    # 将值列表转换为字符串，用于sql语句中
    @staticmethod
    def _parse_values(values):
        return ','.join([SQLite._parse_value(v) for v in values])

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
        c = SQLite.conn.cursor()
        if config.debug:
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
        c = SQLite.conn.cursor()
        if config.debug:
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
    # SQLite.select("table", ["column1", "column2"], "column1 = ?", [1])
    @staticmethod
    def select(table, columns=None, conditions=None, order_by=None, limit=None, ):
        sql = (f"SELECT {SQLite._parse_columns(columns)} "
               f"FROM {SQLite._parse_table_name(table)} "
               f"{'WHERE ' + conditions if conditions else ''} "
               f"{'ORDER BY ' + order_by if order_by else ''} "
               f"{'LIMIT ' + str(limit) if limit else ''}")
        return SQLite.fetch(sql)

    # 查询所有数据，用法示例：
    # SQLite.select_all("table")
    @staticmethod
    def select_all(table):
        sql = f"SELECT * FROM {SQLite._parse_table_name(table)}"
        return SQLite.fetch(sql)

    # 更新数据，用法示例：
    # SQLite.update("table", ["column1", "column2"], [1, 2], "column1 = ?")
    @staticmethod
    def update(table, columns, values, conditions=None):
        sql = f"UPDATE {SQLite._parse_table_name(table)} SET " \
              f"{','.join([f'{SQLite._parse_column(columns[i])}=?' for i in range(len(columns))])} " \
              f"{'WHERE ' + conditions if conditions else ''}"
        SQLite.execute(sql, params=values)

    # 删除数据，用法示例：
    # SQLite.delete("table", "column1 = ?")
    @staticmethod
    def delete(table, conditions=None):
        sql = (f"DELETE FROM {SQLite._parse_table_name(table)} "
               f"{'WHERE ' + conditions if conditions else ''}")
        SQLite.execute(sql)

# 初始化sqlite数据库，用于创建表和插入初始数据
def init_sqlite_db():
    if SQLite.conn is not None:
        Logger.info(f'init_sqlite_db doing ... ... ... ')
        c = SQLite.conn.cursor()
        for sql in config.db_init_sql_list:
            c.execute(sql)
        SQLite.conn.commit()


init_sqlite_db()


# def init_sqlite_db():
#     c = SQLite.conn.cursor()
#     c.execute('''
#             CREATE TABLE IF NOT EXISTS `stu` (
#                 `sid` INTEGER PRIMARY KEY AUTOINCREMENT,
#                 `name` TEXT NOT NULL,
#                 `score` REAL NOT NULL
#             )
#         ''')
#     c.execute('''
#         CREATE TABLE IF NOT EXISTS `book` (
#                     `bid` TEXT PRIMARY KEY,
#                     `uid` TEXT NOT NULL,
#                     `name` TEXT NOT NULL,
#                     `desc` TEXT NOT NULL DEFAULT '',
#                     `face_img` TEXT NOT NULL,
#                     `es_index` TEXT DEFAULT NULL,
#                     `status` INTEGER NOT NULL DEFAULT 0,
#                     `create_time` INTEGER NOT NULL
#                 )
#     ''')
#     c.execute('''
#         CREATE TABLE IF NOT EXISTS `user` (
#                     `uid` TEXT PRIMARY KEY,
#                     `username` TEXT UNIQUE NOT NULL,
#                     `passwd` TEXT NOT NULL,
#                     `create_time` INTEGER NOT NULL
#                 )
#     ''')
#     c.execute('''
#         CREATE TABLE IF NOT EXISTS `session` (
#                     `sid` TEXT PRIMARY KEY,
#                     `uid` TEXT NOT NULL,
#                     `chat` TEXT NOT NULL,
#                     `md` TEXT NOT NULL,
#                     `create_time` INTEGER NOT NULL
#                 )
#     ''')
#
#     SQLite.conn.commit()
