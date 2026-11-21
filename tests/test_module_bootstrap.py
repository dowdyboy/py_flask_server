"""启动自举路径测试：协议服务器半启动回滚 / 内嵌 worker 降级。

这两条路径对应容器"起不来/重启循环"的第一现场（第一轮 P1-6 修复），
之前没有任何用例覆盖（module/__init__.py 覆盖率 56%）。
"""

import pytest


def test_start_protocol_servers_rolls_back_tcp_when_udp_bind_fails(monkeypatch):
    """TCP 已起、UDP 绑定失败 → 必须先停掉 TCP 再把异常抛给入口（不留半启动状态）"""
    from flask_server import module as mod

    events = []

    class FakeTcp:
        def start(self):
            events.append('tcp.start')
            return True

        def stop(self):
            events.append('tcp.stop')

    class FakeUdp:
        def start(self):
            events.append('udp.start')
            raise OSError('address already in use')

        def stop(self):          # pragma: no cover - 本路径不应调用
            events.append('udp.stop')

    monkeypatch.setattr(mod, 'tcp_server', FakeTcp())
    monkeypatch.setattr(mod, 'udp_server', FakeUdp())
    monkeypatch.setattr(mod.config, 'tcp_enabled', True)
    monkeypatch.setattr(mod.config, 'udp_enabled', True)

    with pytest.raises(OSError, match='address already in use'):
        mod.start_protocol_servers()

    assert events == ['tcp.start', 'udp.start', 'tcp.stop'], \
        f'部分启动必须回滚已启动的 TCP: {events}'


def test_start_embedded_worker_degrades_when_queue_unavailable(monkeypatch):
    """TASK_WORKER_EMBEDDED=true + 队列不可用：返回 None 并告警，HTTP 服务照常启动"""
    from flask_server import module as mod
    from flask_server import task as task_pkg
    from flask_server.module import task_worker as tw
    from flask_server.module.task_queue import TaskQueueUnavailable

    monkeypatch.setattr(task_pkg, 'auto_import_tasks', lambda: ['demo.task'])
    monkeypatch.setattr(mod.config, 'task_worker_embedded', True)
    monkeypatch.setattr(mod.config, 'redis_url', 'redis://127.0.0.1:1/0')

    def _boom(self):
        raise TaskQueueUnavailable('redis down')

    monkeypatch.setattr(tw.TaskWorker, 'start', _boom)

    assert mod.start_embedded_task_worker() is None
