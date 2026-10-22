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


class _FakeWorker:
    def __init__(self, age=0, pid=4321):
        self.age = age
        self.pid = pid


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


def test_multi_worker_skips_protocol_servers(monkeypatch):
    """P0 回归：多 worker 时不得启动协议服务器。

    修复前每个 worker 都调用 start_protocol_servers()，2..N 号 worker 因端口被占用
    抛 OSError → post_worker_init 异常 → worker 退出 → master 重拉 → 崩溃循环。
    """
    mod = _load_module(monkeypatch)
    calls = []
    monkeypatch.setattr('flask_server.module.start_protocol_servers',
                        lambda: calls.append(1) or ['tcp'])

    hook = mod._make_post_worker_init(4)
    for age in range(4):
        hook(_FakeWorker(age=age))
    assert calls == [], '多 worker 下不应尝试绑定协议端口'


def test_single_worker_starts_protocol_servers(monkeypatch):
    """单 worker 时正常启动协议服务器"""
    mod = _load_module(monkeypatch)
    calls = []
    monkeypatch.setattr('flask_server.module.start_protocol_servers',
                        lambda: calls.append(1) or ['tcp'])

    mod._make_post_worker_init(1)(_FakeWorker())
    assert calls == [1]


def test_protocol_server_bind_failure_does_not_crash_worker(monkeypatch):
    """单 worker 但端口被占（OSError）时只记录日志，worker 继续提供 HTTP 服务"""
    mod = _load_module(monkeypatch)

    def boom():
        raise OSError('address already in use')

    monkeypatch.setattr('flask_server.module.start_protocol_servers', boom)
    # 不应抛出（旧实现会把异常穿出钩子，导致 worker 启动失败）
    mod._make_post_worker_init(1)(_FakeWorker())


def test_child_exit_registered_only_with_multiprocess_metrics(monkeypatch):
    """PROMETHEUS_MULTIPROC_DIR 未配置时不注册 child_exit（避免无意义钩子）"""
    mod = _load_module(monkeypatch)
    monkeypatch.setattr(mod.config, 'metrics_multiprocess_dir', None)
    assert 'child_exit' not in mod._build_options()

    monkeypatch.setattr(mod.config, 'metrics_multiprocess_dir', '/tmp/prom_mp')
    options = mod._build_options()
    assert callable(options.get('child_exit'))


def test_child_exit_marks_process_dead(monkeypatch):
    """多进程指标：worker 退出时通知 prometheus_client 清理其指标文件"""
    mod = _load_module(monkeypatch)
    monkeypatch.setattr(mod.config, 'metrics_multiprocess_dir', '/tmp/prom_mp')
    marked = []
    import prometheus_client.multiprocess as mp
    monkeypatch.setattr(mp, 'mark_process_dead', lambda pid: marked.append(pid))

    mod._child_exit(None, _FakeWorker(pid=999))
    assert marked == [999]


def test_child_exit_swallows_errors(monkeypatch):
    """清理失败（如依赖版本差异）不应影响 worker 回收"""
    mod = _load_module(monkeypatch)
    monkeypatch.setattr(mod.config, 'metrics_multiprocess_dir', '/tmp/prom_mp')

    def boom(pid):
        raise RuntimeError('mark dead failed')

    import prometheus_client.multiprocess as mp
    monkeypatch.setattr(mp, 'mark_process_dead', boom)
    mod._child_exit(None, _FakeWorker(pid=999))   # 不抛异常


def test_win32_guard_exits(monkeypatch):
    """Windows 平台守卫：调用 _check_platform 应退出并给出指引"""
    mod = _load_module(monkeypatch)
    monkeypatch.setattr(sys, 'platform', 'win32')
    with pytest.raises(SystemExit) as exc:
        mod._check_platform()
    assert exc.value.code == 1
