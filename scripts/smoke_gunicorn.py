#!/usr/bin/env python
"""gunicorn 多 worker 冒烟测试（Linux CI 用；gunicorn 不支持 Windows，本机自动跳过）

覆盖两件事——都是"只有真实多进程部署才暴露"的问题，单测覆盖不到：

  A. `WORKER_NUM=2` + `TCP_ENABLED=true`
     协议服务器必须**不被启动**（否则 worker 2..N 端口冲突 → 钩子抛异常 →
     worker 退出 → master 重拉 → 死循环），同时两个 worker 都要活着、HTTP 可用。

  B. `WORKER_NUM=1` + `TCP_ENABLED=true`
     单 worker 下协议服务器必须正常监听并能收发消息（证明 A 的修复没有把功能一起关掉）。

为了让"协议服务器是否启动"这件事可观测，脚本会临时往 `flask_server/handler/` 写一个
echo 处理器（用例结束删除）——否则脚手架默认没有处理器，端口永远不会被监听，
断言会退化成"永远通过"。

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
HANDLER_PATH = os.path.join(PROJECT_ROOT, 'flask_server', 'handler', '_smoke_tcp_echo.py')

# 临时 TCP echo 处理器（用完即删）：让"协议服务器是否真的监听"可被观测
HANDLER_SOURCE = '''\
"""冒烟测试临时处理器（scripts/smoke_gunicorn.py 生成，用完即删）"""

from flask_server.module import tcp_server


@tcp_server.on_message
def on_message(conn, data, addr):
    conn.sendall(b'echo: ' + data)
'''

# 各阶段最长等待时间（秒）
READY_TIMEOUT = 40
STOP_TIMEOUT = 25
LOG_TAIL = 4000


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


def _tcp_echo_roundtrip(port, timeout=5):
    """真实收发一轮：发 `ping\\n`，期望收到 `echo: ping`"""
    with socket.create_connection(('127.0.0.1', port), timeout=timeout) as sock:
        sock.sendall(b'ping\n')
        return sock.recv(1024)


def _wait(predicate, timeout, interval=0.2):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _tail(text):
    """日志太长会撑爆 CI 注解/输出，只回显尾部"""
    text = text or ''
    return text[-LOG_TAIL:]


def _install_handler():
    """写入临时 handler 模块；返回是否由本脚本创建（避免覆盖用户已有文件）"""
    if os.path.exists(HANDLER_PATH):
        return False
    os.makedirs(os.path.dirname(HANDLER_PATH), exist_ok=True)
    with open(HANDLER_PATH, 'w', encoding='utf-8', newline='\n') as f:
        f.write(HANDLER_SOURCE)
    return True


def _remove_handler(created):
    if created:
        try:
            os.unlink(HANDLER_PATH)
        except OSError:
            pass


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

    print(f'[{name}] starting gunicorn: workers={workers} http={http_port} tcp={tcp_port}',
          flush=True)
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
        print(f'[{name}] http_ready={healthy}', flush=True)
        assert healthy, f'[{name}] HTTP 未就绪（{READY_TIMEOUT}s），输出尾部:\n{_tail(output)}'

        booted = output.count('Booting worker with pid')
        print(f'[{name}] workers_booted={booted}', flush=True)
        assert booted >= workers, \
            f'[{name}] 只启动了 {booted}/{workers} 个 worker，输出尾部:\n{_tail(output)}'
        for bad in ('Exception in worker process', 'address already in use', 'Worker failed to boot'):
            assert bad not in output, f'[{name}] 出现 "{bad}"，输出尾部:\n{_tail(output)}'

        # 协议端口状态：多 worker 必须"未启动"，单 worker 必须"已启动"
        started = _wait(lambda: _tcp_open(tcp_port), 3, interval=0.1)
        print(f'[{name}] tcp_listening={started} (expected {expect_tcp_open})', flush=True)
        if expect_tcp_open:
            assert started, (
                f'[{name}] 单 worker 下 TCP 端口 {tcp_port} 未监听（协议服务器没启动？），'
                f'输出尾部:\n{_tail(output)}')
            reply = _tcp_echo_roundtrip(tcp_port)
            assert reply == b'echo: ping', f'[{name}] TCP 回显异常：{reply!r}'
            print(f'[{name}] tcp_echo={reply!r}', flush=True)
        else:
            assert not started, (
                f'[{name}] 多 worker 下不应启动协议服务器（否则端口冲突 → 崩溃循环），'
                f'但 {tcp_port} 可连接，输出尾部:\n{_tail(output)}')

        # 优雅退出：SIGTERM → master 退出码 0
        proc.send_signal(signal.SIGTERM)
        try:
            code = proc.wait(timeout=STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise AssertionError(f'[{name}] SIGTERM 后 {STOP_TIMEOUT}s 未退出')
        print(f'[{name}] exit_code={code}', flush=True)
        assert code == 0, f'[{name}] 退出码 {code}（期望 0），输出尾部:\n{_tail(_read(out_path))}'
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

    created = _install_handler()
    try:
        results = []
        # A. 多 worker：协议服务器必须被跳过，所有 worker 都要活着
        results.append(_run_phase('多 worker', workers=2, expect_tcp_open=False))
        # B. 单 worker：协议服务器正常监听并能回显
        results.append(_run_phase('单 worker', workers=1, expect_tcp_open=True))
    finally:
        _remove_handler(created)

    for line in results:
        print(line)
    print('gunicorn smoke: PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
