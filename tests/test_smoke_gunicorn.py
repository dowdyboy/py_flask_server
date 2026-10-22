"""gunicorn 冒烟脚本（scripts/smoke_gunicorn.py）的辅助函数测试。

真正的多 worker 行为由 CI 的 gunicorn job 覆盖（gunicorn 不支持 Windows）；
这里覆盖脚本自身的环境构造/端口探测/跳过逻辑，避免"测试脚本自己坏了"。
"""

import importlib.util
import os
import socket
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load():
    path = os.path.join(PROJECT_ROOT, 'scripts', 'smoke_gunicorn.py')
    spec = importlib.util.spec_from_file_location('smoke_gunicorn_under_test', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_child_env_overrides_local_settings(monkeypatch):
    """子进程环境必须覆盖本地 .env：不能连开发者的真实 MySQL/Redis"""
    mod = _load()
    monkeypatch.setenv('SQLALCHEMY_URI', 'mysql+pymysql://user:pw@prod-db/app')
    monkeypatch.setenv('REDIS_URL', 'redis://prod-redis:6379/0')
    env = mod._child_env(http_port=1234, tcp_port=5678, workers=2, tcp_enabled=True)

    assert env['SQLALCHEMY_URI'] == ''
    assert env['REDIS_URL'] == ''
    assert env['SERVER_PORT'] == '1234'
    assert env['TCP_PORT'] == '5678'
    assert env['WORKER_NUM'] == '2'
    assert env['TCP_ENABLED'] == 'true'
    assert env['APP_ENV'] == 'production'
    assert env['LOG_FILE_PATH'] == ''
    assert 'PYTEST_ADDOPTS' not in env


def test_child_env_tcp_disabled():
    mod = _load()
    env = mod._child_env(http_port=1, tcp_port=2, workers=1, tcp_enabled=False)
    assert env['TCP_ENABLED'] == 'false'


def test_free_port_is_bindable():
    mod = _load()
    port = mod._free_port()
    assert 1 <= port <= 65535
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(('127.0.0.1', port))


def test_tcp_open_detects_listener_and_closed_port():
    mod = _load()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        assert mod._tcp_open(port) is True
    # 监听已关闭 → 连接失败
    assert mod._tcp_open(port, timeout=0.2) is False


def test_http_ok_false_for_unused_port():
    mod = _load()
    closed_port = mod._free_port()
    assert mod._http_ok(f'http://127.0.0.1:{closed_port}/api/v1/healthz', timeout=0.3) is False


def test_read_missing_file_returns_empty(tmp_path):
    mod = _load()
    assert mod._read(str(tmp_path / 'not-exist.log')) == ''


def test_main_skips_on_windows(monkeypatch, capsys):
    """Windows 下必须跳过并以 0 退出（否则 CI 的 Windows job 会红）"""
    mod = _load()
    monkeypatch.setattr(sys, 'platform', 'win32')
    assert mod.main() == 0
    assert '[skip]' in capsys.readouterr().out


def test_install_and_remove_handler(tmp_path, monkeypatch):
    """临时 handler 的安装/删除：内容可被 handler 自动发现机制导入，用完不留痕"""
    mod = _load()
    handler = tmp_path / 'handler' / '_smoke_tcp_echo.py'
    monkeypatch.setattr(mod, 'HANDLER_PATH', str(handler))

    assert mod._install_handler() is True
    assert handler.exists()
    source = handler.read_text(encoding='utf-8')
    assert '@tcp_server.on_message' in source
    assert 'def on_message(conn, data, addr):' in source

    mod._remove_handler(True)
    assert not handler.exists()


def test_install_handler_does_not_overwrite_existing(tmp_path, monkeypatch):
    """目标文件已存在时不动它（避免踩到用户自己的 handler）"""
    mod = _load()
    handler = tmp_path / '_smoke_tcp_echo.py'
    handler.write_text('# user file', encoding='utf-8')
    monkeypatch.setattr(mod, 'HANDLER_PATH', str(handler))

    assert mod._install_handler() is False
    assert handler.read_text(encoding='utf-8') == '# user file'
    mod._remove_handler(False)          # created=False 时不应删除
    assert handler.exists()


def test_tcp_echo_roundtrip_against_local_echo(tmp_path):
    """_tcp_echo_roundtrip：连上监听端并收回应答"""
    import threading

    mod = _load()

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.bind(('127.0.0.1', 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def fake_echo():
            # 真实框架按行定界，回调里拿到的是**已剥离分隔符**的 payload
            conn, _ = srv.accept()
            with conn:
                conn.sendall(b'echo: ' + conn.recv(1024).rstrip(b'\n'))

        t = threading.Thread(target=fake_echo, daemon=True)
        t.start()
        assert mod._tcp_echo_roundtrip(port) == b'echo: ping'
        t.join(timeout=2)
