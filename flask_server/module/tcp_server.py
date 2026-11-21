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
#   - head_tail：帧头（TCP_FRAME_HEAD）帧尾（TCP_FRAME_TAIL）定界，回调首个帧头
#     与其后首个帧尾之间的负载；帧头前垃圾字节直接丢弃，负载内可含帧头、但不得含
#     帧尾（无转义协议的固有约束）。切帧只看字节流内容，与 TCP 分包边界无关：
#     整帧一次到达 / 分 N 包 / 逐字节到达，切出的帧序列完全一致
#   - raw：每次 recv 的原始数据直接回调，由用户自行处理粘包/拆包
#
# head_tail 防内存 DoS（仅在"该帧已必然损坏"时触发，不是每帧规则）：从帧头起累计
# 缓冲超过 TCP_MAX_MESSAGE_LENGTH（外加帧尾长度容差）仍无帧尾 → 帧尾后到只会让负载
# 更长，该帧必然超限。此时若其后缓冲中还有帧头，按"最后手段"重同步到该帧头（计数
# 累计，超 _MAX_FRAME_RESYNCS 断开）；没有可重同步的帧头则直接按协议错误断开。
#
# 并发防护：TCP_MAX_CONNECTIONS 限制并发连接数（每连接一线程，防线程耗尽 DoS）。
# 槽位在 spawn 线程前检查（verify_request）：超限的新连接被直接关闭、不创建线程，
# 连接风暴场景零线程 churn；stop() 会同时关闭所有已建立的连接。
#
# 空闲防护：TCP_IDLE_TIMEOUT（默认 300s）内没有任何数据到达的连接自动关闭。
# 没有它时，攻击者建立 TCP_MAX_CONNECTIONS 个"只连不发"的连接就能占满所有槽位，
# 正常客户端被拒——即慢连接耗尽 DoS（≤0 表示不设超时，长连接心跳场景可关闭）。
#
# 处理器（文件放在 flask_server/handler/ 下自动生效）：
#   @tcp_server.on_connect     on_connect(conn, addr)
#   @tcp_server.on_message     on_message(conn, data, addr)   data 为 bytes（定界开销已剥离）
#   @tcp_server.on_disconnect  on_disconnect(conn, addr)
#   @tcp_server.on_error       on_error(e, *原处理器参数)

# head_tail 模式单连接"最后手段"重同步次数上限：只有从帧头起累计缓冲已超过
# TCP_MAX_MESSAGE_LENGTH 仍无帧尾（该帧必然超限、已确认损坏）时才允许重同步到
# 缓冲区中其后的帧头；超限即视为协议错误断开（防"帧头+超长垃圾"反复重同步无限
# 持有连接）。正常帧——包括负载内含帧头字节的合法帧——不触发重同步，因此该计数
# 不再是每帧规则；成功切帧后计数重置（"连续损坏"语义）。
_MAX_FRAME_RESYNCS = 256


class _TcpConnectionHandler(socketserver.BaseRequestHandler):
    """每连接一线程：on_connect → 循环读帧 on_message → on_disconnect

    并发槽位由 _ThreadingTcpServer 在 spawn 线程前获取、线程结束时释放，
    handler 不再自行管理（超限连接根本不会 spawn 线程）。
    """

    def handle(self):
        tcp = getattr(self.server, 'protocol')
        conn = self.request
        addr = self.client_address
        try:
            # 服务器已停止（stop 竞态窗口）时 track 返回 False：立即关闭连接，防漏网
            if not tcp._track_connection(conn):
                conn.close()
                return
            # 空闲超时：防"只连不发"占满并发槽位（慢连接耗尽 DoS）
            tcp._prepare_connection(conn)
            # 跨帧累积缓冲：一次 recv 可能含多帧，未读完的残帧留给下一轮（粘包/拆包处理）
            buffer = bytearray()
            state = {'resyncs': 0}   # head_tail 重同步计数（仅损坏流兜底恢复用）
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
        # 在 handler 线程自己的入口登记/注销：stop() 依赖计数判断在途处理是否结束。
        # 若放在 accept 线程登记，线程本地/配对关系会错位（曾因此漏减计数）。
        self.protocol._begin_handler()
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.protocol._release_slot()
            self.protocol._end_handler()


class TcpServer(ProtocolServer):
    """TCP 协议服务器（line/fixed/head_tail/raw 四种消息定界模式）"""

    _RECV_CHUNK = 4096

    def __init__(self, name='tcp', host=None, port=None, framing=None,
                 separator=None, max_message_length=None,
                 frame_length=None, frame_head=None, frame_tail=None,
                 max_connections=None, idle_timeout=None, shutdown_timeout=None):
        super().__init__(name, max_concurrency=max_connections
                         if max_connections is not None else config.tcp_max_connections,
                         shutdown_timeout=shutdown_timeout)
        self.host = host if host is not None else config.tcp_host
        self.port = port if port is not None else config.tcp_port
        self.framing = framing if framing is not None else config.tcp_framing
        self.separator = separator if separator is not None else config.tcp_frame_separator
        self.max_message_length = max_message_length if max_message_length is not None \
            else config.tcp_max_message_length
        self.frame_length = frame_length if frame_length is not None else config.tcp_frame_length
        self.frame_head = frame_head if frame_head is not None else config.tcp_frame_head
        self.frame_tail = frame_tail if frame_tail is not None else config.tcp_frame_tail
        # 空闲超时（秒）：≤0 表示不设超时
        self.idle_timeout = idle_timeout if idle_timeout is not None else config.tcp_idle_timeout
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

    def _prepare_connection(self, conn):
        """为新连接设置空闲超时（recv 超时即视为空闲，关闭连接释放槽位）"""
        if not self.idle_timeout or self.idle_timeout <= 0:
            return
        try:
            conn.settimeout(self.idle_timeout)
        except OSError as e:
            Logger.warn(f'{self.name}: set connection idle timeout failed: {e}')

    def _idle_timeout(self):
        """空闲超时告警（随后断开）"""
        Logger.warn(f'{self.name}: connection idle for {self.idle_timeout}s, closing '
                    '(raise TCP_IDLE_TIMEOUT for long-lived connections)')

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

    def _stop_handlers(self):
        """先关闭已建立连接（解除 recv 阻塞），再等在途 handler 真正结束"""
        self._close_all_connections()
        self._wait_handlers()

    # ---------------- 帧读取 ----------------

    def _read_frame(self, conn, buffer, state=None):
        """从累积缓冲读取一帧消息（按 framing 分发到具体定界实现）。

        buffer 为跨帧累积缓冲（bytearray），一次 recv 含多帧时残帧留在其中；
        state 为帧协议状态（head_tail 损坏流的"最后手段"重同步计数）；
        连接关闭 / 帧协议错误 / recv 异常：返回 None（调用方结束该连接）。
        """
        if self.framing == 'fixed':
            return self._read_fixed_frame(conn, buffer)
        if self.framing == 'head_tail':
            return self._read_head_tail_frame(conn, buffer, state)
        if self.framing == 'raw':
            try:
                data = conn.recv(self.max_message_length)
            except TimeoutError:
                self._idle_timeout()
                return None
            except OSError:
                return None
            return data or None
        return self._read_line_frame(conn, buffer)

    def _recv_more(self, conn, buffer):
        """recv 一块数据追加到缓冲；连接关闭/空闲超时/recv 异常返回 False"""
        try:
            chunk = conn.recv(self._RECV_CHUNK)
        except TimeoutError:
            # 空闲超时：TCP_IDLE_TIMEOUT 内没有任何数据到达（慢连接耗尽槽位）
            self._idle_timeout()
            return False
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
        """head_tail 模式：首个帧头与其后首个帧尾定界，返回二者之间的负载。

        定界语义（只取决于字节流内容，与 TCP 分包边界无关）：
          - 帧 = 缓冲中首个帧头与其后首个帧尾之间的字节；帧头前的垃圾字节直接丢弃
          - 整帧一次 recv、分 N 包、逐字节到达，切出的帧完全一致；帧头/帧尾被拆到
            两个 recv 时由跨帧累积缓冲补齐（无帧头时只保留末尾可能的半截帧头）
          - 负载内可以出现帧头字节（不再按"更靠后的帧头"重同步），但不得出现帧尾
            ——帧尾是唯一的帧终止标志，这是无转义协议的固有约束

        防内存 DoS（只在"该帧已必然损坏"时触发，不是每帧规则）：
          - 从帧头起累计缓冲超过 max_message_length（外加帧尾长度容差：末尾不足
            一个帧尾的字节可能是被拆包的帧尾前缀，不计入超长判定）仍无帧尾 →
            帧尾后到只会让负载更长，该帧必然超限：若其后缓冲中还有帧头，按
            "最后手段"重同步到该帧头（计数累计，超 _MAX_FRAME_RESYNCS 断开连接）；
            没有可重同步的帧头时直接按协议错误断开（不无界缓冲、不静默跳过数据）
          - 连接关闭 / 空闲超时 / 协议错误：返回 None（调用方结束该连接）
        """
        head, tail = self.frame_head, self.frame_tail
        state = state if state is not None else {}
        # 帧尾长度容差：尾部不足一个帧尾的字节可能是被拆包的帧尾前缀，不能算作负载
        tail_slack = len(tail) - 1
        while True:
            head_pos = buffer.find(head)
            if head_pos < 0:
                # 没有帧头：全部是帧头前的垃圾字节，直接丢弃；
                # 仅保留末尾可能的半截帧头，帧头跨包拆分时不丢帧
                keep = len(head) - 1
                if len(buffer) > keep:
                    del buffer[:len(buffer) - keep]
                if not self._recv_more(conn, buffer):
                    return None
                continue
            # 丢弃首个帧头前的垃圾字节
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
                # 成功切帧：重置连续损坏计数
                state['resyncs'] = 0
                return payload
            # 尚无帧尾，且从帧头起累计的负载已必然超过上限 → 该帧已损坏
            if len(buffer) - len(head) > self.max_message_length + tail_slack:
                next_head = buffer.find(head, len(head))
                if next_head < 0:
                    # 没有可重同步的帧头：协议错误断开（继续缓冲只会无界增长）
                    self._frame_too_long(len(buffer) - len(head))
                    return None
                # 最后手段：丢弃损坏帧的前缀，从其后的帧头重新对齐
                del buffer[:next_head]
                state['resyncs'] = state.get('resyncs', 0) + 1
                if state['resyncs'] > _MAX_FRAME_RESYNCS:
                    Logger.warn(f'{self.name}: too many frame resyncs '
                                f'({state["resyncs"]}), closing connection')
                    return None
                continue
            if not self._recv_more(conn, buffer):
                return None


tcp_server = TcpServer()
