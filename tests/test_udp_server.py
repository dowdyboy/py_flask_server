"""UDP 协议服务器集成测试（真实 loopback 数据报）"""

import logging
import socket
import time

import pytest

from flask_server.module.udp_server import UdpServer


class _CaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _wait_until(cond, timeout=3.0, message='condition not met'):
    """轮询等待条件成立；超时抛出带说明的 AssertionError（避免裸 assert 无上下文）"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    raise AssertionError(f'{message} (timeout after {timeout}s)')


def _start_echo_server(reply=True):
    """启动 UDP 服务器（端口 0 自动分配），返回 (server, port, records)"""
    server = UdpServer(host='127.0.0.1', port=0)
    records = {'messages': []}

    @server.on_message
    def on_message(data, addr):
        records['messages'].append((data, addr))
        if reply:
            return b'echo: ' + data
        return None

    assert server.start() is True
    return server, server.bound_address[1], records


def _udp_client(port, timeout=3):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    return sock


def test_datagram_echo():
    """处理器返回 bytes → 自动回发到来源地址"""
    server, port, records = _start_echo_server()
    try:
        sock = _udp_client(port)
        try:
            sock.sendto(b'hello', ('127.0.0.1', port))
            data, _ = sock.recvfrom(1024)
            assert data == b'echo: hello'
        finally:
            sock.close()
    finally:
        server.stop()


def test_no_reply_when_return_none():
    """处理器返回 None → 不回发"""
    server, port, records = _start_echo_server(reply=False)
    try:
        sock = _udp_client(port, timeout=0.3)
        try:
            sock.sendto(b'hello', ('127.0.0.1', port))
            with pytest.raises(socket.timeout):
                sock.recvfrom(1024)
        finally:
            sock.close()
    finally:
        server.stop()


def test_reply_to_correct_address():
    """多个来源地址：回发到各自来源"""
    server, port, records = _start_echo_server()
    try:
        sock_a = _udp_client(port)
        sock_b = _udp_client(port)
        try:
            sock_a.sendto(b'A', ('127.0.0.1', port))
            sock_b.sendto(b'B', ('127.0.0.1', port))
            data, _ = sock_a.recvfrom(1024)
            assert data == b'echo: A'
            data, _ = sock_b.recvfrom(1024)
            assert data == b'echo: B'
        finally:
            sock_a.close()
            sock_b.close()
    finally:
        server.stop()


def test_multiple_datagrams():
    """同一来源多条数据报分别回调与回发"""
    server, port, records = _start_echo_server()
    try:
        sock = _udp_client(port)
        try:
            for i in range(5):
                sock.sendto(f'm{i}'.encode(), ('127.0.0.1', port))
            seen = set()
            while len(seen) < 5:
                data, _ = sock.recvfrom(1024)
                seen.add(data)
            assert seen == {f'echo: m{i}'.encode() for i in range(5)}
        finally:
            sock.close()
    finally:
        server.stop()
    assert len(records['messages']) == 5


def test_send_active():
    """udp_server.send 主动发送（处理器返回 None 时使用）"""
    server = UdpServer(host='127.0.0.1', port=0)
    records = []

    @server.on_message
    def on_message(data, addr):
        records.append(data)
        if data == b'ping':
            server.send(b'pong', addr)
        return None

    assert server.start() is True
    port = server.bound_address[1]
    try:
        sock = _udp_client(port)
        try:
            sock.sendto(b'ping', ('127.0.0.1', port))
            data, _ = sock.recvfrom(1024)
            assert data == b'pong'
        finally:
            sock.close()
    finally:
        server.stop()
    assert records == [b'ping']


def test_send_before_start_warns():
    """服务器未启动时 send 返回 False（不抛异常）"""
    server = UdpServer(host='127.0.0.1', port=0)
    assert server.send(b'x', ('127.0.0.1', 1)) is False


def test_max_message_length_zero_falls_back():
    """直接构造时 max_message_length ≤0 → 回退默认（防 recvfrom(0) 收到空数据报）"""
    server = UdpServer(host='127.0.0.1', port=0, max_message_length=0)
    assert server.max_message_length == 65536


def test_handler_error_calls_on_error():
    """处理器异常：on_error 钩子被调用，不回发也不中断服务器"""
    server = UdpServer(host='127.0.0.1', port=0)
    errors = []

    @server.on_message
    def on_message(data, addr):
        raise ValueError('boom')

    @server.on_error
    def on_error(e, *args):
        errors.append(e)

    assert server.start() is True
    port = server.bound_address[1]
    try:
        sock = _udp_client(port)
        try:
            sock.sendto(b'x', ('127.0.0.1', port))
            assert _wait_until(lambda: len(errors) == 1)
        finally:
            sock.close()
    finally:
        server.stop()
    assert isinstance(errors[0], ValueError)


def test_non_bytes_return_warns():
    """on_message 返回非 bytes（如 str）：告警提示且不回发（常见类型错误易排查）"""
    server = UdpServer(host='127.0.0.1', port=0)
    handler = _CaptureHandler()
    logging.getLogger('flask_server').addHandler(handler)
    try:
        @server.on_message
        def on_message(data, addr):
            return 'echo: ' + data.decode()   # 错误返回类型：str 而非 bytes

        assert server.start() is True
        port = server.bound_address[1]
        try:
            sock = _udp_client(port, timeout=0.3)
            try:
                sock.sendto(b'hello', ('127.0.0.1', port))
                with pytest.raises(socket.timeout):
                    sock.recvfrom(1024)   # 不回发
            finally:
                sock.close()
        finally:
            server.stop()
        # 告警在处理器线程中写入：轮询日志捕获，不依赖固定的 socket 等待时长
        _wait_until(
            lambda: any('returned str' in r.getMessage() for r in handler.records),
            message='非 bytes 返回值未告警',
        )
    finally:
        logging.getLogger('flask_server').removeHandler(handler)


def test_concurrency_limit_drops_datagrams():
    """UDP 并发上限：槽位被占用时到达的数据报被丢弃（不回调）"""
    import threading
    server = UdpServer(host='127.0.0.1', port=0, max_concurrency=1)
    slow_started = threading.Event()
    release_slow = threading.Event()
    slow_finished = threading.Event()
    records = []

    @server.on_message
    def on_message(data, addr):
        records.append(data)
        if data == b'slow':
            slow_started.set()
            release_slow.wait(3)   # 占用并发槽位，直到测试显式放行
            slow_finished.set()
        return None

    def _slot_free():
        """槽位是否已释放（直接探测信号量，避免 sleep 猜时序）"""
        semaphore = server._semaphore
        if semaphore is None:
            return True
        if semaphore.acquire(blocking=False):
            semaphore.release()
            return True
        return False

    assert server.start() is True
    port = server.bound_address[1]
    try:
        sock = _udp_client(port)
        try:
            sock.sendto(b'slow', ('127.0.0.1', port))
            assert slow_started.wait(3), '慢处理器未启动，无法制造并发占满场景'

            # 槽位被 slow 占满时 fast 到达：服务器必须在读取该数据报时拒绝它。
            # 拒绝会触发一次告警（新 server 的冷却时间戳从 0 变为当前时间），
            # 以此作为"fast 已被服务器处理并丢弃"的确定性信号；不 sleep 猜时序。
            sock.sendto(b'fast', ('127.0.0.1', port))
            _wait_until(lambda: server._last_reject_warn_ts > 0,
                        message='fast 数据报未被并发上限拒绝')
            assert records == [b'slow'], f'被拒绝的数据报不应回调处理器: {records}'

            release_slow.set()
            assert slow_finished.wait(3), '慢处理器未结束'
            _wait_until(_slot_free, message='并发槽位未释放')
            sock.sendto(b'after', ('127.0.0.1', port))
            _wait_until(lambda: records == [b'slow', b'after'],
                        message=f'释放槽位后数据报未被处理: {records}')
        finally:
            sock.close()
    finally:
        release_slow.set()
        server.stop()
    assert records == [b'slow', b'after']


# ---------------- 优雅关闭：等在途 handler ----------------

def test_stop_waits_for_in_flight_datagram_handler():
    """stop() 必须等在途 on_message 处理完（UDP 没有重投，丢一条就是永久丢）"""
    import threading
    server = UdpServer(host='127.0.0.1', port=0, shutdown_timeout=3)
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    @server.on_message
    def on_message(data, addr):
        started.set()
        release.wait(5)
        finished.set()
        return None

    assert server.start() is True
    port = server.bound_address[1]
    sock = _udp_client(port)
    try:
        sock.sendto(b'x', ('127.0.0.1', port))
        assert started.wait(3), 'handler 未启动'
        stopper = threading.Thread(target=server.stop, daemon=True)
        stopper.start()
        stopper.join(0.3)
        assert stopper.is_alive(), 'stop() 未等在途 handler 结束就返回了'
        release.set()
        stopper.join(3)
        assert not stopper.is_alive()
        assert finished.is_set()
    finally:
        release.set()
        sock.close()
        server.stop()

