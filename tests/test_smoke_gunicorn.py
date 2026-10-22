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
