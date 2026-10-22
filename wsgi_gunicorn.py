import sys

# gunicorn 生产入口（Linux 专用）
#
# 与 wsgi.py（waitress）的对比：
#   - waitress：零额外依赖、单进程多线程，够用且稳定
#   - gunicorn：多 worker 高并发；SOCKETIO_ENABLED=true 时自动使用 eventlet worker
#              （waitress 不支持 WebSocket）
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
    """SOCKETIO_ENABLED=true 时使用 eventlet worker（waitress 不支持 WebSocket）"""
    if config.socketio_enabled:
        return 'eventlet'
    return 'sync'


def _make_post_worker_init(worker_num):
    """构造 gunicorn 的 post_worker_init 钩子（协议服务器启动策略）。

    ⚠️ 关键：post_worker_init 在**每个 worker 子进程**里都会执行一次。
    若每个 worker 都调用 start_protocol_servers()，worker 2..N 会因端口已被
    0 号 worker 占用而抛 OSError（SO_REUSEADDR 不允许两个进程同时 LISTEN），
    异常穿出钩子 → gunicorn 记 "Exception in worker process" → 子进程退出 →
    master 重新拉起 → **无限重启循环**，HTTP 只剩 1 个 worker 可用。

    因此多 worker 时直接跳过（只在 0 号 worker 打一条告警），
    需要 TCP/UDP 时请 WORKER_NUM=1 或将协议服务器独立进程部署。
    """
    def _post_worker_init(worker):
        from flask_server.module import start_protocol_servers
        from flask_server.util import Logger
        if worker_num > 1:
            if getattr(worker, 'age', 1) == 0:
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
