"""waitress 生产入口（wsgi.py）测试：优雅关闭流程、二次信号、看门狗超时"""

import importlib.util
import signal
import sys

import pytest


def _load_wsgi(monkeypatch):
    """以独立模块名加载 wsgi.py，避免与其它入口的同名模块冲突"""
    monkeypatch.setattr(sys, 'platform', 'win32' if sys.platform == 'win32' else 'linux')
    spec = importlib.util.spec_from_file_location('wsgi_entry_under_test', 'wsgi.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeServer:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


class _FakeTimer:
    """替换 threading.Timer：只记录回调，不在测试中真的等 15 秒"""
    instances = []

    def __init__(self, interval, function):
        self.interval = interval
        self.function = function
        self.daemon = False
        self.started = False
        _FakeTimer.instances.append(self)

    def start(self):
        self.started = True


@pytest.fixture
def wsgi_mod(monkeypatch):
    _FakeTimer.instances = []
    mod = _load_wsgi(monkeypatch)
    monkeypatch.setattr(mod.threading, 'Timer', _FakeTimer)
    return mod


def test_cleanup_resources_is_idempotent(wsgi_mod):
    """清理逻辑可重复调用且不抛异常（信号路径与正常退出路径都会调用）"""
    wsgi_mod._cleanup_resources()
    wsgi_mod._cleanup_resources()


def test_cleanup_stops_embedded_task_worker(wsgi_mod):
    """内嵌任务 worker 必须在释放 DB 之前停掉（否则在途任务会遇到已 dispose 的引擎）"""
    stopped = []

    class _FakeWorker:
        def stop(self, timeout=15):
            stopped.append(1)

    wsgi_mod._embedded_worker = _FakeWorker()
    wsgi_mod._cleanup_resources()
    assert stopped == [1]
    assert wsgi_mod._embedded_worker is None
    # 再清理一次不报错（幂等）
    wsgi_mod._cleanup_resources()
    assert stopped == [1]


def test_cleanup_survives_embedded_worker_stop_error(wsgi_mod):
    """内嵌 worker 停止失败不应中断后续清理（DB/Redis 仍要释放）"""
    class _BrokenWorker:
        def stop(self, timeout=15):
            raise RuntimeError('boom')

    wsgi_mod._embedded_worker = _BrokenWorker()
    wsgi_mod._cleanup_resources()
    assert wsgi_mod._embedded_worker is None


def test_waitress_options_keep_forwarded_headers(wsgi_mod, monkeypatch):
    """waitress 3 默认清除 X-Forwarded-*：必须显式关闭，否则 TRUSTED_PROXIES 逻辑失效。

    若被清除，代理后所有请求的 remote_addr 都是代理 IP：
    限流退化成全站共享一个桶、审计日志丢失真实来源。
    """
    monkeypatch.setattr(wsgi_mod.config, 'host', '0.0.0.0')
    monkeypatch.setattr(wsgi_mod.config, 'port', 8080)
    monkeypatch.setattr(wsgi_mod.config, 'thread_num', 7)
    options = wsgi_mod._waitress_options()

    assert options['clear_untrusted_proxy_headers'] is False
    assert options['host'] == '0.0.0.0'
    assert options['port'] == 8080
    assert options['threads'] == 7


def test_signal_handler_stops_accept_and_drains(wsgi_mod, monkeypatch):
    """SIGTERM：停止 accept（server.close）而非直接杀进程，并启动看门狗"""
    handlers = {}
    monkeypatch.setattr('signal.signal', lambda sig, h: handlers.__setitem__(sig, h))
    server = _FakeServer()

    state = wsgi_mod._install_signal_handlers(server, graceful_timeout=7)
    assert state['triggered'] is False
    assert signal.SIGTERM in handlers and signal.SIGINT in handlers

    handlers[signal.SIGTERM](signal.SIGTERM, None)

    assert state['triggered'] is True
    assert server.closed == 1, '收到信号后必须关闭监听 socket（停止 accept）'
    timer = _FakeTimer.instances[-1]
    assert timer.started is True
    assert timer.interval == 7, '看门狗必须在优雅超时后兜底强制退出'
    assert timer.daemon is True, '看门狗线程不能阻止进程退出'


def test_watchdog_forces_exit_after_timeout(wsgi_mod, monkeypatch):
    """看门狗超时 → 记录原因并强制退出（防 keep-alive 空连接挂住进程）"""
    exit_codes = []
    handlers = {}
    monkeypatch.setattr(wsgi_mod.os, '_exit', lambda code: exit_codes.append(code))
    monkeypatch.setattr(wsgi_mod, '_cleanup_resources', lambda: None)
    monkeypatch.setattr('signal.signal', lambda sig, h: handlers.__setitem__(sig, h))

    wsgi_mod._install_signal_handlers(_FakeServer(), graceful_timeout=7)
    handlers[signal.SIGTERM](signal.SIGTERM, None)   # 触发关闭流程 → 创建看门狗
    timer = _FakeTimer.instances[-1]
    timer.function()                                # 模拟 drain 超时

    assert exit_codes == [0]


def test_second_signal_exits_immediately(wsgi_mod, monkeypatch):
    """二次信号（运维连按 Ctrl+C）→ 跳过 drain 立即退出"""
    handlers = {}
    monkeypatch.setattr('signal.signal', lambda sig, h: handlers.__setitem__(sig, h))
    cleaned = []
    monkeypatch.setattr(wsgi_mod, '_cleanup_resources', lambda: cleaned.append(1))
    monkeypatch.setattr(wsgi_mod.os, '_exit', lambda code: (_ for _ in ()).throw(SystemExit(code)))
    server = _FakeServer()

    handlers_state = wsgi_mod._install_signal_handlers(server, graceful_timeout=7)
    handlers[signal.SIGINT](signal.SIGINT, None)     # 第一次：进入关闭流程
    assert handlers_state['triggered'] is True

    with pytest.raises(SystemExit):
        handlers[signal.SIGINT](signal.SIGINT, None)  # 第二次：立即退出
    assert cleaned, '强制退出前仍应释放依赖（DB/Redis/协议服务器）'


def test_server_close_error_does_not_break_shutdown(wsgi_mod, monkeypatch):
    """server.close() 抛异常（socket 已关闭等）不应中断关闭流程"""
    handlers = {}
    monkeypatch.setattr('signal.signal', lambda sig, h: handlers.__setitem__(sig, h))

    class _BrokenServer:
        def close(self):
            raise OSError('already closed')

    state = wsgi_mod._install_signal_handlers(_BrokenServer(), graceful_timeout=7)
    handlers[signal.SIGTERM](signal.SIGTERM, None)
    assert state['triggered'] is True
    assert _FakeTimer.instances[-1].started is True
