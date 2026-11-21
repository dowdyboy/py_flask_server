"""日志工具测试：JSON 格式（request_id 字段）、text 格式、handler 配置

handler 的恢复由 tests/conftest.py 的 autouse fixture 统一负责：这里不再手工
remove/close 全部 handler（旧写法会让后续用例面对零 handler 的 logger）。
"""

import json
import logging
import sys

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


def test_json_formatter_includes_exception():
    """exc_info=True 时 JSON 日志应包含 exception 堆栈字段（否则 ELK 拿不到 traceback）"""
    try:
        raise ValueError('boom-for-json-log')
    except ValueError:
        record = _make_record('oops')
        record.exc_info = sys.exc_info()
        out = JsonFormatter().format(record)
    data = json.loads(out)
    assert 'exception' in data
    assert 'ValueError' in data['exception']
    assert 'boom-for-json-log' in data['exception']


def test_json_formatter_no_exception_field_without_exc_info():
    """无 exc_info 时 JSON 日志不含 exception 字段"""
    out = JsonFormatter().format(_make_record('hello'))
    assert 'exception' not in json.loads(out)


def test_text_formatter_default():
    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    record = _make_record('hello')
    out = fmt.format(record)
    assert '[INFO]' in out
    assert 'hello' in out


def test_logger_init_creates_rotating_handler(tmp_path):
    """Logger.init 应配置 RotatingFileHandler（轮转参数正确）"""
    from logging.handlers import RotatingFileHandler

    log_file = tmp_path / 'logger_test.log'
    Logger.init(filename=str(log_file), level=logging.INFO, max_bytes=1024, backup_count=2)
    logger = logging.getLogger('flask_server')
    handlers = [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]
    assert len(handlers) == 1
    assert handlers[0].maxBytes == 1024
    assert handlers[0].backupCount == 2
    Logger.info('test log line')
    handlers[0].flush()          # 保证落盘后再读
    assert 'test log line' in log_file.read_text(encoding='utf-8')


def test_logger_init_creates_missing_directory(tmp_path):
    """LOG_FILE_PATH 指向不存在的目录时自动创建（修复前 import 即崩溃）"""
    from logging.handlers import RotatingFileHandler

    log_dir = tmp_path / 'nested' / 'logs'
    log_file = log_dir / 'app.log'
    Logger.init(filename=str(log_file), level=logging.INFO, to_console=False)
    logger = logging.getLogger('flask_server')
    assert log_dir.is_dir()
    handlers = [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]
    assert len(handlers) == 1
    Logger.info('dir auto-created')
    handlers[0].flush()
    assert 'dir auto-created' in log_file.read_text(encoding='utf-8')


def test_logger_init_falls_back_to_console_on_oserror(tmp_path):
    """日志文件无法创建时（父路径是文件等）降级为控制台日志，不抛异常"""
    blocker = tmp_path / 'blocker'
    blocker.write_text('i am a file, not a dir', encoding='utf-8')
    bad_log_file = blocker / 'sub' / 'app.log'
    # 不应抛异常；文件 handler 缺失，控制台 handler 兜底
    Logger.init(filename=str(bad_log_file), level=logging.INFO, to_console=False)
    logger = logging.getLogger('flask_server')
    from logging import StreamHandler
    from logging.handlers import RotatingFileHandler
    handlers = logger.handlers
    assert not [h for h in handlers if isinstance(h, RotatingFileHandler)]
    assert [h for h in handlers if isinstance(h, StreamHandler)]


def test_logger_init_falls_back_to_console_when_no_sink():
    """P0 回归：没有任何 sink 时必须兜底加控制台 handler，不能静默丢弃 INFO。

    修复前 staging/production 预设的 log_to_console=False 叠加"未设置 LOG_FILE_PATH"
    得到零 handler + propagate=False 的 logger：Logger.info 全部消失，只有 WARNING+
    经 logging.lastResort 打到 stderr（无格式、无 request_id、不进文件）。
    """
    from logging import StreamHandler

    logger = logging.getLogger('flask_server')
    Logger.init(filename=None, level=logging.INFO, to_console=False, to_file=False)
    assert [h for h in logger.handlers if isinstance(h, StreamHandler)], \
        '无 sink 时必须有兜底控制台 handler'


def test_format_msg_escapes_newlines():
    """日志伪造防护：消息中的 CR/LF 必须转义为字面量，不能拆行"""
    from flask_server.util.logger import _escape_newlines

    assert _escape_newlines('a\nb') == 'a\\nb'
    assert _escape_newlines('a\r\nb') == 'a\\r\\nb'
    assert _escape_newlines('plain') == 'plain'
    assert _escape_newlines(123) == '123'
    assert _escape_newlines(None) == 'None'


def test_logger_info_single_line_with_injected_newline(tmp_path):
    """端到端：用户输入带换行的 URL/用户名写日志后，文件里只增加一行（不可伪造记录）"""
    from logging.handlers import RotatingFileHandler

    log_file = tmp_path / 'inject.log'
    Logger.init(filename=str(log_file), level=logging.INFO, to_console=False)
    logger = logging.getLogger('flask_server')
    Logger.set_request_id('rid-1')
    try:
        Logger.warn('rate limit exceeded (path): /api/x\n2026-01-01 [ERROR] fake injected record')
        for h in logger.handlers:
            if isinstance(h, RotatingFileHandler):
                h.flush()
        lines = [ln for ln in log_file.read_text(encoding='utf-8').splitlines() if ln.strip()]
        assert len(lines) == 1, f'注入的换行被当成新日志行: {lines}'
        assert '\\n' in lines[0]
        assert 'fake injected record' in lines[0]
        assert '[rid:rid-1]' in lines[0]
    finally:
        Logger.clear_request_id()


def test_default_logger_restores_level_when_reusing_external_handlers(monkeypatch):
    """外部先挂 handler（level=NOTSET）再首次写日志：必须补上默认级别，不能吞 INFO。

    回归：CI test-mysql job 只跑 sqlalchemy/sqlite 用例，默认 logger 在父级 level 被
    conftest 恢复成 NOTSET 后复用缓存实例，INFO 全部被 root WARNING 过滤。
    """
    import flask_server.util.logger as logger_module

    parent = logging.getLogger('flask_server')
    monkeypatch.setattr(logger_module, '_default_logger_instance', None)
    parent.setLevel(logging.NOTSET)
    handler = logging.NullHandler()
    parent.addHandler(handler)
    try:
        selected = Logger._get_logger()
        assert selected is parent
        assert selected.level == logger_module.config.log_level
    finally:
        parent.removeHandler(handler)
