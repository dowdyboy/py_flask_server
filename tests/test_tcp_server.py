"""TCP 协议服务器集成测试（真实 loopback 连接）"""

import logging
import socket
import time

import pytest

from flask_server.module.tcp_server import TcpServer


class _CaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _wait_until(cond, timeout=3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return False


def _start_echo_server(**kwargs):
    """启动带回显处理器的 TCP 服务器（端口 0 自动分配），返回 (server, port, records)"""
    server = TcpServer(host='127.0.0.1', port=0, **kwargs)
    records = {'connect': [], 'messages': [], 'disconnect': []}

    @server.on_connect
    def on_connect(conn, addr):
        records['connect'].append(addr)

    @server.on_message
    def on_message(conn, data, addr):
        records['messages'].append(data)
        conn.sendall(b'echo: ' + data)

    @server.on_disconnect
    def on_disconnect(conn, addr):
        records['disconnect'].append(addr)

    assert server.start() is True
    return server, server.bound_address[1], records


def _connect(port, timeout=5):
    return socket.create_connection(('127.0.0.1', port), timeout=timeout)


def _recv_exact(sock, n, timeout=5):
    sock.settimeout(timeout)
    buf = b''
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            break
        buf += chunk
    return buf


def test_line_echo_multiple_messages():
    server, port, records = _start_echo_server()
    try:
        sock = _connect(port)
        try:
            sock.sendall(b'hello\n')
            assert _recv_exact(sock, len(b'echo: hello')) == b'echo: hello'
            sock.sendall(b'world\n')
            assert _recv_exact(sock, len(b'echo: world')) == b'echo: world'
        finally:
            sock.close()
    finally:
        server.stop()
    assert records['messages'] == [b'hello', b'world']
    assert len(records['connect']) == 1
    assert _wait_until(lambda: len(records['disconnect']) == 1)


def test_split_packet_merged():
    """拆包：一条消息分多次发送仍按一帧处理"""
    server, port, records = _start_echo_server()
    try:
        sock = _connect(port)
        try:
            sock.sendall(b'he')
            time.sleep(0.05)
            sock.sendall(b'llo\n')
            assert _recv_exact(sock, len(b'echo: hello')) == b'echo: hello'
        finally:
            sock.close()
    finally:
        server.stop()
    assert records['messages'] == [b'hello']


def test_multiple_frames_in_one_packet():
    """粘包：一包多帧分别回调"""
    server, port, records = _start_echo_server()
    try:
        sock = _connect(port)
        try:
            sock.sendall(b'a\nb\n')
            assert _recv_exact(sock, len(b'echo: a')) == b'echo: a'
            assert _recv_exact(sock, len(b'echo: b')) == b'echo: b'
        finally:
            sock.close()
    finally:
        server.stop()
    assert records['messages'] == [b'a', b'b']


def test_empty_line_skipped():
    """纯空帧（紧邻分隔符）跳过，不回调"""
    server, port, records = _start_echo_server()
    try:
        sock = _connect(port)
        try:
            sock.sendall(b'\nhello\n')
            assert _recv_exact(sock, len(b'echo: hello')) == b'echo: hello'
        finally:
            sock.close()
    finally:
        server.stop()
    assert records['messages'] == [b'hello']


def test_crlf_separator():
    """自定义多字节分隔符 \\r\\n"""
    server, port, records = _start_echo_server(separator=b'\r\n')
    try:
        sock = _connect(port)
        try:
            sock.sendall(b'hi\r\n')
            assert _recv_exact(sock, len(b'echo: hi')) == b'echo: hi'
        finally:
            sock.close()
    finally:
        server.stop()
    assert records['messages'] == [b'hi']


def test_oversized_message_disconnects():
    """无分隔符且超长：视为协议错误，服务端断开连接"""
    server, port, records = _start_echo_server(max_message_length=8)
    try:
        sock = _connect(port)
        try:
            sock.sendall(b'x' * 100)
            sock.settimeout(5)
            received = b''
            while True:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                received += chunk
        finally:
            sock.close()
    finally:
        server.stop()
    assert _wait_until(lambda: len(records['disconnect']) == 1)


def test_raw_mode_chunks():
    """raw 模式：recv 原始数据直接回调（可能一包合并，只断言内容）"""
    server, port, records = _start_echo_server(framing='raw')
    try:
        sock = _connect(port)
        try:
            sock.sendall(b'abc')
            sock.sendall(b'def')
            assert _wait_until(lambda: b''.join(records['messages']) == b'abcdef')
        finally:
            sock.close()
    finally:
        server.stop()
    assert _wait_until(lambda: len(records['disconnect']) == 1)


def test_handler_error_keeps_connection():
    """处理器抛异常：on_error 钩子被调用，连接不断开，后续消息正常处理"""
    server = TcpServer(host='127.0.0.1', port=0)
    records = {'errors': [], 'messages': []}

    @server.on_message
    def on_message(conn, data, addr):
        records['messages'].append(data)
        if data == b'boom':
            raise ValueError('boom')
        conn.sendall(b'echo: ' + data)

    @server.on_error
    def on_error(e, *args):
        records['errors'].append(e)

    assert server.start() is True
    port = server.bound_address[1]
    try:
        sock = _connect(port)
        try:
            sock.sendall(b'boom\n')
            time.sleep(0.1)
            sock.sendall(b'ok\n')
            assert _recv_exact(sock, len(b'echo: ok')) == b'echo: ok'
        finally:
            sock.close()
    finally:
        server.stop()
    assert records['messages'] == [b'boom', b'ok']
    assert len(records['errors']) == 1


def test_multiple_clients():
    """多客户端并发：各自独立连接与回显"""
    server, port, records = _start_echo_server()
    try:
        socks = [_connect(port), _connect(port)]
        try:
            for i, sock in enumerate(socks):
                msg = f'c{i}'.encode()
                sock.sendall(msg + b'\n')
                assert _recv_exact(sock, len(b'echo: ' + msg)) == b'echo: ' + msg
        finally:
            for sock in socks:
                sock.close()
    finally:
        server.stop()
    assert len(records['connect']) == 2
    assert _wait_until(lambda: len(records['disconnect']) == 2)


def test_stop_then_connect_refused():
    """停止后端口不再接受新连接"""
    server, port, records = _start_echo_server()
    server.stop()
    with pytest.raises(OSError):
        sock = _connect(port)
        sock.close()


# ---------------- fixed（固定长度）定界 ----------------

def test_fixed_frame_exact():
    """fixed 模式：恰好 frame_length 字节一帧"""
    server, port, records = _start_echo_server(framing='fixed', frame_length=4)
    try:
        sock = _connect(port)
        try:
            sock.sendall(b'abcd')
            assert _recv_exact(sock, len(b'echo: abcd')) == b'echo: abcd'
        finally:
            sock.close()
    finally:
        server.stop()
    assert records['messages'] == [b'abcd']


def test_fixed_split_packet():
    """fixed 模式：一帧分多次发送（拆包）"""
    server, port, records = _start_echo_server(framing='fixed', frame_length=4)
    try:
        sock = _connect(port)
        try:
            sock.sendall(b'ab')
            time.sleep(0.05)
            sock.sendall(b'cd')
            assert _recv_exact(sock, len(b'echo: abcd')) == b'echo: abcd'
        finally:
            sock.close()
    finally:
        server.stop()
    assert records['messages'] == [b'abcd']


def test_fixed_multiple_frames_and_leftover():
    """fixed 模式：一包多帧（粘包）+ 残帧跨包累积"""
    server, port, records = _start_echo_server(framing='fixed', frame_length=3)
    try:
        sock = _connect(port)
        try:
            sock.sendall(b'abcdefg')      # abc / def 两帧 + g 残帧
            sock.sendall(b'hi')           # ghi 第三帧
            assert _recv_exact(sock, len(b'echo: abc')) == b'echo: abc'
            assert _recv_exact(sock, len(b'echo: def')) == b'echo: def'
            assert _recv_exact(sock, len(b'echo: ghi')) == b'echo: ghi'
        finally:
            sock.close()
    finally:
        server.stop()
    assert records['messages'] == [b'abc', b'def', b'ghi']


def test_fixed_invalid_falls_back_line():
    """直接构造时 frame_length 非法（≤0）→ 回退 line 定界"""
    server = TcpServer(host='127.0.0.1', port=0, framing='fixed', frame_length=0)
    assert server.framing == 'line'


# ---------------- head_tail（帧头帧尾）定界 ----------------

def _start_head_tail_server(**kwargs):
    params = dict(frame_head=b'\xaa\x55', frame_tail=b'\x0d\x0a')
    params.update(kwargs)
    return _start_echo_server(framing='head_tail', **params)


def test_head_tail_strips_markers():
    """head_tail 模式：回调帧头帧尾之间的负载（剥离定界开销）"""
    server, port, records = _start_head_tail_server()
    try:
        sock = _connect(port)
        try:
            sock.sendall(b'\xaa\x55hello\x0d\x0a')
            assert _recv_exact(sock, len(b'echo: hello')) == b'echo: hello'
        finally:
            sock.close()
    finally:
        server.stop()
    assert records['messages'] == [b'hello']


def test_head_tail_garbage_resync():
    """head_tail 模式：帧头前的垃圾字节自动丢弃（重同步）"""
    server, port, records = _start_head_tail_server()
    try:
        sock = _connect(port)
        try:
            sock.sendall(b'junkjunk\xaa\x55hi\x0d\x0a')
            assert _recv_exact(sock, len(b'echo: hi')) == b'echo: hi'
        finally:
            sock.close()
    finally:
        server.stop()
    assert records['messages'] == [b'hi']


def test_head_tail_split_markers():
    """head_tail 模式：帧头/帧尾跨包到达（拆包）"""
    server, port, records = _start_head_tail_server()
    try:
        sock = _connect(port)
        try:
            sock.sendall(b'\xaa')
            time.sleep(0.05)
            sock.sendall(b'\x55hi\x0d')
            time.sleep(0.05)
            sock.sendall(b'\x0a')
            assert _recv_exact(sock, len(b'echo: hi')) == b'echo: hi'
        finally:
            sock.close()
    finally:
        server.stop()
    assert records['messages'] == [b'hi']


def test_head_tail_inner_head_resync():
    """head_tail 模式：首帧帧尾缺失（流中断），新帧头出现 → 以更靠后的帧头重同步"""
    server, port, records = _start_echo_server(
        framing='head_tail', frame_head=b'\xaa', frame_tail=b'\xbb')
    try:
        sock = _connect(port)
        try:
            # 第一帧 \xaa+abc 帧尾缺失，随后第二帧 \xaa+y+\xbb 到达
            sock.sendall(b'\xaaabc')          # 首帧无帧尾
            time.sleep(0.1)
            sock.sendall(b'\xaay')            # 新的帧头出现 → 重同步到它
            time.sleep(0.1)
            sock.sendall(b'\xbb')             # 帧尾到达
            assert _recv_exact(sock, len(b'echo: y')) == b'echo: y'
        finally:
            sock.close()
    finally:
        server.stop()
    assert records['messages'] == [b'y']


def test_head_tail_multiple_frames():
    """head_tail 模式：一包多帧（粘包）"""
    server, port, records = _start_head_tail_server()
    try:
        sock = _connect(port)
        try:
            sock.sendall(b'\xaa\x55a\x0d\x0a\xaa\x55b\x0d\x0a')
            assert _recv_exact(sock, len(b'echo: a')) == b'echo: a'
            assert _recv_exact(sock, len(b'echo: b')) == b'echo: b'
        finally:
            sock.close()
    finally:
        server.stop()
    assert records['messages'] == [b'a', b'b']


def test_head_tail_missing_tail_disconnects():
    """head_tail 模式：帧尾缺失且超限 → 协议错误断开连接"""
    server, port, records = _start_head_tail_server(max_message_length=8)
    try:
        sock = _connect(port)
        try:
            sock.sendall(b'\xaa\x55xxxxxxxxxx')   # 无帧尾且超限
            sock.settimeout(5)
            received = b''
            while True:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                received += chunk
        finally:
            sock.close()
    finally:
        server.stop()
    assert _wait_until(lambda: len(records['disconnect']) == 1)


def test_head_tail_missing_marker_falls_back_line():
    """直接构造时缺帧头/帧尾 → 回退 line 定界"""
    server = TcpServer(host='127.0.0.1', port=0, framing='head_tail', frame_head=b'\xaa')
    assert server.framing == 'line'


# ---------------- 并发上限 / 连接生命周期 / 硬上限 ----------------

def test_concurrency_limit_rejects_extra_connections():
    """TCP_MAX_CONNECTIONS：超限的新连接被直接拒绝（已建立的连接不受影响）"""
    server = TcpServer(host='127.0.0.1', port=0, max_connections=2)
    records = []

    @server.on_message
    def on_message(conn, data, addr):
        records.append(data)
        conn.sendall(b'echo: ' + data)

    assert server.start() is True
    port = server.bound_address[1]
    try:
        s1, s2 = _connect(port), _connect(port)
        try:
            for sock in (s1, s2):
                sock.sendall(b'ok\n')
                assert _recv_exact(sock, len(b'echo: ok')) == b'echo: ok'
            # 第三个连接：握手成功但服务端立即关闭（不触发 on_message/on_connect）
            s3 = _connect(port)
            s3.settimeout(5)
            try:
                data = s3.recv(1024)
            except OSError:
                data = b''
            assert data == b''
        finally:
            for sock in (s1, s2):
                sock.close()
    finally:
        server.stop()
    assert records == [b'ok', b'ok']


def test_stop_closes_active_connections():
    """stop() 关闭所有已建立的连接（热重启后无残留旧连接）"""
    server, port, records = _start_echo_server()
    sock = _connect(port)
    try:
        sock.sendall(b'hello\n')
        assert _recv_exact(sock, len(b'echo: hello')) == b'echo: hello'
        server.stop()
        sock.settimeout(5)
        try:
            data = sock.recv(1024)
        except OSError:
            data = b''
        assert data == b''   # 服务端已关闭连接
    finally:
        sock.close()
        server.stop()   # 幂等


def test_track_after_stop_rejected_and_closed():
    """stop 竞态窗口：连接登记晚于服务器停止 → 拒绝登记（调用方关闭连接），
    不会成为 close_all 的漏网之鱼（stop 先置 _server=None 再关连接，登记时检查即可闭合）"""
    server, port, records = _start_echo_server()
    server.stop()
    fake_conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        assert server._track_connection(fake_conn) is False
        assert fake_conn not in server._connections
    finally:
        fake_conn.close()


def test_head_tail_non_bytes_marker_falls_back_line():
    """直接构造时帧头/帧尾非 bytes（如 str）→ 回退 line 定界（防 find TypeError）"""
    server = TcpServer(host='127.0.0.1', port=0, framing='head_tail',
                       frame_head=b'\xaa', frame_tail='\x0d\x0a')
    assert server.framing == 'line'


def test_max_message_length_zero_falls_back():
    """直接构造时 max_message_length ≤0 → 回退默认（防 max=0 全部帧"超长"断开）"""
    server = TcpServer(host='127.0.0.1', port=0, max_message_length=0)
    assert server.max_message_length == 65536


def test_frame_parser_error_logged_and_disconnects(monkeypatch):
    """帧解析器意外异常：统一进 Logger（非 stderr 裸 traceback），连接优雅断开"""
    server, port, records = _start_echo_server()

    def boom(conn, buffer, state=None):
        raise RuntimeError('parser boom')

    monkeypatch.setattr(server, '_read_frame', boom)
    handler = _CaptureHandler()
    logging.getLogger('flask_server').addHandler(handler)
    try:
        sock = _connect(port)
        try:
            sock.sendall(b'hello\n')
            sock.settimeout(5)
            try:
                data = sock.recv(1024)
            except OSError:
                data = b''
            assert data == b''   # 解析异常后连接被断开
        finally:
            sock.close()
    finally:
        server.stop()
        logging.getLogger('flask_server').removeHandler(handler)
    assert any('frame read error' in r.getMessage() for r in handler.records)
    assert records['messages'] == []
    assert _wait_until(lambda: len(records['disconnect']) == 1)


def test_verify_request_slot_check():
    """槽位前置检查：并发槽位被占时 verify_request 返回 False（请求被拒、不 spawn 线程）"""
    server = TcpServer(host='127.0.0.1', port=0, max_connections=1)

    @server.on_message
    def on_message(conn, data, addr):
        pass

    inner = server._create_server()
    try:
        assert server._acquire_slot() is True    # 占用唯一槽位
        assert inner.verify_request(None, ('client', 1)) is False   # 超限被拒
        server._release_slot()
        assert inner.verify_request(None, ('client', 2)) is True    # 恢复（并占用）
        server._release_slot()
    finally:
        inner.server_close()   # 释放端口（未启动，仅关闭绑定）


class _FakeConn:
    """按块喂数据的伪连接（单测 _read_head_tail_frame 的跨块解析）"""

    def __init__(self, chunks):
        self._chunks = iter(chunks)

    def recv(self, n):
        return next(self._chunks, b'')


def test_head_tail_resync_counter_resets_on_frame():
    """重同步计数在成功切帧后重置（"连续未完成帧"语义）：
    300 轮"孤儿帧头+垃圾（重同步 1 次）→ 完整帧（重置计数）"，连接不被误断"""
    server = TcpServer(host='127.0.0.1', port=0, framing='head_tail',
                       frame_head=b'\xaa', frame_tail=b'\xbb')
    # 每轮三块：孤儿帧头 / 新帧头+负载 / 帧尾 —— 触发 1 次重同步 + 1 次成功切帧
    chunks = []
    for _ in range(300):
        chunks.extend([b'\xaa', b'\xaay', b'\xbb'])
    conn = _FakeConn(chunks)
    buffer = bytearray()
    state = {'resyncs': 0}
    frames = []
    while True:
        data = server._read_head_tail_frame(conn, buffer, state)
        if data is None:
            break
        frames.append(data)
    assert len(frames) == 300, f'修复前累计计数在第 257 轮断开，实际切出 {len(frames)} 帧'
    assert all(f == b'y' for f in frames)


def test_head_tail_resync_cap_still_disconnects_without_frames():
    """攻击模式（永不完成帧）：计数持续增长，256 上限照常断开（重置不影响攻击防护）"""
    server = TcpServer(host='127.0.0.1', port=0, framing='head_tail',
                       frame_head=b'\xaa', frame_tail=b'\xbb')
    conn = _FakeConn([b'\xaa\xaajunk' for _ in range(600)])
    buffer = bytearray()
    state = {'resyncs': 0}
    data = server._read_head_tail_frame(conn, buffer, state)
    assert data is None   # 重同步超限断开
    assert state['resyncs'] > 256


def test_oversized_frame_in_single_packet_disconnects():
    """line 模式：分隔符与超长帧同包到达也断开（硬上限，无 recv 块粒度窗口）"""
    server, port, records = _start_echo_server(max_message_length=8)
    try:
        sock = _connect(port)
        try:
            sock.sendall(b'1234567890\n')   # 10 字节 > 8，分隔符同包
            sock.settimeout(5)
            received = b''
            while True:
                try:
                    chunk = sock.recv(1024)
                except OSError:
                    break
                if not chunk:
                    break
                received += chunk
            assert received == b''   # 未回显任何消息
        finally:
            sock.close()
    finally:
        server.stop()
    assert records['messages'] == []
    assert _wait_until(lambda: len(records['disconnect']) == 1)


def test_head_tail_oversized_payload_disconnects():
    """head_tail 模式：帧尾与超长负载同包到达也断开（硬上限）"""
    server, port, records = _start_head_tail_server(max_message_length=8)
    try:
        sock = _connect(port)
        try:
            sock.sendall(b'\xaa\x551234567890\x0d\x0a')   # 负载 10 字节 > 8
            sock.settimeout(5)
            try:
                data = sock.recv(1024)
            except OSError:
                data = b''
            assert data == b''
        finally:
            sock.close()
    finally:
        server.stop()
    assert records['messages'] == []
    assert _wait_until(lambda: len(records['disconnect']) == 1)


def test_head_tail_resync_cap_disconnects():
    """head_tail 模式：反复"帧头+垃圾"重同步超上限 → 断开连接（防无限持有）"""
    server, port, records = _start_echo_server(
        framing='head_tail', frame_head=b'\xaa', frame_tail=b'\xbb')
    try:
        sock = _connect(port)
        try:
            for _ in range(600):
                try:
                    sock.sendall(b'\xaajunk')
                except OSError:
                    break   # 服务端已断开
                time.sleep(0.001)
            sock.settimeout(5)
            try:
                data = sock.recv(1024)
            except OSError:
                data = b''
            assert data == b''
        finally:
            sock.close()
    finally:
        server.stop()
    assert records['messages'] == []
    assert _wait_until(lambda: len(records['disconnect']) == 1)
