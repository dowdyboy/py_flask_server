"""TCP/UDP 协议服务器端到端测试（真实 loopback，四种定界与并发/超时防护）。"""

import logging
import os
import socket
import time

from common import Results, dump_exception

os.environ.update(__import__('common').base_env())

R = Results('ProtocolServers')


def _wait_until(cond, timeout=5.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(interval)
    return False


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


def _start_echo(**kwargs):
    from flask_server.module.tcp_server import TcpServer
    server = TcpServer(host='127.0.0.1', port=0, **kwargs)
    records = {'connect': [], 'messages': [], 'disconnect': [], 'errors': []}

    @server.on_connect
    def on_connect(conn, addr):
        records['connect'].append(addr)

    @server.on_message
    def on_message(conn, data, addr):
        records['messages'].append(data)
        if data == b'boom':
            raise ValueError('boom')
        conn.sendall(b'echo:' + data)

    @server.on_disconnect
    def on_disconnect(conn, addr):
        records['disconnect'].append(addr)

    @server.on_error
    def on_error(e, *args):
        records['errors'].append(e)

    R.eq('TCP 服务器启动', server.start(), True)
    return server, server.bound_address[1], records


def test_line_framing():
    server, port, records = _start_echo()
    try:
        sock = _connect(port)
        try:
            sock.sendall(b'hello\n')
            R.eq('line 单帧', _recv_exact(sock, 10), b'echo:hello')
            sock.sendall(b'a\nb\n')
            R.eq('line 粘包第一帧', _recv_exact(sock, 6), b'echo:a')
            R.eq('line 粘包第二帧', _recv_exact(sock, 6), b'echo:b')
            sock.sendall(b'he')
            time.sleep(0.05)
            sock.sendall(b'llo\n')
            R.eq('line 拆包', _recv_exact(sock, 10), b'echo:hello')
            sock.sendall(b'\n\nok\n')
            R.eq('line 空帧跳过', _recv_exact(sock, 7), b'echo:ok')
        finally:
            sock.close()
    finally:
        server.stop()
    R.eq('line 消息序列', records['messages'], [b'hello', b'a', b'b', b'hello', b'ok'])
    R.check('line on_connect 触发', len(records['connect']) == 1)
    R.check('line on_disconnect 触发', _wait_until(lambda: len(records['disconnect']) == 1))

    server, port, records = _start_echo(separator=b'\r\n')
    try:
        sock = _connect(port)
        try:
            sock.sendall(b'hi\r\n')
            R.eq('CRLF 分隔符', _recv_exact(sock, 7), b'echo:hi')
        finally:
            sock.close()
    finally:
        server.stop()


def test_line_oversized():
    server, port, records = _start_echo(max_message_length=8)
    try:
        sock = _connect(port)
        try:
            sock.sendall(b'x' * 100)
            sock.settimeout(5)
            data = sock.recv(1024)
            R.eq('line 超长断开连接', data, b'')
        finally:
            sock.close()
    finally:
        server.stop()
    R.check('line 超长记录 disconnect', _wait_until(lambda: len(records['disconnect']) == 1))


def test_raw_framing():
    server, port, records = _start_echo(framing='raw')
    try:
        sock = _connect(port)
        try:
            sock.sendall(b'abc')
            sock.sendall(b'def')
            R.check('raw 模式收到原始块', _wait_until(lambda: b''.join(records['messages']) == b'abcdef'))
        finally:
            sock.close()
    finally:
        server.stop()


def test_fixed_framing():
    server, port, records = _start_echo(framing='fixed', frame_length=3)
    try:
        sock = _connect(port)
        try:
            sock.sendall(b'abcdefg')
            R.eq('fixed 第一帧', _recv_exact(sock, 8), b'echo:abc')
            R.eq('fixed 第二帧', _recv_exact(sock, 8), b'echo:def')
            sock.sendall(b'hi')
            R.eq('fixed 跨包残帧', _recv_exact(sock, 8), b'echo:ghi')
        finally:
            sock.close()
    finally:
        server.stop()
    R.eq('fixed 消息序列', records['messages'], [b'abc', b'def', b'ghi'])


def test_head_tail_framing():
    head, tail = b'\xaa\x55', b'\x0d\x0a'
    server, port, records = _start_echo(framing='head_tail', frame_head=head, frame_tail=tail)
    try:
        sock = _connect(port)
        try:
            sock.sendall(b'junkjunk' + head + b'hello' + tail)
            R.eq('head_tail 剥离标记/丢弃前缀', _recv_exact(sock, 10), b'echo:hello')
            sock.sendall(head + b'a' + tail + head + b'b' + tail)
            R.eq('head_tail 粘包第一帧', _recv_exact(sock, 6), b'echo:a')
            R.eq('head_tail 粘包第二帧', _recv_exact(sock, 6), b'echo:b')
            sock.sendall(head + b'sp')
            time.sleep(0.05)
            sock.sendall(b'lit' + tail)
            R.eq('head_tail 标记拆包', _recv_exact(sock, 10), b'echo:split')
        finally:
            sock.close()
    finally:
        server.stop()
    R.eq('head_tail 消息序列', records['messages'], [b'hello', b'a', b'b', b'split'])

    # 文档语义：帧尾缺失时，若新帧头到达（且尚未看到帧尾）→ 重同步到更靠后的帧头；
    # 但若新帧头与帧尾同包到达，则按“首帧头+首帧尾”解析（负载可含帧头字节）。
    server, port, records = _start_echo(framing='head_tail', frame_head=b'\xaa', frame_tail=b'\xbb')
    try:
        sock = _connect(port)
        try:
            sock.sendall(b'\xaaabc')
            time.sleep(0.05)
            sock.sendall(b'\xaay\xbb')
            R.eq('head_tail 同包内层帧头按首帧头+首帧尾（文档语义）',
                 _recv_exact(sock, 10), b'echo:abc\xaay')
        finally:
            sock.close()
    finally:
        server.stop()
    R.eq('head_tail 同包消息序列（文档语义）', records['messages'], [b'abc\xaay'])

    # 同一场景但新帧头与帧尾分两包到达：现有实现可以正确重同步（说明 bug 与分包边界相关）
    server, port, records = _start_echo(framing='head_tail', frame_head=b'\xaa', frame_tail=b'\xbb')
    try:
        sock = _connect(port)
        try:
            sock.sendall(b'\xaaabc')
            time.sleep(0.05)
            sock.sendall(b'\xaay')
            time.sleep(0.05)
            sock.sendall(b'\xbb')
            R.eq('head_tail 分包新帧头重同步', _recv_exact(sock, 6), b'echo:y')
        finally:
            sock.close()
    finally:
        server.stop()
    R.eq('head_tail 分包重同步消息序列', records['messages'], [b'y'])

    # 帧尾缺失且超限断开
    server, port, records = _start_echo(framing='head_tail', frame_head=head, frame_tail=tail,
                                        max_message_length=8)
    try:
        sock = _connect(port)
        try:
            sock.sendall(head + b'x' * 20)
            sock.settimeout(5)
            R.eq('head_tail 超限断开', sock.recv(1024), b'')
        finally:
            sock.close()
    finally:
        server.stop()


def test_handler_error_keeps_connection():
    server, port, records = _start_echo()
    try:
        sock = _connect(port)
        try:
            sock.sendall(b'boom\n')
            time.sleep(0.1)
            sock.sendall(b'ok\n')
            R.eq('handler 异常后连接仍可用', _recv_exact(sock, 7), b'echo:ok')
        finally:
            sock.close()
    finally:
        server.stop()
    R.eq('on_error 钩子被调用', len(records['errors']), 1)
    R.check('on_error 异常类型', isinstance(records['errors'][0], ValueError))


def test_multiple_clients():
    server, port, records = _start_echo()
    socks = []
    try:
        for i in range(3):
            s = _connect(port)
            socks.append(s)
            s.sendall(f'c{i}\n'.encode())
            R.eq(f'多客户端 {i} 回显', _recv_exact(s, 7), f'echo:c{i}'.encode())
    finally:
        for s in socks:
            s.close()
        server.stop()
    R.check('多客户端 on_connect 次数', len(records['connect']) == 3)
    R.check('多客户端 on_disconnect 次数', _wait_until(lambda: len(records['disconnect']) == 3))


def test_connection_limits_and_idle():
    server, port, records = _start_echo(max_connections=2)
    s1 = s2 = None
    try:
        s1, s2 = _connect(port), _connect(port)
        for s in (s1, s2):
            s.sendall(b'ok\n')
            R.eq('并发上限内连接正常', _recv_exact(s, 7), b'echo:ok')
        s3 = _connect(port)
        s3.settimeout(5)
        try:
            data = s3.recv(1024)
        except OSError:
            data = b''
        s3.close()
        R.eq('并发超限连接被关闭', data, b'')
    finally:
        for s in (s1, s2):
            if s:
                s.close()
        server.stop()

    # 空闲超时
    server, port, records = _start_echo(idle_timeout=1)
    try:
        s = _connect(port)
        s.settimeout(4)
        t0 = time.time()
        R.eq('空闲连接被关闭', s.recv(16), b'')
        R.check('空闲超时约 1s', 0.7 <= time.time() - t0 <= 3.5, f'{time.time() - t0:.2f}s')
        s.close()
    finally:
        server.stop()


def test_stop_and_restart():
    server, port, records = _start_echo()
    s = _connect(port)
    try:
        s.sendall(b'hello\n')
        R.eq('stop 前连接正常', _recv_exact(s, 10), b'echo:hello')
        server.stop()
        s.settimeout(5)
        try:
            data = s.recv(1024)
        except OSError:
            data = b''
        R.eq('stop 关闭活动连接', data, b'')
    finally:
        s.close()
        server.stop()
    try:
        _connect(port, timeout=2).close()
        R.check('stop 后端口拒绝新连接', False, 'connected unexpectedly')
    except OSError:
        R.check('stop 后端口拒绝新连接', True)


def test_invalid_config_fallbacks():
    from flask_server.module.tcp_server import TcpServer
    R.eq('非法 framing 回退 line', TcpServer(host='127.0.0.1', port=0, framing='bogus').framing, 'line')
    R.eq('fixed frame_length=0 回退 line',
         TcpServer(host='127.0.0.1', port=0, framing='fixed', frame_length=0).framing, 'line')
    R.eq('head_tail 缺帧尾回退 line',
         TcpServer(host='127.0.0.1', port=0, framing='head_tail', frame_head=b'\xaa').framing, 'line')
    R.eq('非法分隔符回退 \\n',
         TcpServer(host='127.0.0.1', port=0, separator=b'').separator, b'\n')
    R.eq('非法 max_message_length 回退默认',
         TcpServer(host='127.0.0.1', port=0, max_message_length=0).max_message_length, 65536)


def test_no_handler_not_started():
    from flask_server.module.tcp_server import TcpServer
    from flask_server.module.udp_server import UdpServer
    tcp = TcpServer(host='127.0.0.1', port=0)
    R.eq('无 on_message 时 TCP 不启动', tcp.start(), False)
    udp = UdpServer(host='127.0.0.1', port=0)
    R.eq('无 on_message 时 UDP 不启动', udp.start(), False)


def test_udp():
    from flask_server.module.udp_server import UdpServer
    server = UdpServer(host='127.0.0.1', port=0, max_message_length=64)
    records = []

    @server.on_message
    def on_message(data, addr):
        records.append(data)
        if data == b'none':
            return None
        if data == b'str':
            return 'not-bytes'
        if data == b'active':
            server.send(b'active-reply', addr)
            return None
        return b'echo:' + data

    R.eq('UDP 服务器启动', server.start(), True)
    port = server.bound_address[1]

    def rt(payload, timeout=2):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        try:
            s.sendto(payload, ('127.0.0.1', port))
            return s.recvfrom(65535)[0]
        except socket.timeout:
            return None
        finally:
            s.close()

    try:
        R.eq('UDP 回显', rt(b'hello'), b'echo:hello')
        R.eq('UDP 返回 None 不回发', rt(b'none'), None)
        R.eq('UDP 返回 str 不回发', rt(b'str'), None)
        R.eq('UDP 主动 send', rt(b'active'), b'active-reply')
        # 超长数据报：Windows 的 recvfrom 缓冲不足会丢弃（WSAEMSGSIZE），
        # Linux 则截断后返回；文档写“静默截断”，存在平台差异。
        got = rt(b'x' * 100)
        if os.name == 'nt':
            R.check('UDP 超长数据报在 Windows 被丢弃（文档写截断）', got is None, got)
        else:
            R.eq('UDP 超长数据报截断', got, b'echo:' + b'x' * 58)
    finally:
        server.stop()
    R.check('UDP stop 后幂等', server.stop() is None)


def main():
    try:
        test_line_framing()
        test_line_oversized()
        test_raw_framing()
        test_fixed_framing()
        test_head_tail_framing()
        test_handler_error_keeps_connection()
        test_multiple_clients()
        test_connection_limits_and_idle()
        test_stop_and_restart()
        test_invalid_config_fallbacks()
        test_no_handler_not_started()
        test_udp()
    except Exception as e:  # noqa: BLE001
        dump_exception('e2e_protocols', e)
        R.check('e2e_protocols 未发生未捕获异常', False, f'{type(e).__name__}: {e}')
    return 0 if R.summary() else 1


if __name__ == '__main__':
    raise SystemExit(main())
