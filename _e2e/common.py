"""端到端测试公共设施（不导入 flask_server，避免测试进程被 .env 污染）。"""

import json
import os
import re
import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
E2E_DIR = ROOT / '_e2e'
TMP_DIR = E2E_DIR / 'tmp'
LOG_DIR = E2E_DIR / 'logs'
PY = sys.executable

# 以 `python _e2e/xxx.py` 运行时，sys.path[0] 是 _e2e/ 而非项目根；补上项目根
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for _d in (TMP_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)


class Results:
    def __init__(self, name):
        self.name = name
        self.passed = 0
        self.failures = []

    def check(self, label, cond, detail=''):
        if cond:
            self.passed += 1
            print(f'  PASS  {label}')
        else:
            self.failures.append((label, str(detail)))
            print(f'  FAIL  {label}  :: {detail}')

    def eq(self, label, actual, expected):
        self.check(label, actual == expected, f'expected={expected!r} actual={actual!r}')

    def contains(self, label, haystack, needle):
        self.check(label, needle in haystack, f'{needle!r} not found')

    def raises(self, label, fn, exc=Exception):
        try:
            fn()
        except exc as e:
            self.check(label, True, '')
            return e
        except Exception as e:  # noqa: BLE001
            self.check(label, False, f'unexpected {type(e).__name__}: {e}')
            return None
        self.check(label, False, 'did not raise')
        return None

    def summary(self):
        total = self.passed + len(self.failures)
        print(f'\n[{self.name}] {self.passed}/{total} checks passed, {len(self.failures)} failed')
        for label, detail in self.failures:
            print(f'  - {label}: {detail}')
        return len(self.failures) == 0


def parse_dotenv(path=ROOT / '.env'):
    env = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding='utf-8').splitlines():
        m = re.match(r'^\s*([A-Za-z_0-9]+)\s*=\s*(.*)$', line)
        if m:
            env[m.group(1)] = m.group(2)
    return env


def redis_url(db=15):
    """真实 Redis URL（改写 DB 号，避免污染业务库）；未配置返回 None。"""
    url = parse_dotenv().get('REDIS_URL')
    if not url:
        return None
    return re.sub(r'/\d+$', f'/{db}', url)


def base_env(**overrides):
    env = os.environ.copy()
    env.update({
        'APP_ENV': 'development',
        'DEBUG': 'false',
        'SERVER_HOST': '127.0.0.1',
        'THREAD_NUM': '8',
        'WORKER_NUM': '1',
        'SQLALCHEMY_URI': '',
        'SQLITE_DB_PATH': '',
        'REDIS_URL': '',
        'DB_REFLECT_ON_START': 'false',
        'AUTH_ENABLED': 'false',
        'AUTH_STORE': 'memory',
        'AUTH_PBKDF2_ITERATIONS': '10000',
        'AUTH_LOGIN_MAX_FAILS': '5',
        'AUTH_LOGIN_LOCK_SECONDS': '300',
        'RATE_LIMIT_ENABLED': 'false',
        'RATE_LIMIT_PER_MINUTE': '60',
        'RATE_LIMIT_STORE': 'memory',
        'TCP_ENABLED': 'false',
        'UDP_ENABLED': 'false',
        'TASK_WORKER_EMBEDDED': 'false',
        'TASK_QUEUE_STREAM': 'tasks:e2e',
        'TASK_QUEUE_GROUP': 'e2e',
        'TASK_RETRY_BACKOFF': '1',
        'TASK_MAX_RETRIES': '2',
        'TASK_RESERVE_TIMEOUT': '1',
        'TASK_VISIBILITY_TIMEOUT': '300',
        'TASK_STATUS_TTL': '86400',
        'TASK_DEAD_LETTER_MAX': '1000',
        'TASK_STATUS_MAX': '10000',
        'METRICS_ENABLED': 'true',
        'SECURITY_HEADERS_ENABLED': 'true',
        'HSTS_ENABLED': 'false',
        'CORS_ORIGINS': '*',
        'TRUSTED_PROXIES': '127.0.0.1,::1',
        'LOG_FILE_PATH': '',
        'LOG_TO_CONSOLE': 'true',
        'LOG_FORMAT': 'text',
        'MAX_CONTENT_LENGTH': '16777216',
        'SWAGGER_UI_URL': 'https://cdn.jsdelivr.net/npm/swagger-ui-dist/',
        'PYTHONUNBUFFERED': '1',
        'PYTHONIOENCODING': 'utf-8',
        'PYTHONPATH': str(ROOT),
    })
    env.update({k: str(v) for k, v in overrides.items()})
    return env


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_http(url, timeout=40, proc=None):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(f'server exited early rc={proc.returncode}')
        try:
            r = requests.get(url, timeout=2)
            if r.status_code < 500:
                return r
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(0.15)
    raise TimeoutError(f'wait_http {url} timeout, last={last}')


class Server:
    """启动一个真实 wsgi.py 进程（waitress），退出时终止并保存日志。"""

    def __init__(self, name, port, env, wait_path='/api/v1/healthz'):
        self.name = name
        self.port = port
        self.env = env
        self.wait_path = wait_path
        self.proc = None
        self.out_path = LOG_DIR / f'{name}.out.log'
        self.err_path = LOG_DIR / f'{name}.err.log'
        self._out = None
        self._err = None

    @property
    def base(self):
        return f'http://127.0.0.1:{self.port}'

    def __enter__(self):
        self._out = open(self.out_path, 'w', encoding='utf-8')
        self._err = open(self.err_path, 'w', encoding='utf-8')
        self.proc = subprocess.Popen(
            [PY, 'wsgi.py'], cwd=str(ROOT), env=self.env,
            stdout=self._out, stderr=self._err,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        try:
            wait_http(self.base + self.wait_path, proc=self.proc)
        except Exception:
            self.stop()
            raise RuntimeError(f'{self.name} failed to start\n{self.logs()}')
        return self

    def __exit__(self, *exc):
        self.stop()
        return False

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        for f in (self._out, self._err):
            if f is not None:
                try:
                    f.close()
                except Exception:  # noqa: BLE001
                    pass
        self._out = self._err = None

    def logs(self, tail=6000):
        text = ''
        for p in (self.out_path, self.err_path):
            if p.exists():
                text += f'\n--- {p.name} ---\n' + p.read_text(encoding='utf-8', errors='replace')
        return text[-tail:]


def get_json(resp):
    """兼容 requests.Response（.json() 方法）与 Flask Response（.json 属性）。"""
    try:
        value = resp.json
    except Exception:  # noqa: BLE001
        return None
    try:
        return value() if callable(value) else value
    except Exception:  # noqa: BLE001
        return None


def run_py(code, env=None, timeout=60):
    """在子进程中执行一段 Python（cwd=项目根），返回 CompletedProcess。"""
    return subprocess.run(
        [PY, '-c', code], cwd=str(ROOT), env=env or base_env(),
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )


def run_script(args, env=None, timeout=120):
    return subprocess.run(
        [PY] + args, cwd=str(ROOT), env=env or base_env(),
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )


def install_probe_modules():
    """写入临时 task/handler 模块（测试结束必须调用 remove_probe_modules）。"""
    task = ROOT / 'flask_server' / 'task' / 'e2e_probe.py'
    handler = ROOT / 'flask_server' / 'handler' / 'e2e_probe.py'
    task.write_text(
        "import os\n"
        "import time\n"
        "from flask_server.module.task_queue import task\n"
        "\n"
        "def _out(text):\n"
        "    path = os.environ.get('E2E_TASK_OUTPUT')\n"
        "    if path:\n"
        "        with open(path, 'a', encoding='utf-8') as f:\n"
        "            f.write(text + '\\n')\n"
        "\n"
        "@task('e2e.echo')\n"
        "def echo(value='x'):\n"
        "    _out(f'echo:{value}')\n"
        "    return value\n"
        "\n"
        "@task('e2e.fail_once')\n"
        "def fail_once(value='x'):\n"
        "    marker = (os.environ.get('E2E_TASK_OUTPUT') or '') + '.failonce'\n"
        "    n = int(open(marker).read() or 0) if os.path.exists(marker) else 0\n"
        "    n += 1\n"
        "    open(marker, 'w').write(str(n))\n"
        "    if n == 1:\n"
        "        raise RuntimeError('planned first failure')\n"
        "    _out(f'fail_once:{value}:{n}')\n"
        "    return n\n"
        "\n"
        "@task('e2e.always_fail')\n"
        "def always_fail(value='x'):\n"
        "    raise ValueError('always fails')\n"
        "\n"
        "@task('e2e.sleep')\n"
        "def sleep_task(seconds=1.0, value='x'):\n"
        "    time.sleep(float(seconds))\n"
        "    _out(f'sleep:{value}')\n",
        encoding='utf-8',
    )
    handler.write_text(
        "from flask_server.module import tcp_server, udp_server\n"
        "\n"
        "@tcp_server.on_message\n"
        "def tcp_on_message(conn, data, addr):\n"
        "    if data == b'boom':\n"
        "        raise RuntimeError('planned tcp handler error')\n"
        "    conn.sendall(b'echo:' + data + b'\\n')\n"
        "\n"
        "@udp_server.on_message\n"
        "def udp_on_message(data, addr):\n"
        "    if data == b'none':\n"
        "        return None\n"
        "    if data == b'str':\n"
        "        return 'not-bytes'\n"
        "    return b'echo:' + data\n",
        encoding='utf-8',
    )
    return task, handler


def remove_probe_modules():
    for rel in ('flask_server/task/e2e_probe.py', 'flask_server/handler/e2e_probe.py'):
        p = ROOT / rel
        if p.exists():
            p.unlink()
        cache = p.parent / '__pycache__'
        if cache.exists():
            for pyc in cache.glob('e2e_probe.*.pyc'):
                pyc.unlink(missing_ok=True)


def cleanup_redis(prefix='e2e:'):
    """删除测试用 Redis key（只删 e2e 前缀 + 限流测试 IP 的键）。"""
    url = redis_url(15)
    if not url:
        return 0
    import redis
    c = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=3, socket_timeout=3)
    removed = 0
    try:
        for pattern in (f'{prefix}*', 'rate:10.9.9.*', 'rate:10.1.*', 'rate:10.0.*'):
            keys = list(c.scan_iter(match=pattern, count=200))
            if keys:
                removed += c.delete(*keys)
    finally:
        c.close()
    return removed


def dump_exception(label, e):
    print(f'  ERROR {label}: {type(e).__name__}: {e}')
    traceback.print_exc()
