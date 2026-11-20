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


def _wait_until(cond, timeout=3.0, message='condition not met'):
    """轮询等待条件成立；超时抛出带说明的 AssertionError（避免裸 assert 无上下文）"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    raise AssertionError(f'{message} (timeout after {timeout}s)')


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
        # 直接以两块数据喂解析器：确定性覆盖"同一帧跨两次 recv"，不靠 sleep 制造分包
        frame = server._read_line_frame(_FakeConn([b'he', b'llo\n']), bytearray())
        assert frame == b'hello'
        sock = _connect(port)
        try:
            sock.sendall(b'he')
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
            # 等 on_error 钩子真正触发后再发下一条：不 sleep 猜服务器线程进度
            _wait_until(lambda: len(records['errors']) == 1,
                        message='处理器异常未触发 on_error 钩子')
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
        # 两块分别 recv 仍拼成同一帧（确定性，无需 sleep 制造分包）
        assert server._read_fixed_frame(_FakeConn([b'ab', b'cd']), bytearray()) == b'abcd'
        sock = _connect(port)
        try:
            sock.sendall(b'ab')
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


class _FakeConn:
    """按块喂数据的伪连接（单测 _read_head_tail_frame 的跨块解析）"""

    def __init__(self, chunks):
        self._chunks = iter(chunks)
        self.recv_calls = 0

    def recv(self, n):
        self.recv_calls += 1
        return next(self._chunks, b'')


def _head_tail_parser(head=b'\xaa', tail=b'\xbb', **kwargs):
    """构造不启动的 head_tail 服务器（单测直接调用帧解析器）"""
    params = dict(host='127.0.0.1', port=0, framing='head_tail',
                  frame_head=head, frame_tail=tail)
    params.update(kwargs)
    return TcpServer(**params)


def _drain_frames(server, chunks):
    """把 chunks 依次喂给 _read_head_tail_frame（None 结束），返回 (帧列表, state, conn)"""
    conn = _FakeConn(chunks)
    buffer = bytearray()
    state = {'resyncs': 0}
    frames = []
    while True:
        data = server._read_head_tail_frame(conn, buffer, state)
        if data is None:
            break
        frames.append(data)
    return frames, state, conn


def test_head_tail_frame_chunking_independent():
    """回归（P0）：同一字节流按 1/2/3 包或逐字节投递，切出的帧必须完全一致。

    修复前：帧头后尚未见到帧尾时，代码按"更靠后的帧头"重同步，把负载内合法的
    帧头字节当成新帧起点——整帧一次到达切出 `a<HEAD>b`，分成 2/3 包或逐字节到达
    却丢掉 `a<HEAD>` 只切出 `b`。现在帧 = 首个帧头 → 其后首个帧尾。
    """
    head, tail = b'\xaa\x55', b'\xbb'
    stream = head + b'a' + head + b'b' + tail          # 一帧，负载 = b'a' + HEAD + b'b'
    expected = [b'a' + head + b'b']
    chunkings = {
        '1 recv': [stream],
        '2 recvs': [stream[:5], stream[5:]],           # HEAD+a+HEAD | b+TAIL
        '3 recvs': [stream[:3], stream[3:5], stream[5:]],   # HEAD+a | HEAD | b+TAIL
        'byte-by-byte': [stream[i:i + 1] for i in range(len(stream))],
    }
    results = {}
    for name, chunks in chunkings.items():
        frames, state, _ = _drain_frames(_head_tail_parser(head, tail), chunks)
        results[name] = frames
        assert frames == expected, f'{name} 投递切出的帧不一致: {frames!r}'
        assert state['resyncs'] == 0                   # 合法流不消耗重同步额度
    assert len({tuple(f) for f in results.values()}) == 1


def test_head_tail_same_stream_different_packets_on_socket():
    """回归（P0，真实连接）：同一字节流按 1/2/3 包发送，回调帧必须一致"""
    head, tail = b'\xaa\x55', b'\xbb'
    stream = head + b'a' + head + b'b' + tail
    expected = b'a' + head + b'b'
    patterns = [
        [stream],                                      # 一包
        [stream[:5], stream[5:]],                      # 包边界落在"内层帧头之后"，旧实现会丢帧
        [stream[:3], stream[3:5], stream[5:]],         # 三包（内层帧头单独成包）
    ]
    for chunks in patterns:
        server, port, records = _start_echo_server(
            framing='head_tail', frame_head=head, frame_tail=tail)
        try:
            sock = _connect(port)
            try:
                # 不 sleep：OS 可能合并包，但解析器对分包边界无关（上方纯解析器用例
                # 已确定性地覆盖 1/2/3 包边界）；这里只验证真实 socket 端到端不再丢帧
                for chunk in chunks:
                    sock.sendall(chunk)
                assert _recv_exact(sock, len(b'echo: ' + expected)) == b'echo: ' + expected
            finally:
                sock.close()
        finally:
            server.stop()
        assert records['messages'] == [expected], f'分包 {chunks!r} 的回调帧不一致'


def test_head_tail_head_straddles_chunks():
    """帧头本身被拆到两个 recv（\\xaa | \\x55...）仍能识别（含帧头前垃圾）"""
    head, tail = b'\xaa\x55', b'\xbb'
    frames, _, _ = _drain_frames(_head_tail_parser(head, tail), [b'\xaa', b'\x55hi\xbb'])
    assert frames == [b'hi']
    # 帧头前有垃圾且帧头跨块：垃圾丢弃、末尾半截帧头保留
    frames, _, _ = _drain_frames(_head_tail_parser(head, tail), [b'junk\xaa', b'\x55hi\xbb'])
    assert frames == [b'hi']


def test_head_tail_junk_before_head_discarded():
    """帧头前的垃圾字节丢弃；不完整的伪帧头（\\xaa 后不是 \\x55）按垃圾处理"""
    head, tail = b'\xaa\x55', b'\xbb'
    frames, _, _ = _drain_frames(
        _head_tail_parser(head, tail), [b'junkjunk', head + b'hi' + tail])
    assert frames == [b'hi']
    frames, _, _ = _drain_frames(
        _head_tail_parser(head, tail), [b'\xaajunk' + head + b'hi' + tail])
    assert frames == [b'hi']


def test_head_tail_payload_may_contain_head():
    """负载内出现帧头字节是合法的：300 轮"帧头+帧头+负载+帧尾"稳定切出完整负载"""
    server = _head_tail_parser(b'\xaa', b'\xbb')
    chunks = []
    for _ in range(300):
        chunks.extend([b'\xaa', b'\xaay', b'\xbb'])
    frames, state, _ = _drain_frames(server, chunks)
    assert len(frames) == 300
    assert all(f == b'\xaay' for f in frames)   # 内层帧头属于负载，不再被丢弃
    assert state['resyncs'] == 0                # 合法流不触发重同步


def test_head_tail_multiple_frames_in_one_buffer():
    """粘包：一次缓冲内的多帧按顺序切出（帧间垃圾丢弃、空负载帧保留）"""
    head, tail = b'\xaa\x55', b'\xbb'
    stream = head + b'a' + tail + b'junk' + head + b'b' + tail + head + tail
    frames, _, _ = _drain_frames(_head_tail_parser(head, tail), [stream])
    assert frames == [b'a', b'b', b'']


def test_head_tail_empty_payload_frame():
    """帧头紧邻帧尾：回调空负载帧（心跳等信号帧，与 line 模式空帧跳过不同）"""
    head, tail = b'\xaa\x55', b'\xbb'
    frames, _, _ = _drain_frames(
        _head_tail_parser(head, tail), [head + tail + head + b'x' + tail])
    assert frames == [b'', b'x']


def test_head_tail_max_length_payload_with_split_tail_ok():
    """负载恰好达到上限且帧尾被拆包：不得误判超长（帧尾前缀不算负载）"""
    head, tail = b'\xaa\x55', b'\x0d\x0a'
    payload = b'12345678'                  # 恰好 8 字节 = 上限
    frames, _, _ = _drain_frames(
        _head_tail_parser(head, tail, max_message_length=8),
        [head + payload + b'\x0d', b'\x0a'])
    assert frames == [payload]


def test_head_tail_oversized_without_tail_disconnects():
    """超长且无帧尾、且没有可重同步的帧头 → 立即按协议错误断开（不继续缓冲）"""
    server = _head_tail_parser(b'\xaa', b'\xbb', max_message_length=8)
    conn = _FakeConn([b'\xaa' + b'x' * 20, b'more-data-without-a-head'])
    buffer = bytearray()
    state = {'resyncs': 0}
    assert server._read_head_tail_frame(conn, buffer, state) is None
    assert conn.recv_calls == 1      # 判定即断开：没有为了等下一个帧头再多读一块
    assert state['resyncs'] == 0     # 无重同步点，不消耗额度


def test_head_tail_corrupt_frame_resyncs_to_next_head():
    """损坏帧（超长且缓冲中无帧尾）之后仍有帧头：按最后手段重同步，后续帧正常切出"""
    head, tail = b'\xaa', b'\xbb'
    server = _head_tail_parser(head, tail, max_message_length=8)
    # 首块：帧头 + 超长垃圾 + 新帧头 + 负载；帧尾在下一块才到达
    corrupt = head + b'x' * 20 + head + b'y'
    frames, state, _ = _drain_frames(server, [corrupt, tail])
    assert frames == [b'y']
    assert state['resyncs'] == 0     # 成功切帧后计数重置（"连续损坏"语义）


def test_head_tail_resync_cap_terminates():
    """最后手段重同步仍受 256 次上限约束：持续损坏的流必定终止（不无限持有连接）"""
    head, tail = b'\xaa', b'\xbb'
    server = _head_tail_parser(head, tail, max_message_length=8)
    # 每块 = 帧头 + 超长垃圾，块尾又是下一个帧头 → 每次超长判定都能重同步
    conn = _FakeConn([(head + b'x' * 20) * 600])
    buffer = bytearray()
    state = {'resyncs': 0}
    assert server._read_head_tail_frame(conn, buffer, state) is None
    assert state['resyncs'] > 256


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


def test_head_tail_garbage_before_head_discarded():
    """head_tail 模式：帧头前的垃圾字节（脏数据/半帧前缀）直接丢弃"""
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
        # 直接喂三块：帧头跨块、帧尾跨块 —— 确定性覆盖，不靠 sleep 分割 recv
        assert server._read_head_tail_frame(
            _FakeConn([b'\xaa', b'\x55hi\x0d', b'\x0a']),
            bytearray(), {'resyncs': 0},
        ) == b'hi'
        sock = _connect(port)
        try:
            sock.sendall(b'\xaa')
            sock.sendall(b'\x55hi\x0d')
            sock.sendall(b'\x0a')
            assert _recv_exact(sock, len(b'echo: hi')) == b'echo: hi'
        finally:
            sock.close()
    finally:
        server.stop()
    assert records['messages'] == [b'hi']


def test_head_tail_inner_head_stays_in_payload():
    """head_tail 模式：负载内的帧头字节属于负载（不再按"更靠后的帧头"重同步）

    旧实现：首帧未见到帧尾时把 `\\xaay` 当新帧起点，丢掉了 `abc\\xaa`；
    新语义：帧 = 首个帧头 → 其后首个帧尾，与分包边界无关。
    """
    server, port, records = _start_echo_server(
        framing='head_tail', frame_head=b'\xaa', frame_tail=b'\xbb')
    try:
        # 直接按三块喂解析器：负载内帧头、帧尾跨块同时覆盖，不 sleep
        assert server._read_head_tail_frame(
            _FakeConn([b'\xaaabc', b'\xaay', b'\xbb']),
            bytearray(), {'resyncs': 0},
        ) == b'abc\xaay'
        sock = _connect(port)
        try:
            sock.sendall(b'\xaaabc')          # 帧头 + 负载开头
            sock.sendall(b'\xaay')            # 负载内的帧头字节（旧实现会在此重同步）
            sock.sendall(b'\xbb')             # 帧尾到达
            expected = b'echo: abc\xaay'
            assert _recv_exact(sock, len(expected)) == expected
        finally:
            sock.close()
    finally:
        server.stop()
    assert records['messages'] == [b'abc\xaay']


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
    """head_tail 模式：帧尾缺失、负载超限且无可重同步帧头 → 协议错误断开连接"""
    server, port, records = _start_head_tail_server(max_message_length=8)
    try:
        sock = _connect(port)
        try:
            sock.sendall(b'\xaa\x55xxxxxxxxxx')   # 无帧尾且超限（缓冲中再无帧头）
            sock.settimeout(5)
            received = b''
            while True:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                received += chunk
            assert received == b''   # 未回显任何消息：连接已被服务端关闭
        finally:
            sock.close()
    finally:
        server.stop()
    assert records['messages'] == []
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


def test_head_tail_corrupt_stream_disconnects_on_socket():
    """head_tail 模式：持续"帧头+超长垃圾"的损坏流 → 服务端主动断开（防无限持有）

    每条消息都是"帧头 + 超长无帧尾"，块尾再留一个帧头：损坏判定必然触发，随后或按
    "最后手段"重同步累计到上限，或因缓冲中已无帧头直接断开——两条路径都必须断开。
    """
    server, port, records = _start_echo_server(
        framing='head_tail', frame_head=b'\xaa', frame_tail=b'\xbb', max_message_length=8)
    unit = b'\xaa' + b'x' * 10 + b'\xaa'   # 超长无帧尾，块尾留有下一个帧头
    try:
        sock = _connect(port)
        try:
            for _ in range(600):
                try:
                    sock.sendall(unit)
                except OSError:
                    break   # 服务端已断开
                if records['disconnect']:
                    break
            assert _wait_until(lambda: len(records['disconnect']) == 1,
                               message='损坏流未断开连接')
            sock.settimeout(5)
            try:
                data = sock.recv(1024)
            except OSError:
                data = b''   # 服务端关闭时可能以 RST 通知（Windows: WinError 10053）
            assert data == b''
        finally:
            sock.close()
    finally:
        server.stop()
    assert records['messages'] == []


# ---------------- 空闲连接超时（慢连接耗尽 DoS 防护） ----------------


def test_idle_connection_closed_after_timeout():
    """只连不发：超过 TCP_IDLE_TIMEOUT 无数据 → 服务端主动断开并释放槽位"""
    server, port, records = _start_echo_server(idle_timeout=0.3)
    try:
        sock = _connect(port)
        try:
            sock.settimeout(5)
            assert sock.recv(1024) == b''   # 服务端关闭 → recv 返回空
        finally:
            sock.close()
    finally:
        server.stop()
    assert _wait_until(lambda: len(records['disconnect']) == 1)


def test_active_connection_not_killed_by_idle_timeout():
    """有空闲超时也不应误杀正常连接（消息间隔短于超时即可持续交互）"""
    server, port, records = _start_echo_server(idle_timeout=2)
    try:
        sock = _connect(port)
        try:
            for i in range(3):
                sock.sendall(b'ping\n')
                assert _recv_exact(sock, len(b'echo: ping')) == b'echo: ping'
                time.sleep(0.2)
        finally:
            sock.close()
    finally:
        server.stop()
    assert records['messages'] == [b'ping'] * 3


def test_idle_timeout_disabled_keeps_connection_open():
    """idle_timeout<=0 表示不设超时：连接保持，不产生超时断开"""
    server, port, records = _start_echo_server(idle_timeout=0)
    try:
        sock = _connect(port)
        try:
            # 真实时间断言：空闲超过一个"若启用就会断开"的时长后连接仍可用
            time.sleep(0.3)
            sock.sendall(b'still-here\n')
            assert _recv_exact(sock, len(b'echo: still-here')) == b'echo: still-here'
        finally:
            sock.close()
    finally:
        server.stop()
    assert records['messages'] == [b'still-here']


def test_idle_timeout_applies_to_raw_mode():
    """raw 模式同样受空闲超时保护（recv 阻塞分支）"""
    server, port, records = _start_echo_server(framing='raw', idle_timeout=0.3)
    try:
        sock = _connect(port)
        try:
            sock.settimeout(5)
            assert sock.recv(1024) == b''
        finally:
            sock.close()
    finally:
        server.stop()
    assert _wait_until(lambda: len(records['disconnect']) == 1)


def test_idle_timeout_defaults_from_config(monkeypatch):
    """未显式传 idle_timeout 时取配置 TCP_IDLE_TIMEOUT"""
    from flask_server import config
    from flask_server.module.tcp_server import TcpServer

    monkeypatch.setattr(config, 'tcp_idle_timeout', 42)
    server = TcpServer(host='127.0.0.1', port=0)
    assert server.idle_timeout == 42
    monkeypatch.setattr(config, 'tcp_idle_timeout', 0)
    assert TcpServer(host='127.0.0.1', port=0).idle_timeout == 0
