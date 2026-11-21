"""日志工具（请求 ID 链路追踪 + 防日志伪造 + 多级别 API）

对外统一使用 `Logger` 静态方法，内部走命名 logger（避免捕获 SQLAlchemy/redis/urllib3
等第三方库的日志）：

    - 有 app 上下文：使用该 app 的独立 logger（`flask_server.app.<id>`，见 `Logger.configure`）；
    - 无 app 上下文（脚本/CLI/测试）：回退进程默认 logger `flask_server`，首次使用时按
      默认配置初始化（`_default_logger`）。

    - `Logger.debug(txt, exc_info=False)`：调试日志（仅当 logger 级别 ≤ DEBUG 时输出；
      级别由 `DEBUG` 派生：`DEBUG=true` → DEBUG，否则 INFO，见 config）
    - `Logger.info(txt)`：普通运行信息
    - `Logger.warn(txt, exc_info=False)` / `Logger.warning(...)`：告警（两种拼写等价；
      `warn` 为历史写法，保留以兼容既有调用点）
    - `Logger.error(txt, exc_info=False)`：错误
    - `Logger.exception(txt)`：**只能在 `except` 块内调用**——以 ERROR 级别记录并强制
      附带当前异常堆栈（等价于 `Logger.error(txt, exc_info=True)`）。在 except 之外调用
      只会打印 `NoneType: None`，起不到排查作用。

所有方法都经过 `_format_msg`：统一加 `[rid:xxx]` 请求前缀、转义 CR/LF（防攻击者用
换行伪造日志行）。新增级别时也必须走这条路径，不要直接调用 logging。

日志初始化有两个入口：`Logger.configure(app, cfg)`（每个 app 独立，create_app 调用）
与 `Logger.init(...)`（进程默认/测试/脚本）；支持 text/JSON 格式、文件轮转、控制台兜底。
app logger 的 `propagate=True` 指向父 logger `flask_server`（默认无 handler），
测试/工具挂在父 logger 上的 handler 仍能收到 app 日志。
"""

import logging
import json
import os
import threading
import time
import contextvars
from logging.handlers import RotatingFileHandler
from ..config import config

# 请求 ID 上下文（模块级创建，线程安全，兼容 threading/eventlet/asyncio）
_request_id_ctx = contextvars.ContextVar('request_id', default=None)

# 日志工具类，用于输出日志
# 支持 request_id 链路追踪：使用 contextvars 实现线程安全
# factory.py 的 before_request 会调用 Logger.set_request_id()，teardown_request 调用 clear_request_id()
# 使用命名 logger 'flask_server'，避免捕获第三方库（SQLAlchemy/redis/urllib3）的日志


class JsonFormatter(logging.Formatter):
    """JSON 格式日志 formatter，便于接入 ELK/Loki"""

    def format(self, record):
        log_entry = {
            'timestamp': self.formatTime(record, self.datefmt),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }
        # 附带当前 request_id，便于链路追踪（ELK 场景按此字段聚合）
        rid = _request_id_ctx.get()
        if rid:
            log_entry['request_id'] = rid
        # exc_info=True 时附带完整堆栈，否则 JSON 日志排查生产故障时拿不到 traceback
        if record.exc_info:
            log_entry['exception'] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


def _escape_newlines(txt):
    """转义 CR/LF，防日志伪造（log forging）。

    用户可控输入（URL、用户名、请求头等）中的换行会把一条日志拆成两条——
    攻击者可据此伪造 ERROR 行、污染审计记录或触发误告警。
    统一在日志出口转义，避免每个调用点各自清洗（漏一处即失守）。
    """
    text = txt if isinstance(txt, str) else str(txt)
    if '\n' in text or '\r' in text:
        return text.replace('\r', '\\r').replace('\n', '\\n')
    return text


class Logger:

    @staticmethod
    def set_request_id(rid):
        """设置当前请求 ID，后续日志会附带 [rid:xxx]"""
        _request_id_ctx.set(rid)

    @staticmethod
    def clear_request_id():
        """清除当前请求 ID（请求结束时调用）"""
        _request_id_ctx.set(None)

    @staticmethod
    def _format_msg(txt):
        rid = _request_id_ctx.get()
        text = _escape_newlines(txt)
        if rid:
            return f'[rid:{rid}] {text}'
        return text

    @staticmethod
    def init(
                 filename=None,
                 level=logging.INFO,
                 format='%(asctime)s [%(levelname)s] %(message)s',
                 max_bytes=10 * 1024 * 1024,
                 backup_count=5,
                 to_console=False,
                 log_format='text',
                 to_file=True,
                 logger_name='flask_server',
                 **args
                 ):
        # 使用命名 logger，不污染 root；每个 app 可用独立名字（flask_server.app.xxx）
        logger = logging.getLogger(logger_name)
        # 清理已有 handler，避免重复初始化
        # 必须 close()：只 removeHandler 不关闭会泄漏文件描述符，Windows 下文件还会被占用
        # 导致无法删除/轮转（tests/test_logger.py 里手工 close 正是为了绕过这个缺陷）
        for h in list(logger.handlers):
            logger.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass   # 关闭失败不应阻断日志初始化
        logger.setLevel(level)
        logger.propagate = False   # 不向 root 传播
        formatter: logging.Formatter
        if log_format == 'json':
            formatter = JsonFormatter()
        else:
            formatter = logging.Formatter(format)
        if to_file:
            if filename is None:
                filename = time.strftime("%Y_%m_%d_%H_%M_%S", time.localtime())
                filename = f'log_{filename}.log'
            try:
                # 目录不存在时自动创建（与 SQLite 自动建目录一致）；创建/打开失败
                # （权限不足、路径非法等）降级为控制台日志，不因日志路径问题导致启动崩溃
                _log_dir = os.path.dirname(os.path.abspath(filename))
                if _log_dir and not os.path.isdir(_log_dir):
                    os.makedirs(_log_dir, exist_ok=True)
                file_handler = RotatingFileHandler(
                    filename, maxBytes=max_bytes, backupCount=backup_count,
                    encoding='utf-8',
                )
                file_handler.setFormatter(formatter)
                logger.addHandler(file_handler)
            except OSError as e:
                print(f'[Logger WARNING] cannot create log file {filename}: {e}; '
                      'falling back to console logging')
                to_console = True   # 文件日志失败时保证日志仍有一处可写（不静默丢失）
        if to_console:
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)
        # 最后一道兜底：保证 logger 至少有一个 sink。
        # staging/production 预设的 log_to_console 为 False，而 LOG_FILE_PATH 未设置时
        # log_to_file 也是 False —— 两者叠加会得到一个"零 handler + propagate=False"的
        # logger：INFO 级别日志被静默丢弃，只有 WARNING+ 经 logging.lastResort 打到 stderr
        # （无格式、无 request_id、不进文件），表现为"生产环境什么都看不到"。
        if not logger.handlers:
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)
            logger.warning(
                'no log sink configured (LOG_FILE_PATH unset/empty and LOG_TO_CONSOLE=false); '
                'falling back to console logging'
            )
        return logger

    @staticmethod
    def configure(app, cfg=None):
        """按 app 的配置创建独立 logger，并登记到 app.extensions（0.9 起）。

        - logger 名：`flask_server.app.<id>`，每个 app 独立 handler/级别/格式；
        - `propagate=True`：父 logger `flask_server` 默认没有 handler，正常不产生重复输出；
          测试/工具往父 logger 挂 handler 时仍能收到 app 日志（保持既有捕获方式可用）；
        - 返回 logger，同时写入 `app.extensions['flask_server.logger']`。
        """
        from ..module.container import LOGGER_EXT_KEY
        cfg = cfg if cfg is not None else config
        logger = Logger.init(
            filename=cfg.log_filename if cfg.log_to_file else None,
            level=cfg.log_level,
            max_bytes=cfg.log_max_bytes,
            backup_count=cfg.log_backup_count,
            to_console=cfg.log_to_console,
            log_format=cfg.log_format,
            to_file=cfg.log_to_file,
            logger_name=f'flask_server.app.{id(app):x}',
        )
        logger.propagate = True
        # 父 logger 只作为"捕获入口"：不主动输出，避免 app logger + 父 logger 双重写盘
        parent = logging.getLogger('flask_server')
        parent.propagate = False
        app.extensions[LOGGER_EXT_KEY] = logger
        return logger

    @staticmethod
    def _get_logger():
        """当前 app 的 logger（无 app 上下文时回退进程默认 logger）"""
        try:
            from flask import current_app, has_app_context
            if has_app_context():
                from ..module.container import LOGGER_EXT_KEY
                logger = current_app.extensions.get(LOGGER_EXT_KEY)
                if logger is not None:
                    return logger
        except ImportError:   # Flask 未安装（纯脚本）
            pass
        return _default_logger()

    @staticmethod
    def debug(txt, exc_info=False):
        """调试日志（经 _format_msg：request_id 前缀 + CR/LF 转义）"""
        Logger._get_logger().debug(Logger._format_msg(txt), exc_info=exc_info)

    @staticmethod
    def info(txt):
        Logger._get_logger().info(Logger._format_msg(txt))

    @staticmethod
    def warn(txt, exc_info=False):
        Logger._get_logger().warning(Logger._format_msg(txt), exc_info=exc_info)

    @staticmethod
    def warning(txt, exc_info=False):
        """`warn` 的显式别名（两种拼写等价）；新代码建议用 warning

        保留 warn 是因为既有调用点很多，且 logging 模块自身也兼容两种拼写。
        """
        Logger.warn(txt, exc_info=exc_info)

    @staticmethod
    def error(txt, exc_info=False):
        Logger._get_logger().error(Logger._format_msg(txt), exc_info=exc_info)

    @staticmethod
    def exception(txt):
        """ERROR 级别 + 强制附带异常堆栈；**只能在 `except` 块内调用**

        在 except 块之外调用时 sys.exc_info() 为空，只会得到 `NoneType: None`，
        无法定位问题——那种场景请改用 `Logger.error(txt)`。
        """
        Logger._get_logger().error(Logger._format_msg(txt), exc_info=True)


_default_logger_instance = None
_default_logger_lock = threading.Lock()


def _default_logger():
    """进程默认 logger（无 app 上下文的脚本/CLI/测试用），首次使用时按默认配置初始化"""
    global _default_logger_instance
    if _default_logger_instance is not None:
        # conftest/测试可能把父 logger 的 level 恢复成 NOTSET；补回默认级别，
        # 否则后续 INFO 会被 root 的 WARNING 过滤（只在"复用缓存 logger"路径发生）。
        if _default_logger_instance.level == logging.NOTSET:
            _default_logger_instance.setLevel(config.log_level)
        return _default_logger_instance
    with _default_logger_lock:
        if _default_logger_instance is None:
            logger = logging.getLogger('flask_server')
            if logger.handlers:
                # 外部已配置（测试/conftest/手工 Logger.init）：直接复用其 handler。
                # 但 logger 级别可能仍是 NOTSET（继承 root 的 WARNING），会吞掉 INFO；
                # 这里补上进程默认级别，保证"先挂 handler、后首次 Logger.info"也能输出。
                if logger.level == logging.NOTSET:
                    logger.setLevel(config.log_level)
                _default_logger_instance = logger
            else:
                _default_logger_instance = Logger.init(
                    filename=config.log_filename,
                    level=config.log_level,
                    max_bytes=config.log_max_bytes,
                    backup_count=config.log_backup_count,
                    to_console=config.log_to_console,
                    log_format=config.log_format,
                    to_file=config.log_to_file,
                )
    return _default_logger_instance
