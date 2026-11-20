import logging
import os
import sys
import time

import pytest

# 确保项目根目录在 sys.path 中，以便 import flask_server
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# 本地开发可能配置了 .env（真实 MySQL/Redis/AUTH/限流等）。
# 测试必须运行在干净环境：在 flask_server 导入前将关键配置重置为安全默认值，
# 避免真实配置（全局认证拦截、限流、真实数据库连接）污染用例。
# 注意：setdefault 语义——用户 shell 显式导出的环境变量仍优先（如 CI 的 TEST_DB_URI），
# 仅 .env 文件中的值被此处覆盖（config.py 的 load_dotenv(override=False) 不会覆盖已存在变量）。
for _key, _val in {
    'SQLALCHEMY_URI': '',
    'REDIS_URL': '',
    'SQLITE_DB_PATH': '',
    'AUTH_ENABLED': 'false',
    'AUTH_STORE': 'memory',
    'RATE_LIMIT_ENABLED': 'false',
    'TCP_ENABLED': 'false',
    'UDP_ENABLED': 'false',
    'DB_REFLECT_ON_START': 'false',
    'APP_ENV': 'development',
    # 测试日志不写项目根 server.log（LOG_FILE_PATH 为空 = 禁用文件 handler）
    'LOG_FILE_PATH': '',
    # 固定 DEBUG=true：禁用 health 依赖检查缓存等仅生产生效的逻辑，保证测试实时探测
    'DEBUG': 'true',
    # 密码哈希迭代次数：生产默认 600000（OWASP 建议），测试降到 10000 以免
    # 每次注册/登录都耗 ~0.2s CPU；哈希串自带迭代次数，不影响正确性断言
    'AUTH_PBKDF2_ITERATIONS': '10000',
}.items():
    os.environ.setdefault(_key, _val)


def pytest_configure(config):
    """注册本仓库自有的 marker。

    pyproject.toml 由另一条改动线维护，这里不往其中追加配置；
    真实 Redis 集成测试用 `-m redis_live` 选择（见 tests/test_redis_live.py）。
    """
    config.addinivalue_line(
        'markers',
        'redis_live: 需要真实 Redis 的集成测试（设置 REDIS_URL 后由 -m redis_live 选择）',
    )


# ---------------------------------------------------------------------------
# 全局状态隔离
#
# 本套件直接操作若干进程级单例（logger handler、SQLAlchemy 扩展、线程池、
# UserPO 模型类等）。一旦某个用例替换/关闭它们而不恢复，后续用例就会在
# "被上一个用例改坏"的环境里运行——表现为"单跑通过、全量失败"或依赖文件名的
# 字母序。下面这组 autouse fixture 在每个用例前后做最小快照/恢复，保证用例
# 之间（以及 pytest 的收集顺序）互不影响。快照只做浅拷贝，不复制大对象。
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_flask_server_logger():
    """快照并恢复 `flask_server` logger 的 handlers / level / propagate。

    test_logger.py 会调用 Logger.init()（内部 remove + close 所有旧 handler），
    其它用例也会临时挂载捕获 handler；不恢复的话，后面的用例可能面对
    "零 handler + propagate=False" 的静默 logger。
    """
    logger = logging.getLogger('flask_server')
    saved_handlers = list(logger.handlers)
    saved_level = logger.level
    saved_propagate = logger.propagate
    yield
    for handler in list(logger.handlers):
        if handler not in saved_handlers:
            logger.removeHandler(handler)
            try:
                handler.close()   # 关闭文件句柄（Windows 下不关闭无法删除临时文件）
            except Exception:
                pass
    for handler in saved_handlers:
        if handler not in logger.handlers:
            # Logger.init() 可能已 close() 过旧 handler；FileHandler.emit 会在
            # stream 为 None 时自行重开（追加模式），控制台 handler 也不受影响
            logger.addHandler(handler)
    logger.setLevel(saved_level)
    logger.propagate = saved_propagate


@pytest.fixture(autouse=True)
def _restore_sqlalchemy_module_state():
    """避免用例创建的 SQLAlchemy/Migrate/Flask app 泄漏到后续用例。

    `flask_server.module.sqlalchemy` 的 db/_app 是进程级单例；例如
    test_app.py::test_readyz_no_dependencies 依赖"全局 db 仍为 None"，
    以前只有在测试文件按字母序先跑时才成立。
    """
    sqlalchemy_module = sys.modules.get('flask_server.module.sqlalchemy')
    if sqlalchemy_module is None:
        yield
        return
    saved_state = (
        sqlalchemy_module.db,
        sqlalchemy_module.migrate,
        sqlalchemy_module._app,
        # 单例绑定的数据库 URI：init_SQLAlchemy 用它拒绝"同进程切换到另一个库"
        getattr(sqlalchemy_module, '_bound_uri', None),
    )
    yield
    (sqlalchemy_module.db, sqlalchemy_module.migrate,
     sqlalchemy_module._app, sqlalchemy_module._bound_uri) = saved_state


@pytest.fixture(autouse=True)
def _restore_user_model_module():
    """恢复 `flask_server.model.po.user` 的模块身份与 UserPO 类。

    test_auth.py 的 SQLAlchemy 用例会 importlib.reload 该模块，把占位 UserPO
    永久替换成绑定到临时 db 的真实 ORM 模型；临时 db 随后被 dispose，后续用例
    再 import 就会拿到一个不可用的模型类。
    """
    user_module = sys.modules.get('flask_server.model.po.user')
    if user_module is None:
        yield
        return
    saved_userpo = getattr(user_module, 'UserPO', None)
    saved_db = getattr(user_module, 'db', None)
    yield
    current = sys.modules.get('flask_server.model.po.user')
    if current is not user_module:
        # 极端情况下用例删除了 sys.modules 条目并重新导入：恢复原模块对象
        sys.modules['flask_server.model.po.user'] = user_module
        current = user_module
    if getattr(current, 'UserPO', None) is not saved_userpo:
        current.UserPO = saved_userpo
        current.db = saved_db


@pytest.fixture(autouse=True)
def _restore_default_config():
    """快照并恢复进程默认配置对象的所有字段。

    绝大多数用例用 monkeypatch 改配置，但少数直接赋值（如
    test_key_generator.py::test_worker_id_range 的 `config.snowflake_worker_id = None`）。
    default_config 的字段都是标量/小列表，浅拷贝字典 + 复制列表即可（约 120 个键）。
    """
    from flask_server.config import default_config
    saved_fields = {
        key: list(value) if isinstance(value, list) else value
        for key, value in default_config.__dict__.items()
    }
    yield
    default_config.__dict__.clear()
    default_config.__dict__.update(saved_fields)


def _executor_is_shutdown(executor):
    """线程池是否已被 shutdown（CPython 内部标记，无公开只读 API 可探测）"""
    return getattr(getattr(executor, '_executor', None), '_shutdown', False)


@pytest.fixture(autouse=True)
def _restore_async_task_executor():
    """保证 AsyncTaskUtil.executor 始终可用。

    wsgi.py 的 _cleanup_resources()（生产关闭流程）会调用
    `AsyncTaskUtil.executor.shutdown(wait=False)`；真实调用后线程池永久关闭，
    后续所有异步任务用例都会抛 "cannot schedule new futures after shutdown"。
    """
    from flask_server import config
    from flask_server.util.async_task_util import AsyncTaskUtil, BoundedExecutor

    saved_workers = config.thread_num
    saved_pending = config.async_task_queue_max
    original = AsyncTaskUtil.executor
    if _executor_is_shutdown(original):
        # 上一个用例已关闭但未替换：先恢复到可用状态
        original = BoundedExecutor(saved_workers, saved_pending)
        AsyncTaskUtil.executor = original
    yield
    current = AsyncTaskUtil.executor
    if current is not original:
        try:
            current.shutdown(wait=False)
        except Exception:
            pass
        if not _executor_is_shutdown(original):
            AsyncTaskUtil.executor = original
        else:
            AsyncTaskUtil.executor = BoundedExecutor(saved_workers, saved_pending)
    elif _executor_is_shutdown(original):
        AsyncTaskUtil.executor = BoundedExecutor(saved_workers, saved_pending)


@pytest.fixture(autouse=True)
def _restore_auth_service_store():
    """恢复 AuthService 的进程默认用户存储（app 上下文内的 per-app store 不受影响）。"""
    from flask_server.component.auth import AuthService
    saved_store = AuthService._store
    yield
    AuthService._store = saved_store


class FakeClock:
    """可注入的假时钟。

    用法：`monkeypatch.setattr(module, 'time', fake_clock)`——被测模块里的
    `time.time()` 变为可控值，`sleep()` 仍走真实时钟（避免误伤后台线程）。
    """

    def __init__(self, now=None):
        self.now = time.time() if now is None else now

    def time(self):
        return self.now

    def sleep(self, seconds):
        time.sleep(seconds)

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def fake_clock():
    """TTL/冷却/锁定过期等时间逻辑用的假时钟（配合 monkeypatch 注入到模块级 time）。"""
    return FakeClock()


@pytest.fixture
def client():
    """Flask 测试客户端（HTTP 层集成测试用）"""
    from flask_server import app
    app.config['TESTING'] = True
    return app.test_client()
