import logging
import json
import time
from logging.handlers import RotatingFileHandler
from ..config import config

# 日志工具类，用于输出日志
# 支持 request_id 链路追踪：使用 contextvars 实现线程安全
# app.py 的 before_request 会调用 Logger.set_request_id()，teardown_request 调用 clear_request_id()
# 使用命名 logger 'flask_server'，避免捕获第三方库（SQLAlchemy/redis/urllib3）的日志


class JsonFormatter(logging.Formatter):
    """JSON 格式日志 formatter，便于接入 ELK/Loki"""

    def format(self, record):
        log_entry = {
            'timestamp': self.formatTime(record, self.datefmt),
            'level': record.levelname,
            'message': record.getMessage(),
        }
        return json.dumps(log_entry, ensure_ascii=False)


class Logger:

    # contextvars 线程安全，兼容 threading/eventlet/asyncio
    _request_id_ctx = None

    @classmethod
    def _ensure_ctx(cls):
        if cls._request_id_ctx is None:
            import contextvars
            cls._request_id_ctx = contextvars.ContextVar('request_id', default=None)

    @staticmethod
    def set_request_id(rid):
        """设置当前请求 ID，后续日志会附带 [rid:xxx]"""
        Logger._ensure_ctx()
        Logger._request_id_ctx.set(rid)

    @staticmethod
    def clear_request_id():
        """清除当前请求 ID（请求结束时调用）"""
        Logger._ensure_ctx()
        Logger._request_id_ctx.set(None)

    @staticmethod
    def _format_msg(txt):
        Logger._ensure_ctx()
        rid = Logger._request_id_ctx.get()
        if rid:
            return f'[rid:{rid}] {txt}'
        return txt

    @staticmethod
    def init(
                 filename=None,
                 level=logging.INFO,
                 format='%(asctime)s [%(levelname)s] %(message)s',
                 max_bytes=10 * 1024 * 1024,
                 backup_count=5,
                 to_console=False,
                 log_format='text',
                 **args
                 ):
        if filename is None:
            filename = time.strftime("%Y_%m_%d_%H_%M_%S", time.localtime())
            filename = f'log_{filename}.log'
        # 使用命名 logger，不污染 root
        logger = logging.getLogger('flask_server')
        # 清理已有 handler，避免重复初始化
        for h in list(logger.handlers):
            logger.removeHandler(h)
        logger.setLevel(level)
        logger.propagate = False   # 不向 root 传播
        if log_format == 'json':
            formatter = JsonFormatter()
        else:
            formatter = logging.Formatter(format)
        file_handler = RotatingFileHandler(
            filename, maxBytes=max_bytes, backupCount=backup_count,
            encoding='utf-8',
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        if to_console:
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)

    @staticmethod
    def info(txt):
        logging.getLogger('flask_server').info(Logger._format_msg(txt))

    @staticmethod
    def warn(txt):
        logging.getLogger('flask_server').warning(Logger._format_msg(txt))

    @staticmethod
    def error(txt):
        logging.getLogger('flask_server').error(Logger._format_msg(txt))


Logger.init(filename=config.log_filename,
            level=config.log_level,
            max_bytes=config.log_max_bytes,
            backup_count=config.log_backup_count,
            to_console=config.log_to_console,
            log_format=config.log_format, )
