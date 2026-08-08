"""日志工具测试：JSON 格式（request_id 字段）、text 格式、handler 配置"""

import json
import logging
import time
import tempfile
import os

from flask_server.util.logger import Logger, JsonFormatter


def _make_record(msg='hello'):
    record = logging.LogRecord(
        name='flask_server', level=logging.INFO, pathname=__file__,
        lineno=1, msg=msg, args=(), exc_info=None,
    )
    record.levelname = 'INFO'
    record.name = 'flask_server'
    return record


def test_json_formatter_with_request_id():
    """JSON 日志应包含 request_id 字段（便于 ELK 链路聚合）"""
    Logger.set_request_id('test-rid-123')
    try:
        out = JsonFormatter().format(_make_record('hello'))
        data = json.loads(out)
        assert data['request_id'] == 'test-rid-123'
        assert data['level'] == 'INFO'
        assert data['message'] == 'hello'
        assert data['logger'] == 'flask_server'
    finally:
        Logger.clear_request_id()


def test_json_formatter_without_request_id():
    """无 request_id 时 JSON 日志不含该字段"""
    Logger.clear_request_id()
    out = JsonFormatter().format(_make_record('hello'))
    data = json.loads(out)
    assert 'request_id' not in data


def test_text_formatter_default():
    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    record = _make_record('hello')
    out = fmt.format(record)
    assert '[INFO]' in out
    assert 'hello' in out


def test_logger_init_creates_rotating_handler():
    """Logger.init 应配置 RotatingFileHandler（轮转参数正确）"""
    from logging.handlers import RotatingFileHandler
    log_file = os.path.join(tempfile.gettempdir(), f'logger_test_{time.time()}.log')
    Logger.init(filename=log_file, level=logging.INFO, max_bytes=1024, backup_count=2)
    try:
        logger = logging.getLogger('flask_server')
        handlers = [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]
        assert len(handlers) == 1
        assert handlers[0].maxBytes == 1024
        assert handlers[0].backupCount == 2
        Logger.info('test log line')
        # 保证落盘后再读
        handlers[0].flush()
        with open(log_file, 'r', encoding='utf-8') as f:
            assert 'test log line' in f.read()
    finally:
        logger = logging.getLogger('flask_server')
        for h in list(logger.handlers):
            logger.removeHandler(h)
            h.close()   # 关闭文件句柄（Windows 下不关闭无法删除文件）
        if os.path.exists(log_file):
            os.remove(log_file)
