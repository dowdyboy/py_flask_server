#!/usr/bin/env python
"""gunicorn 多 worker 冒烟测试（Linux CI 用；gunicorn 不支持 Windows，本机自动跳过）

覆盖两件事——都是"只有真实多进程部署才暴露"的问题，单测覆盖不到：

  A. `WORKER_NUM=2` + `TCP_ENABLED=true`
     协议服务器必须**不被启动**（否则 worker 2..N 端口冲突 → 钩子抛异常 →
     worker 退出 → master 重拉 → 死循环），同时两个 worker 都要活着、HTTP 可用。

  B. `WORKER_NUM=1` + `TCP_ENABLED=true`
     单 worker 下协议服务器必须正常监听（证明 A 的修复没有把功能一起关掉）。

用法：
    pip install gunicorn
    python scripts/smoke_gunicorn.py            # 退出码 0 表示通过
"""

import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRY = os.path.join(PROJECT_ROOT, 'wsgi_gunicorn.py')

# 各阶段最长等待时间（秒）
READY_TIMEOUT = 40
STOP_TIMEOUT = 25


def _free_port():
    """申请一个空闲端口（bind 后立即释放，存在极小的竞争窗口，CI 场景可接受）"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _http_ok(url, timeout=2):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _tcp_open(port, timeout=1):
    """端口是否可连接（协议服务器是否已监听）"""
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=timeout):
            return True
    except OSError:
        return False


def _wait(predicate, timeout, interval=0.2):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _child_env(http_port, tcp_port, workers, tcp_enabled):
    """构造干净的子进程环境（覆盖本地 .env，避免连到开发者的真实 MySQL/Redis）"""
    env = dict(os.environ)
    env.update({
        'APP_ENV': 'production',
        'DEBUG': 'false',
        'SERVER_HOST': '127.0.0.1',
        'SERVER_PORT': str(http_port),
        'WORKER_NUM': str(workers),
        'SECRET_KEY': 'smoke-test-only',
        'SQLALCHEMY_URI': '',
        'REDIS_URL': '',
        'SQLITE_DB_PATH': '',
        'LOG_FILE_PATH': '',
        'AUTH_ENABLED': 'false',
        'RATE_LIMIT_ENABLED': 'false',
        'METRICS_ENABLED': 'true',
        'TCP_ENABLED': 'true' if tcp_enabled else 'false',
        'TCP_HOST': '127.0.0.1',
        'TCP_PORT': str(tcp_port),
        'UDP_ENABLED': 'false',
        'PYTHONUNBUFFERED': '1',
    })
    # 测试进程里可能带着 pytest 的 addopts 等，清掉避免影响子进程
    env.pop('PYTEST_ADDOPTS', None)
    return env


def _run_phase(name, workers, expect_tcp_open):
    """启动一次 gunicorn，校验 worker 存活/协议端口状态，SIGTERM 后校验退出码"""
    http_port = _free_port()
    tcp_port = _free_port()
    env = _child_env(http_port, tcp_port, workers, tcp_enabled=True)

    out_file = tempfile.NamedTemporaryFile(prefix=f'gunicorn-smoke-{workers}-',
                                           suffix='.log', delete=False, mode='wb')
    out_path = out_file.name
    proc = subprocess.Popen(
        [sys.executable, ENTRY],
        cwd=PROJECT_ROOT, env=env,
        stdout=out_file, stderr=subprocess.STDOUT,
        start_new_session=True,   # 独立进程组：便于整组清理（master + workers）
    )
    try:
        healthy = _wait(lambda: _http_ok(f'http://127.0.0.1:{http_port}/api/v1/healthz'),
                        READY_TIMEOUT)
        output = _read(out_path)
        assert healthy, f'[{name}] HTTP 未就绪（{READY_TIMEOUT}s），输出:\n{output}'

        booted = output.count('Booting worker with pid')
        assert booted >= workers, \
            f'[{name}] 只启动了 {booted}/{workers} 个 worker，输出:\n{output}'
        for bad in ('Exception in worker process', 'address already in use', 'Worker failed to boot'):
            assert bad not in output, f'[{name}] 出现 "{bad}"：\n{output}'

        # 协议端口状态：多 worker 必须"未启动"，单 worker 必须"已启动"
        started = _wait(lambda: _tcp_open(tcp_port), 3, interval=0.1)
        if expect_tcp_open:
            assert started, f'[{name}] 单 worker 下 TCP 端口 {tcp_port} 未监听：\n{output}'
        else:
            assert not started, (
                f'[{name}] 多 worker 下不应启动协议服务器（否则端口冲突 → 崩溃循环），'
                f'但 {tcp_port} 可连接：\n{output}')

        # 优雅退出：SIGTERM → master 退出码 0
        proc.send_signal(signal.SIGTERM)
        try:
            code = proc.wait(timeout=STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise AssertionError(f'[{name}] SIGTERM 后 {STOP_TIMEOUT}s 未退出')
        assert code == 0, f'[{name}] 退出码 {code}（期望 0）：\n{_read(out_path)}'
        return f'[{name}] OK（workers={workers}, 协议端口{"已" if expect_tcp_open else "未"}监听）'
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
        out_file.close()
        try:
            os.unlink(out_path)
        except OSError:
            pass


def _read(path):
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read()
    except OSError:
        return ''


def main():
    if sys.platform == 'win32':
        print('[skip] gunicorn 不支持 Windows，跳过（请在 Linux CI 或 WSL 中运行）')
        return 0
    try:
        import gunicorn  # noqa: F401
    except ImportError:
        print('[skip] 未安装 gunicorn（pip install gunicorn）')
        return 0

    results = []
    # A. 多 worker：协议服务器必须被跳过，所有 worker 都要活着
    results.append(_run_phase('多 worker', workers=2, expect_tcp_open=False))
    # B. 单 worker：协议服务器正常监听
    results.append(_run_phase('单 worker', workers=1, expect_tcp_open=True))
    for line in results:
        print(line)
    print('gunicorn smoke: PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
