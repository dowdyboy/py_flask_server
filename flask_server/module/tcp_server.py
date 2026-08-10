import socketserver
import threading

from ..config import config
from ..util import Logger
from .protocol_server import ProtocolServer

# TCP 协议服务器（socketserver.ThreadingTCPServer 线程模型，每连接一线程）
#
# 消息定界（TCP_FRAMING，四种模式）：
#   - line（默认）：按分隔符（TCP_FRAME_SEPARATOR，默认 \n）切分消息，
#     单条消息超过 TCP_MAX_MESSAGE_LENGTH 视为协议错误并断开连接（防内存 DoS）
#   - fixed：固定长度（TCP_FRAME_LENGTH 字节）切分消息，粘包/拆包自动处理
#   - head_tail：帧头（TCP_FRAME_HEAD）帧尾（TCP_FRAME_TAIL）定界，
#     回调帧头帧尾之间的负载；帧头前垃圾字节自动丢弃、帧尾缺失时以新帧头重同步
#     （重同步次数超上限视为协议错误）、帧尾缺失且超限断开连接
#   - raw：每次 recv 的原始数据直接回调，由用户自行处理粘包/拆包
#
# 并发防护：TCP_MAX_CONNECTIONS 限制并发连接数（每连接一线程，防线程耗尽 DoS）。
# 槽位在 spawn 线程前检查（verify_request）：超限的新连接被直接关闭、不创建线程，
# 连接风暴场景零线程 churn；stop() 会同时关闭所有已建立的连接。
#
# 处理器（文件放在 flask_server/handler/ 下自动生效）：
#   @tcp_server.on_connect     on_connect(conn, addr)
#   @tcp_server.on_message     on_message(conn, data, addr)   data 为 bytes（定界开销已剥离）
#   @tcp_server.on_disconnect  on_disconnect(conn, addr)
#   @tcp_server.on_error       on_error(e, *原处理器参数)

# head_tail 模式单连接最大重同步次数：帧尾连续缺失时按新帧头重同步，
# 超限视为协议错误断开（防"帧头+垃圾"反复重同步无限持有连接）
_MAX_FRAME_RESYNCS = 256


class _TcpConnectionHandler(socketserver.BaseRequestHandler):
    """每连接一线程：on_connect → 循环读帧 on_message → on_disconnect

    并发槽位由 _ThreadingTcpServer 在 spawn 线程前获取、线程结束时释放，
    handler 不再自行管理（超限连接根本不会 spawn 线程）。
    """

    def handle(self):
        tcp = self.server.protocol
        conn = self.request
        addr = self.client_address
        try:
            # 服务器已停止（stop 竞态窗口）时 track 返回 False：立即关闭连接，防漏网
            if not tcp._track_connection(conn):
                conn.close()
                return
            # 跨帧累积缓冲：一次 recv 可能含多帧，未读完的残帧留给下一轮（粘包/拆包处理）
            buffer = bytearray()
            state = {'resyncs': 0}   # head_tail 模式重同步计数（帧协议状态）
            if tcp._has_handler('on_connect'):
                tcp._dispatch('on_connect', (conn, addr), ctx=f' addr={addr}')
            try:
                while True:
                    data = tcp._read_frame(conn, buffer, state)
                    if data is None:
                        break   # 连接关闭或帧协议错误
                    if tcp._has_handler('on_message'):
                        tcp._dispatch('on_message', (conn, data, addr), ctx=f' addr={addr}')
            except Exception as e:
                # 防御性兜底：帧解析器意外异常（配置/边界 bug）统一进 Logger 后断开，
                # 避免 socketserver 默认把裸 traceback 打到 stderr（绕过统一日志）
                Logger.error(f'{tcp.name} frame read error addr={addr}: {e}', exc_info=True)
            finally:
                if tcp._has_handler('on_disconnect'):
                    tcp._dispatch('on_disconnect', (conn, addr), ctx=f' addr={addr}')
        finally:
            tcp._untrack_connection(conn)


class _ThreadingTcpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, addr, protocol):
        self.protocol = protocol
        super().__init__(addr, _TcpConnectionHandler)

    def verify_request(self, request, client_address):
        """并发槽位前置检查：超限返回 False（连接被直接关闭，不 spawn 线程）。

        在 accept 后、创建连接线程前检查——洪泛场景零线程 churn
        （若先 spawn 线程再在 handle() 内检查，被拒连接仍消耗线程创建成本）。
        """
        return self.protocol._acquire_slot()

    def process_request(self, request, client_address):
        try:
            super().process_request(request, client_address)
        except BaseException:
            # 线程 spawn 失败：释放已获取的槽位（防泄漏），异常继续传播
            self.protocol._release_slot()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.protocol._release_slot()


class TcpServer(ProtocolServer):
    """TCP 协议服务器（line/fixed/head_tail/raw 四种消息定界模式）"""

    _RECV_CHUNK = 4096

    def __init__(self, name='tcp', host=None, port=None, framing=None,
                 separator=None, max_message_length=None,
                 frame_length=None, frame_head=None, frame_tail=None,
                 max_connections=None):
        super().__init__(name, max_concurrency=max_connections
                         if max_connections is not None else config.tcp_max_connections)
        self.host = host if host is not None else config.tcp_host
        self.port = port if port is not None else config.tcp_port
        self.framing = framing if framing is not None else config.tcp_framing
        self.separator = separator if separator is not None else config.tcp_frame_separator
        self.max_message_length = max_message_length if max_message_length is not None \
            else config.tcp_max_message_length
        self.frame_length = frame_length if frame_length is not None else config.tcp_frame_length
        self.frame_head = frame_head if frame_head is not None else config.tcp_frame_head
        self.frame_tail = frame_tail if frame_tail is not None else config.tcp_frame_tail
        # 活动连接集合（stop 时统一关闭）
        self._connections = set()
        self._connections_lock = threading.Lock()
        # 直接构造实例时的配置兜底（与 config.py 的校验语义一致）
        if self.framing not in ('line', 'fixed', 'head_tail', 'raw'):
            Logger.warn(f'{self.name}: invalid framing {self.framing!r}, '
                        'falling back to line framing')
            self.framing = 'line'
        if not isinstance(self.separator, bytes) or not self.separator:
            Logger.warn(f'{self.name}: invalid frame separator, using default \\n')
            self.separator = b'\n'
        if not isinstance(self.max_message_length, int) or self.max_message_length <= 0:
            Logger.warn(f'{self.name}: invalid max_message_length {self.max_message_length!r}, '
                        'using default')
            self.max_message_length = config.tcp_max_message_length
        if self.framing == 'fixed' and (not isinstance(self.frame_length, int) or self.frame_length <= 0):
            Logger.warn(f'{self.name}: invalid frame_length {self.frame_length!r}, '
                        'falling back to line framing')
            self.framing = 'line'
        if self.framing == 'head_tail' and (
                not isinstance(self.frame_head, bytes) or not self.frame_head
                or not isinstance(self.frame_tail, bytes) or not self.frame_tail):
            Logger.warn(f'{self.name}: frame_head/frame_tail must be non-empty bytes '
                        'for head_tail framing, falling back to line framing')
            self.framing = 'line'

    def _create_server(self):
        return _ThreadingTcpServer((self.host, self.port), self)

    # ---------------- 活动连接跟踪 ----------------

    def _track_connection(self, conn):
        """登记活动连接；服务器已停止时返回 False（调用方应立即关闭连接）。

        stop() 与 handler 线程存在竞态窗口：连接已 accept 但尚未登记时 stop 已执行
        close_all。stop 先置 _server=None 再关连接，因此登记时检查 _server 即可
        闭合竞态——登记晚于停止的连接立即关闭，不会成为"漏网之鱼"。
        """
        with self._connections_lock:
            if self._server is None:
                return False
            self._connections.add(conn)
            return True

    def _untrack_connection(self, conn):
        with self._connections_lock:
            self._connections.discard(conn)

    def _close_all_connections(self):
        """关闭所有已建立的连接（stop 时调用；连接的 recv 解除阻塞自然退出）"""
        with self._connections_lock:
            conns = list(self._connections)
            self._connections.clear()
        for conn in conns:
            try:
                conn.close()
            except OSError:
                pass

    def stop(self):
        """停止服务器并关闭所有已建立的连接（幂等）"""
        super().stop()
        self._close_all_connections()

    # ---------------- 帧读取 ----------------

    def _read_frame(self, conn, buffer, state=None):
        """从累积缓冲读取一帧消息（按 framing 分发到具体定界实现）。

        buffer 为跨帧累积缓冲（bytearray），一次 recv 含多帧时残帧留在其中；
        state 为帧协议状态（head_tail 重同步计数）；
        连接关闭 / 帧协议错误 / recv 异常：返回 None（调用方结束该连接）。
        """
        if self.framing == 'fixed':
            return self._read_fixed_frame(conn, buffer)
        if self.framing == 'head_tail':
            return self._read_head_tail_frame(conn, buffer, state)
        if self.framing == 'raw':
            try:
                data = conn.recv(self.max_message_length)
            except OSError:
                return None
            return data or None
        return self._read_line_frame(conn, buffer)

    def _recv_more(self, conn, buffer):
        """recv 一块数据追加到缓冲；连接关闭/recv 异常返回 False"""
        try:
            chunk = conn.recv(self._RECV_CHUNK)
        except OSError:
            return False
        if not chunk:
            return False   # 客户端关闭
        buffer += chunk
        return True

    def _frame_too_long(self, length):
        """单帧长度超限告警（调用方随后断开连接）"""
        Logger.warn(f'{self.name}: message too long ({length} bytes, '
                    f'max {self.max_message_length}), closing connection')

    def _read_line_frame(self, conn, buffer):
        """line 模式：按分隔符切分，返回剥离分隔符的 bytes（纯空帧跳过，继续读）"""
        while True:
            sep_pos = buffer.find(self.separator)
            if sep_pos >= 0:
                # 帧完成；纯空帧（紧邻分隔符）跳过
                frame = bytes(buffer[:sep_pos])
                del buffer[:sep_pos + len(self.separator)]
                if not frame:
                    continue
                # 硬上限：分隔符与超长帧同包到达时也要拦截（max 判定无 recv 块粒度窗口）
                if len(frame) > self.max_message_length:
                    self._frame_too_long(len(frame))
                    return None
                return frame
            if len(buffer) > self.max_message_length:
                self._frame_too_long(len(buffer))
                return None
            if not self._recv_more(conn, buffer):
                return None

    def _read_fixed_frame(self, conn, buffer):
        """fixed 模式：缓冲累积满 frame_length 字节即切出一帧（粘包/拆包自动处理）"""
        while len(buffer) < self.frame_length:
            if not self._recv_more(conn, buffer):
                return None
        frame = bytes(buffer[:self.frame_length])
        del buffer[:self.frame_length]
        return frame

    def _read_head_tail_frame(self, conn, buffer, state=None):
        """head_tail 模式：帧头帧尾定界，返回帧头帧尾之间的负载。

        重同步策略：
          - 帧头前的垃圾字节自动丢弃（脏数据/半帧前缀）
          - 帧头后、帧尾前再次出现帧头 → 以更靠后的帧头重新同步
            （上一帧帧尾丢失时自动恢复；连续重同步次数超 _MAX_FRAME_RESYNCS
            （成功切帧后重置）视为协议错误断开，防"帧头+垃圾"反复重同步
            无限持有连接，又不误断偶发损坏的合法长连接）
          - 从帧头起超过 max_message_length 仍未找到帧尾 → 协议错误断开（防内存 DoS）
        """
        head, tail = self.frame_head, self.frame_tail
        state = state if state is not None else {}
        while True:
            head_pos = buffer.find(head)
            if head_pos < 0:
                if len(buffer) > self.max_message_length:
                    self._frame_too_long(len(buffer))
                    return None
                if not self._recv_more(conn, buffer):
                    return None
                continue
            # 丢弃帧头前的垃圾字节（重同步）
            del buffer[:head_pos]
            # 从帧头之后查找帧尾（避免帧尾是帧头子串时误判）
            tail_pos = buffer.find(tail, len(head))
            if tail_pos >= 0:
                payload = bytes(buffer[len(head):tail_pos])
                del buffer[:tail_pos + len(tail)]
                # 硬上限：帧尾与超长负载同包到达时也要拦截
                if len(payload) > self.max_message_length:
                    self._frame_too_long(len(payload))
                    return None
                # 成功切帧：重置连续重同步计数（"连续未完成帧"语义——
                # 合法流在损坏帧之间有成功帧，计数反复重置，不会被累计误断；
                # 攻击者永不完成帧，计数持续增长，上限照常生效）
                state['resyncs'] = 0
                return payload
            # 帧尾未找到：若帧头后再次出现帧头，重同步到更靠后的帧头
            inner_head = buffer.find(head, len(head))
            if inner_head >= 0:
                del buffer[:inner_head]
                state['resyncs'] = state.get('resyncs', 0) + 1
                if state['resyncs'] > _MAX_FRAME_RESYNCS:
                    Logger.warn(f'{self.name}: too many frame resyncs '
                                f'({state["resyncs"]}), closing connection')
                    return None
                continue
            if len(buffer) > self.max_message_length:
                self._frame_too_long(len(buffer))
                return None
            if not self._recv_more(conn, buffer):
                return None


tcp_server = TcpServer()
