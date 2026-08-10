import socketserver

from ..config import config
from ..util import Logger
from .protocol_server import ProtocolServer

# UDP 协议服务器（socketserver.ThreadingUDPServer 线程模型，每数据报一线程）
#
# 每个数据报调用一次 on_message(data, addr)：
#   - 处理器返回 bytes → 自动回发到来源地址（最简用法）
#   - 处理器返回 None → 不回发（可调用 udp_server.send(data, addr) 主动发送）
#
# 并发防护：UDP_MAX_CONCURRENCY 限制并发处理的数据报数（每数据报一线程，
# 防数据报洪泛线程爆炸 DoS）。槽位在 spawn 线程前检查（verify_request）：
# 超限的数据报被直接丢弃、不创建线程，洪泛场景零线程 churn。
# 注意：数据报超过 UDP_MAX_MESSAGE_LENGTH 会被操作系统静默截断（recvfrom 缓冲），
# 无感知；请按协议约定的最大报文设置该值。
#
# 处理器（文件放在 flask_server/handler/ 下自动生效）：
#   @udp_server.on_message   on_message(data, addr)
#   @udp_server.on_error     on_error(e, *原处理器参数)


class _UdpDatagramHandler(socketserver.BaseRequestHandler):
    """每个数据报一线程：on_message（返回值 bytes 自动回发）

    并发槽位由 _ThreadingUdpServer 在 spawn 线程前获取、线程结束时释放，
    handler 不再自行管理（超限数据报根本不会 spawn 线程）。
    """

    def handle(self):
        udp = self.server.protocol
        data, sock = self.request
        addr = self.client_address
        if not udp._has_handler('on_message'):
            return
        result = udp._dispatch('on_message', (data, addr), ctx=f' addr={addr}')
        if result is None:
            return
        if not isinstance(result, bytes):
            # 常见错误：返回 str 而非 bytes（如 'echo: ' + data 而非 b'echo: ' + data）。
            # 静默忽略会让用户难以排查，此处告警提示
            Logger.warn(f'{udp.name}: on_message returned {type(result).__name__}, '
                        'only bytes will be auto-replied (return None to skip)')
            return
        try:
            sock.sendto(result, addr)
        except OSError as e:
            Logger.warn(f'{udp.name} reply send failed: {e}')


class _ThreadingUdpServer(socketserver.ThreadingUDPServer):
    daemon_threads = True

    def __init__(self, addr, protocol):
        self.protocol = protocol
        super().__init__(addr, _UdpDatagramHandler)

    def verify_request(self, request, client_address):
        """并发槽位前置检查：超限返回 False（数据报被丢弃，不 spawn 线程）。

        在收到数据报后、创建处理线程前检查——洪泛场景零线程 churn
        （若先 spawn 线程再在 handle() 内检查，被丢弃数据报仍消耗线程创建成本）。
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


class UdpServer(ProtocolServer):
    """UDP 协议服务器（on_message 返回值 bytes 自动回发）"""

    def __init__(self, name='udp', host=None, port=None, max_message_length=None,
                 max_concurrency=None):
        super().__init__(name, max_concurrency=max_concurrency
                         if max_concurrency is not None else config.udp_max_concurrency)
        self.host = host if host is not None else config.udp_host
        self.port = port if port is not None else config.udp_port
        self.max_message_length = max_message_length if max_message_length is not None \
            else config.udp_max_message_length
        if not isinstance(self.max_message_length, int) or self.max_message_length <= 0:
            Logger.warn(f'{self.name}: invalid max_message_length {self.max_message_length!r}, '
                        'using default')
            self.max_message_length = config.udp_max_message_length

    def _create_server(self):
        server = _ThreadingUdpServer((self.host, self.port), self)
        server.max_packet_size = self.max_message_length
        return server

    def send(self, data, addr):
        """主动向指定地址发送数据（服务器已启动时有效），成功返回 True"""
        if not isinstance(data, bytes):
            Logger.warn(f'{self.name}: send data must be bytes')
            return False
        if self._server is None:
            Logger.warn(f'{self.name}: send called but server not running')
            return False
        try:
            self._server.socket.sendto(data, addr)
            return True
        except OSError as e:
            Logger.warn(f'{self.name} send failed: {e}')
            return False


udp_server = UdpServer()
