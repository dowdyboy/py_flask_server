import atexit
import threading
import time

from ..util import Logger

# TCP/UDP 协议服务器共享基类
#
# 用法（单例，见 flask_server.module.tcp_server / udp_server）：
#   1. 注册处理器：装饰器注册是 import 副作用（不启动 socket），
#      处理器文件放在 flask_server/handler/ 下任意 .py 文件即自动生效
#   2. 启动/停止：由入口（server.py / wsgi.py / wsgi_gunicorn.py）调用
#      start_protocol_servers() / stop_protocol_servers()；atexit 兜底
#
# 线程模型：socketserver.Threading*Server（每连接/每数据报一线程），
# 与 waitress 线程模型及 AsyncTaskUtil 线程池风格一致，零新依赖、Windows 可用。
# 处理器异常不会中断服务器：Logger.error 记录完整 traceback 后走 on_error 钩子。


class ProtocolServer:
    """TCP/UDP 协议服务器共享基类（注册表 + 生命周期 + 异常钩子 + 并发上限）"""

    # 并发超限告警冷却窗口（秒）：洪泛场景下不逐条告警（防日志刷屏 DoS），
    # 窗口内累计拒绝次数，冷却结束输出一次摘要（与 Redis 冷却告警策略一致）
    _REJECT_WARN_COOLDOWN = 10

    def __init__(self, name, max_concurrency=None):
        self.name = name
        self._handlers = {}
        self._server = None
        self._thread = None
        self._lock = threading.Lock()
        self._atexit_registered = False
        # 并发槽位信号量：TCP 连接 / UDP 数据报的处理线程数上限（防线程耗尽 DoS）
        # None 或 ≤0 表示不限制
        self._semaphore = threading.Semaphore(max_concurrency) \
            if (max_concurrency is not None and max_concurrency > 0) else None
        # 超限拒绝的告警冷却状态
        self._last_reject_warn_ts = 0.0
        self._reject_count = 0

    # ---------------- 处理器注册（装饰器） ----------------

    def _register(self, hook, func):
        with self._lock:
            if hook in self._handlers and self._handlers[hook] is not func:
                Logger.warn(f'{self.name}: handler `{hook}` already registered, overwriting')
            self._handlers[hook] = func
        return func

    def on_connect(self, func):
        """注册连接建立处理器：on_connect(conn, addr)（仅 TCP）"""
        return self._register('on_connect', func)

    def on_message(self, func):
        """注册消息处理器：TCP on_message(conn, data, addr)；UDP on_message(data, addr)"""
        return self._register('on_message', func)

    def on_disconnect(self, func):
        """注册连接断开处理器：on_disconnect(conn, addr)（仅 TCP）"""
        return self._register('on_disconnect', func)

    def on_error(self, func):
        """注册处理器异常钩子：on_error(e, *原处理器参数)，返回值被忽略"""
        return self._register('on_error', func)

    def _has_handler(self, hook):
        return hook in self._handlers

    def _acquire_slot(self):
        """尝试获取并发处理槽位；已达上限返回 False（调用方拒绝新连接/丢弃数据报）"""
        if self._semaphore is None:
            return True
        if self._semaphore.acquire(blocking=False):
            return True
        self._record_reject()
        return False

    def _record_reject(self):
        """记录一次并发超限拒绝：告警冷却 + 拒绝计数（洪泛时防日志刷屏）"""
        with self._lock:
            self._reject_count += 1
            now = time.time()
            if now - self._last_reject_warn_ts < self._REJECT_WARN_COOLDOWN:
                return
            Logger.warn(f'{self.name}: concurrency limit reached, rejecting '
                        f'(+{self._reject_count} since last report)')
            self._last_reject_warn_ts = now
            self._reject_count = 0

    def _release_slot(self):
        """释放并发处理槽位（与 _acquire_slot 成对使用）"""
        if self._semaphore is not None:
            self._semaphore.release()

    def _dispatch(self, hook, args, ctx=''):
        """执行用户处理器并捕获异常：异常 → Logger.error(traceback) → on_error 钩子。

        返回处理器返回值；无处理器或处理器异常时返回 None。
        """
        func = self._handlers.get(hook)
        if func is None:
            return None
        try:
            return func(*args)
        except Exception as e:
            Logger.error(f'{self.name} handler `{hook}` error{ctx}: {e}', exc_info=True)
            err_hook = self._handlers.get('on_error')
            if err_hook is not None:
                try:
                    err_hook(e, *args)
                except Exception as e2:
                    Logger.error(f'{self.name} on_error hook failed: {e2}', exc_info=True)
            return None

    # ---------------- 生命周期 ----------------

    @property
    def is_running(self):
        """服务器是否已启动"""
        return self._server is not None

    @property
    def bound_address(self):
        """实际绑定地址 (host, port)；未启动返回 None（绑定端口 0 时可取真实端口）"""
        if self._server is None:
            return None
        return self._server.server_address[:2]

    def start(self):
        """启动服务器（后台线程 serve_forever）；重复调用无副作用。

        需已注册 on_message 处理器（否则告警不启动）；绑定失败抛 OSError（不静默）。
        """
        with self._lock:
            if self._server is not None:
                return True
            if not self._has_handler('on_message'):
                Logger.warn(f'{self.name}: enabled but no on_message handler registered, '
                            'server not started')
                return False
            try:
                server = self._create_server()
            except OSError as e:
                Logger.error(f'{self.name} start failed: {e}')
                raise
            self._server = server
            thread = threading.Thread(
                target=server.serve_forever, name=f'{self.name}-serve', daemon=True)
            try:
                thread.start()
            except Exception:
                # 线程启动失败（资源耗尽等极端场景）：回滚状态并关闭 socket，
                # 避免 is_running 误报与端口泄漏
                self._server = None
                self._thread = None
                try:
                    server.server_close()
                except Exception:
                    pass
                raise
            self._thread = thread
            if not self._atexit_registered:
                atexit.register(self.stop)
                self._atexit_registered = True
        Logger.info(f'{self.name} listening on {server.server_address}')
        return True

    def stop(self):
        """停止服务器（幂等）：shutdown → server_close → join 后台线程"""
        with self._lock:
            server = self._server
            thread = self._thread
            self._server = None
            self._thread = None
        if server is None:
            return
        try:
            server.shutdown()
        except Exception as e:
            Logger.error(f'{self.name} shutdown error: {e}')
        try:
            server.server_close()
        except Exception as e:
            Logger.error(f'{self.name} server_close error: {e}')
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)
        Logger.info(f'{self.name} stopped')

    def _create_server(self):
        """由子类实现：构建 socketserver 实例（绑定/监听在构造函数中完成）"""
        raise NotImplementedError
