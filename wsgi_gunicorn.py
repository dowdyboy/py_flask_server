import importlib.util
import sys

# gunicorn 生产入口（Linux 专用）
#
# 与 wsgi.py（waitress）的对比：
#   - waitress：零额外依赖、单进程多线程，够用且稳定
#   - gunicorn：多 worker 高并发。只有同时满足 "SOCKETIO_ENABLED=true" +
#              "SOCKETIO_ASYNC_MODE=eventlet" + 已安装 eventlet 时才使用 eventlet worker；
#              默认的 threading 模式用 sync worker（waitress 与 sync worker 都不支持 WebSocket）
#
# 用法：
#   python wsgi_gunicorn.py                     # 使用环境变量配置
#   WORKER_NUM=8 python wsgi_gunicorn.py        # 指定 worker 数
#
# 依赖（可选）：
#   pip install gunicorn        # 必须
#   pip install eventlet        # 仅 WebSocket 场景
#
# 注意：gunicorn 不支持 Windows，请使用 wsgi.py（waitress）或 server.py 开发。


def _check_platform():
    if sys.platform == 'win32':
        print('[ERROR] gunicorn does not support Windows. '
              'Use `python wsgi.py` (waitress) or `python server.py` (dev) instead.')
        sys.exit(1)


_check_platform()

from flask_server import app, config   # noqa: E402


def _worker_class():
    """按"实际生效的 async_mode"选择 worker class。

    修复前只看 SOCKETIO_ENABLED 就返回 eventlet：SOCKETIO_ENABLED=true +
    SOCKETIO_ASYNC_MODE=threading（.env.example 的默认组合）也会走 eventlet 分支，
    而 eventlet 是可选依赖（requirements.txt 中为注释）。未安装时 gunicorn 在解析
    worker class 阶段直接失败，整个入口起不来——app.py 里的降级逻辑被完全绕过。
    即使装了 eventlet，也会出现"SocketIO 实为 threading、worker 却是 eventlet"的错配。
    """
    if not config.socketio_enabled:
        return 'sync'
    if config.socketio_async_mode != 'eventlet':
        # threading（默认）模式不需要 eventlet worker
        return 'sync'
    if importlib.util.find_spec('eventlet') is None:
        print('[WARN] SOCKETIO_ASYNC_MODE=eventlet but eventlet is not installed; '
              'falling back to sync worker (SocketIO will run in threading mode)')
        return 'sync'
    return 'eventlet'


def _dispose_inherited_engine(app):
    """丢弃 master 进程在 fork 之前建立的 DB 连接。

    本模块在 import 期就执行 `create_app()`，而 `DB_REFLECT_ON_START` 默认为 true，
    反射会真实连库并把连接放回 QueuePool。随后 gunicorn fork 出 N 个 worker，
    **每个 worker 都继承了同一个连接池对象与同一条已建立的 socket**。多个 worker
    并发 check-out 到同一条连接后同时读写同一 socket，MySQL 侧会出现
    "Commands out of sync"、报文错位、随机 500；pool_pre_ping 也无法识别（它只是在这条
    坏连接上再发一次 SELECT 1）。

    正确做法（SQLAlchemy 官方对 fork 的建议）：子进程里 `engine.dispose(close=False)`,
    丢弃继承来的连接但保留连接池，worker 后续会各自建立新连接。
    """
    try:
        from flask_server.module import sqlalchemy
        from flask_server.util import Logger
        db = sqlalchemy()
        if db is None or app is None:
            return
        with app.app_context():
            db.engine.dispose(close=False)
        Logger.info('gunicorn worker: inherited SQLAlchemy connections disposed (fork-safe)')
    except Exception as e:      # 清理失败不应让 worker 起不来
        from flask_server.util import Logger
        Logger.warn(f'gunicorn worker: dispose inherited engine failed: {e}')


def _make_post_worker_init(worker_num):
    """构造 gunicorn 的 post_worker_init 钩子（fork 后清理 + 协议服务器启动策略）。

    ⚠️ 关键：post_worker_init 在**每个 worker 子进程**里都会执行一次。
    若每个 worker 都调用 start_protocol_servers()，worker 2..N 会因端口已被
    0 号 worker 占用而抛 OSError（SO_REUSEADDR 不允许两个进程同时 LISTEN），
    异常穿出钩子 → gunicorn 记 "Exception in worker process" → 子进程退出 →
    master 重新拉起 → **无限重启循环**，HTTP 只剩 1 个 worker 可用。

    因此多 worker 时直接跳过协议服务器（只打一条告警），
    需要 TCP/UDP 时请 WORKER_NUM=1 或将协议服务器独立进程部署。

    注意：DB 连接池的清理对所有 worker 数都要做（包括单 worker），它是 fork 安全问题，
    与协议服务器无关。
    """
    # 用闭包标志避免每个 worker 都重复告警——注意闭包在 master 创建、fork 后
    # 每个 worker 各持一份副本，所以实际效果是"每个 worker 首次进入时各告警一次"
    # （不是全进程只一次）。真正的"只一次"需要写文件/环境标记，收益不大。
    warned = {'multi_worker_skip': False}

    def _post_worker_init(worker):
        from flask_server.module import start_protocol_servers
        from flask_server.util import Logger

        # 先做 fork 安全清理（每个 worker 各一次，与 worker 数无关）。
        # 直接用模块级 app：它就是本入口 import 期创建、被 fork 继承的那个 Flask 实例，
        # `init_SQLAlchemy` 绑定的也是它。注意 gunicorn 的 worker.app 是它自己的
        # ArbiterApp 包装对象（不是 Flask app），worker.wsgi 才是 WSGI 可调用对象，
        # 两者都容易踩坑，这里不依赖 gunicorn 内部结构。
        from flask_server import app as flask_app
        _dispose_inherited_engine(flask_app)

        if worker_num > 1:
            if not warned['multi_worker_skip']:
                warned['multi_worker_skip'] = True
                Logger.warn('TCP/UDP protocol servers are not started with multiple gunicorn '
                            f'workers (workers={worker_num}): same port would conflict. '
                            'Use WORKER_NUM=1 or deploy protocol servers as a separate process.')
            return
        try:
            start_protocol_servers()
        except OSError as e:
            # 绑定失败（端口被占/权限不足）不应让 worker 崩溃：记录后继续提供 HTTP 服务
            Logger.error(f'protocol servers not started in gunicorn worker: {e}')

    return _post_worker_init


def _child_exit(server, worker):
    """gunicorn child_exit（在 master 进程中调用）：清理退出 worker 的多进程指标文件。

    启用 PROMETHEUS_MULTIPROC_DIR 后，每个 worker 的指标写在独立文件里；
    worker 被回收后若不清理，其计数器会一直残留在 /metrics 中（数据永久虚高）。
    """
    if not config.metrics_multiprocess_dir:
        return
    try:
        from prometheus_client import multiprocess
        multiprocess.mark_process_dead(worker.pid)
    except Exception as e:   # 指标清理失败不应影响 worker 回收
        from flask_server.util import Logger
        Logger.warn(f'mark_process_dead failed for pid={getattr(worker, "pid", None)}: {e}')


def _build_options():
    from flask_server.config import _parse_worker_num
    worker_num = _parse_worker_num(default=4)
    options = {
        'bind': f'{config.host}:{config.port}',
        'workers': worker_num,
        'worker_class': _worker_class(),
        'timeout': 120,
        'graceful_timeout': 15,
        'accesslog': '-',
        'errorlog': '-',
        'capture_output': False,
        'post_worker_init': _make_post_worker_init(worker_num),
    }
    if config.metrics_multiprocess_dir:
        options['child_exit'] = _child_exit
    return options


def run():
    from gunicorn.app.base import BaseApplication
    from flask_server.util.banner import print_startup_banner

    class StandaloneApplication(BaseApplication):
        def __init__(self, app, options=None):
            self.application = app
            self.options = options or {}
            super().__init__()

        def load_config(self):
            for key, value in self.options.items():
                self.cfg.set(key, value)

        def load(self):
            return self.application

    options = _build_options()
    # 传入实际 worker 数：banner 的多 worker 自检（Redis/token/TCP/日志）与 gunicorn
    # 默认（未设 WORKER_NUM 时为 4）保持一致，未显式配置时也能触发告警
    print_startup_banner(worker_num=options['workers'])
    print(f'[gunicorn] starting on {options["bind"]} '
          f'workers={options["workers"]} worker_class={options["worker_class"]}')
    StandaloneApplication(app, options).run()


if __name__ == '__main__':
    run()
