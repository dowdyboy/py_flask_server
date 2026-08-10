"""协议服务器基类测试：注册/重复注册/生命周期/异常钩子/配置默认值"""

import logging
import socket
import threading

import pytest

from flask_server.module.protocol_server import ProtocolServer
from flask_server.module.tcp_server import TcpServer


class _CaptureHandler(logging.Handler):
    """附加到 flask_server logger 的捕获 handler（logger 不向 root 传播，需直接挂载）"""

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _attach_logger_capture():
    handler = _CaptureHandler()
    logging.getLogger('flask_server').addHandler(handler)
    return handler


def _new_tcp_server(**kwargs):
    params = {'host': '127.0.0.1', 'port': 0}
    params.update(kwargs)
    return TcpServer(**params)


def test_config_defaults_disabled():
    """默认配置下 TCP/UDP 均关闭、TCP 帧为换行定界（与测试隔离环境一致）"""
    from flask_server.config import config
    assert config.tcp_enabled is False
    assert config.udp_enabled is False
    assert config.tcp_framing == 'line'
    assert config.tcp_frame_separator == b'\n'
    assert config.tcp_frame_length == 1024
    assert config.tcp_frame_head == b''
    assert config.tcp_frame_tail == b''


def test_register_and_overwrite_warns():
    """装饰器注册处理器；重复注册告警并覆盖"""
    server = _new_tcp_server()

    @server.on_message
    def first(conn, data, addr):
        return 'first'

    @server.on_message
    def second(conn, data, addr):
        return 'second'

    assert server._handlers['on_message'] is second
    assert server._dispatch('on_message', (None, b'x', ('h', 1))) == 'second'


def test_dispatch_no_handler_returns_none():
    server = _new_tcp_server()
    assert server._dispatch('on_message', (None, b'x', ('h', 1))) is None


def test_dispatch_error_calls_on_error_hook():
    """处理器异常：Logger.error 记录，on_error 钩子被调用，返回值归一为 None"""
    server = _new_tcp_server()
    errors = []

    @server.on_message
    def boom(conn, data, addr):
        raise ValueError('boom')

    @server.on_error
    def on_error(e, *args):
        errors.append(e)

    result = server._dispatch('on_message', (None, b'x', ('h', 1)), ctx=' test')
    assert result is None
    assert len(errors) == 1
    assert isinstance(errors[0], ValueError)


def test_on_error_hook_exception_does_not_raise():
    """on_error 钩子自身异常不影响调用方（记录后吞掉）"""
    server = _new_tcp_server()

    @server.on_message
    def boom(conn, data, addr):
        raise ValueError('boom')

    @server.on_error
    def on_error(e, *args):
        raise RuntimeError('hook boom')

    assert server._dispatch('on_message', (None, b'x', ('h', 1))) is None


def test_start_without_handler_warns_not_started():
    """启用但未注册 on_message 处理器：告警且不启动"""
    server = _new_tcp_server()
    assert server.start() is False
    assert server.is_running is False
    assert server.bound_address is None
    server.stop()   # 幂等


def test_start_thread_failure_rolls_back(monkeypatch):
    """serve 线程启动失败：回滚状态并关闭 socket（is_running 不误报、端口不泄漏）"""
    server = _new_tcp_server()

    @server.on_message
    def on_message(conn, data, addr):
        pass

    def boom(self, *args, **kwargs):
        raise RuntimeError('thread start boom')

    monkeypatch.setattr(threading.Thread, 'start', boom)
    with pytest.raises(RuntimeError):
        server.start()
    monkeypatch.undo()
    assert server.is_running is False
    assert server.bound_address is None
    # 端口已释放：恢复后可以正常重启
    assert server.start() is True
    server.stop()


def test_start_stop_idempotent():
    """start/stop 重复调用均无副作用；bound_address 提供真实端口"""
    server = _new_tcp_server()

    @server.on_message
    def on_message(conn, data, addr):
        return b'echo: ' + data

    assert server.start() is True
    assert server.is_running is True
    port = server.bound_address[1]
    assert isinstance(port, int) and port > 0
    assert server.start() is True   # 重复启动
    server.stop()
    server.stop()                    # 重复停止
    assert server.is_running is False


def test_bind_conflict_raises_oserror():
    """端口被占用时 start 抛出 OSError（不静默）"""
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(('127.0.0.1', 0))
    holder.listen(1)
    port = holder.getsockname()[1]

    server = _new_tcp_server(port=port)

    @server.on_message
    def on_message(conn, data, addr):
        pass

    with pytest.raises(OSError):
        server.start()
    assert server.is_running is False
    holder.close()


def test_protocol_server_is_abstract():
    """基类未实现 _create_server：调用 start 抛 NotImplementedError"""
    server = ProtocolServer('abstract')

    @server.on_message
    def on_message(*args):
        pass

    with pytest.raises(NotImplementedError):
        server.start()


def test_invalid_framing_falls_back_line():
    """直接构造时 framing 非法值 → 告警回退 line（实例层与 config 层校验一致）"""
    server = TcpServer(host='127.0.0.1', port=0, framing='bogus')
    assert server.framing == 'line'


def test_max_connections_zero_unlimited():
    """并发上限 ≤0 表示不限制（信号量为 None，acquire 恒通过）"""
    server = TcpServer(host='127.0.0.1', port=0, max_connections=0)
    assert server._semaphore is None
    assert server._acquire_slot() is True
    server._release_slot()


def test_acquire_slot_reject_warn_cooldown():
    """并发超限告警带冷却：连续拒绝仅首次告警，冷却后输出累计摘要（防日志刷屏）"""
    server = TcpServer(host='127.0.0.1', port=0, max_connections=1)
    handler = _attach_logger_capture()
    try:
        # 占用唯一槽位
        assert server._acquire_slot() is True
        # 冷却窗口内连续 5 次拒绝：仅首次产生告警
        for _ in range(5):
            assert server._acquire_slot() is False
        rejects = [r for r in handler.records if 'concurrency limit' in r.getMessage()]
        assert len(rejects) == 1
        assert '(+1 since last report)' in rejects[0].getMessage()

        # 冷却期结束后再次拒绝 → 输出累计摘要（把告警时间拨回以模拟窗口过期）
        server._last_reject_warn_ts = 0.0
        server._reject_count = 0
        assert server._acquire_slot() is False
        rejects = [r for r in handler.records if 'concurrency limit' in r.getMessage()]
        assert len(rejects) == 2
        assert '(+1 since last report)' in rejects[1].getMessage()

        # 释放槽位后恢复正常获取
        server._release_slot()
        assert server._acquire_slot() is True
        server._release_slot()
    finally:
        logging.getLogger('flask_server').removeHandler(handler)


def test_handler_import_failure_tolerated(tmp_path, monkeypatch):
    """handler 模块导入失败：告警跳过该模块，不中断其他模块"""
    import sys
    pkg = tmp_path / 'fake_handlers'
    pkg.mkdir()
    (pkg / '__init__.py').write_text('', encoding='utf-8')
    (pkg / 'bad_module.py').write_text('raise RuntimeError("boom")', encoding='utf-8')
    (pkg / 'good_module.py').write_text('value = 42', encoding='utf-8')
    sys.path.insert(0, str(tmp_path))
    try:
        from flask_server.handler import auto_import_handlers
        auto_import_handlers(package_name='fake_handlers')
        import fake_handlers.good_module
        assert fake_handlers.good_module.value == 42
    finally:
        sys.path.remove(str(tmp_path))


def test_should_start_protocol_servers(monkeypatch):
    """reloader 守卫：非 debug 启动；debug 下仅 reloader 子进程（真实服务进程）启动，
    父进程（监督者）不启动，避免子进程二次绑定端口 EADDRINUSE 崩溃循环"""
    import importlib
    server_module = importlib.import_module('server')

    monkeypatch.setattr(server_module.config, 'debug', False)
    monkeypatch.delenv('WERKZEUG_RUN_MAIN', raising=False)
    assert server_module.should_start_protocol_servers() is True

    monkeypatch.setattr(server_module.config, 'debug', True)
    monkeypatch.delenv('WERKZEUG_RUN_MAIN', raising=False)
    assert server_module.should_start_protocol_servers() is False   # reloader 父进程

    monkeypatch.setenv('WERKZEUG_RUN_MAIN', 'true')
    assert server_module.should_start_protocol_servers() is True    # reloader 子进程
