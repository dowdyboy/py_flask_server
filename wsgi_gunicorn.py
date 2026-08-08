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


def _build_options():
    from flask_server.config import _parse_int
    worker_num = _parse_int('WORKER_NUM', 4)
    return {
        'bind': f'{config.host}:{config.port}',
        'workers': worker_num,
        'worker_class': _worker_class(),
        'timeout': 120,
        'graceful_timeout': 15,
        'accesslog': '-',
        'errorlog': '-',
        'capture_output': False,
    }


def run():
    from gunicorn.app.base import BaseApplication

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
    print(f'[gunicorn] starting on {options["bind"]} '
          f'workers={options["workers"]} worker_class={options["worker_class"]}')
    StandaloneApplication(app, options).run()


if __name__ == '__main__':
    run()
