"""gunicorn 入口配置构造测试（gunicorn 本体不支持 Windows，仅测纯函数与平台守卫）"""

import importlib.util
import sys

import pytest


def _load_module(monkeypatch):
    """加载 wsgi_gunicorn 模块（临时伪装非 Windows 平台，避免模块级退出）"""
    monkeypatch.setattr(sys, 'platform', 'linux')
    spec = importlib.util.spec_from_file_location('wsgi_gunicorn', 'wsgi_gunicorn.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_build_options_default(monkeypatch):
    """默认配置：4 worker + sync worker_class + 绑定配置端口"""
    mod = _load_module(monkeypatch)
    options = mod._build_options()
    assert options['workers'] == 4
    assert options['worker_class'] == 'sync'
    assert '5000' in options['bind']


def test_worker_class_eventlet_when_socketio(monkeypatch):
    """SOCKETIO_ENABLED=true 时应使用 eventlet worker（WebSocket 支持）"""
    mod = _load_module(monkeypatch)
    monkeypatch.setattr(mod.config, 'socketio_enabled', True)
    assert mod._worker_class() == 'eventlet'
    monkeypatch.setattr(mod.config, 'socketio_enabled', False)
    assert mod._worker_class() == 'sync'


def test_build_options_includes_post_worker_init(monkeypatch):
    """协议服务器由 post_worker_init 钩子在每个 worker 内启动"""
    mod = _load_module(monkeypatch)
    options = mod._build_options()
    assert callable(options.get('post_worker_init'))


def test_win32_guard_exits(monkeypatch):
    """Windows 平台守卫：调用 _check_platform 应退出并给出指引"""
    mod = _load_module(monkeypatch)
    monkeypatch.setattr(sys, 'platform', 'win32')
    with pytest.raises(SystemExit) as exc:
        mod._check_platform()
    assert exc.value.code == 1
