from functools import wraps
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from ..config import config
from ..util import Logger, CommonUtil

# 使用SQLAlchemy作为数据库访问层，具体使用参考sqlalchemy库
# 本文件中的函数用于初始化SQLAlchemy对象，以及提供一个装饰器用于事务处理

# 脱敏数据库 URI 中的密码部分，避免明文密码写入日志
def _mask_uri(uri):
    return CommonUtil.mask_uri(uri)

Logger.info(f'SQLALCHEMY_DATABASE_URI : {_mask_uri(config.sqlalchemy_uri)}')

db = None         # SQLAlchemy 实例（进程级单例；首次 init_SQLAlchemy 时创建）
migrate = None    # Migrate 实例
_app = None       # 初始化时绑定的 Flask app（单应用场景）
# 首次绑定的数据库 URI：进程级单例的"所有权"记录，用于拒绝同进程切换到另一个库
_bound_uri = None
# 同时冗余记在 db 实例上：测试替换/恢复 db 对象后仍能识别该实例实际绑定的库
_BOUND_URI_ATTR = '_flask_server_bound_uri'


def _bound_database_uri():
    """当前 db 实例绑定的数据库 URI（未绑定或无法判断时返回 None）"""
    if db is None:
        return None
    uri = getattr(db, _BOUND_URI_ATTR, None)
    if isinstance(uri, str):
        return uri
    if _app is not None:
        uri = _app.config.get('SQLALCHEMY_DATABASE_URI')
        if isinstance(uri, str):
            return uri
    return _bound_uri


# 初始化SQLAlchemy对象，并根据配置决定是否反射数据库表结构
def init_SQLAlchemy(app, cfg=None, reflect=None):
    """为进程绑定唯一的 SQLAlchemy 实例（**进程级单例**）。

    Args:
        app (Flask): 目标应用
        cfg (Config, optional): 框架配置；缺省用进程默认配置
        reflect (bool, optional): 是否反射表结构；None 用 cfg.db_reflect_on_start。
            worker/CLI 的 create_core_app 会传 False：不需要反射，避免启动时真实连库

    已知约束（刻意，不是 bug）：Flask-SQLAlchemy 的扩展对象只能 `init_app` 一次绑定，
    因此同一进程内 create_app() 多次调用时：
      - 第一次带 SQLALCHEMY_URI 的调用完成绑定，`_app` 固定为它；
      - 后续调用未指定 URI（`sqlalchemy_uri is None`）或 URI 与已绑定的完全相同 →
        直接复用当前 db，不重复初始化，也不打日志；
      - 后续调用指定了**不同的 URI** → 抛 RuntimeError。静默沿用第一个数据库会让
        调用方误以为在写另一个（例如临时/测试）库，实际却写进了生产库——这是数据完整性
        事故，必须显式失败（错误信息里只出现脱敏后的 URI）。
      - 需要同时跑多个不同数据库的应用实例，请用多进程部署。
    """
    global db, _app, _bound_uri
    cfg = cfg if cfg is not None else config
    # 已初始化：进程级单例直接复用（create_app() 二次调用时不得把它重置为 None，
    # 否则第二次创建的应用会丢掉 db，且 model 模块的 UserPO 绑定失效）
    if db is not None:
        current_uri = _bound_database_uri()
        requested_uri = cfg.sqlalchemy_uri
        # 同库复用（或调用方未指定 URI）保持历史行为；换库必须失败而不是静默忽略。
        if requested_uri is None or current_uri is None or requested_uri == current_uri:
            return
        raise RuntimeError(
            'SQLAlchemy 是进程级单例（Flask-SQLAlchemy 的 db 只能 init_app 一次），'
            f'当前已绑定 {_mask_uri(current_uri)}，不能在同一进程切换到另一个数据库 '
            f'{_mask_uri(requested_uri)}。多个数据库请使用多进程部署：'
            '每个进程只配置一个 SQLALCHEMY_URI（见 create_app 的“进程级单例”说明）。'
        )
    if cfg.sqlalchemy_uri is None:
        return
    _app = app
    Logger.info(f'init_SQLAlchemy : {_mask_uri(cfg.sqlalchemy_uri)}')
    app.config['SQLALCHEMY_DATABASE_URI'] = cfg.sqlalchemy_uri
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = cfg.sqlalchemy_track_modify
    # S3: 数据库连接池参数
    # 只对 QueuePool 族生效：sqlite:///:memory: 用的是 StaticPool/SingletonThreadPool，
    # 传 pool_size/pool_timeout 会让 create_engine 直接抛 TypeError（应用导入即崩溃）。
    # 这里按后端判断，非池化后端（SQLite）只保留 pool_pre_ping。
    _backend = (cfg.sqlalchemy_uri or '').split(':', 1)[0].lower()
    if _backend.startswith('sqlite'):
        app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
            'pool_pre_ping': cfg.db_pool_pre_ping,
        }
    else:
        app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
            'pool_size': cfg.db_pool_size,
            'pool_recycle': cfg.db_pool_recycle,
            'pool_pre_ping': cfg.db_pool_pre_ping,
            'pool_timeout': cfg.db_pool_timeout,
        }
    # 必须在上面所有 app.config 赋值之后创建扩展：SQLAlchemy(app) 会立即读这些配置建 engine
    db = SQLAlchemy(app)
    _bound_uri = cfg.sqlalchemy_uri
    setattr(db, _BOUND_URI_ATTR, _bound_uri)
    should_reflect = cfg.db_reflect_on_start if reflect is None else reflect
    if should_reflect:
        # 容错：反射失败（如 DB 短暂不可达）不应导致应用启动崩溃，
        # engine 本身为惰性连接，首个查询时才真正连库
        try:
            with app.app_context():
                # 先导入声明式模型（UserPO 等）占据 metadata 表名，再只反射
                # 未声明的表——修复"已建表（如 flask db upgrade 后的 user 表）
                # + 声明式模型 + 二次启动"时 Table 重定义崩溃（InvalidRequestError）。
                # 模型导入依赖 db 已赋值（本函数前面已完成），模块缓存保证
                # flask_server/__init__ 后续的 `from . import model` 无重复执行。
                import importlib
                importlib.import_module('flask_server.model')
                from sqlalchemy import inspect
                declared = set(db.metadata.tables.keys())
                inspector = inspect(db.engine)
                to_reflect = [t for t in inspector.get_table_names() if t not in declared]
                if to_reflect:
                    db.metadata.reflect(bind=db.engine, only=to_reflect)
        except Exception as e:
            Logger.warn(f'db.reflect failed, continuing without reflection: {e}')


# 初始化 Flask-Migrate（数据库迁移），仅 db 非None时启用
def init_Migrate(app):
    global migrate
    if db is not None and migrate is None:
        migrate = Migrate(app, db)
        Logger.info('init_Migrate : Flask-Migrate enabled')


# 获取SQLAlchemy对象
def sqlalchemy():
    global db
    return db


# 获取Migrate对象
def get_migrate():
    global migrate
    return migrate


# 事务装饰器，用于处理事务
def sqlalchemy_trans(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if db is None:
            raise RuntimeError('SQLAlchemy 未初始化，请配置 SQLALCHEMY_URI')
        from flask import has_app_context
        # 自动管理 app context：业务函数内无需再手动 with app.app_context()。
        # 已经有 app context 时不需要 _app（它只是"无 context 时用来创建 context"的回退），
        # 因此先判断 context 再检查 _app——否则在请求上下文内也会被"未绑定 app"拦住。
        if has_app_context():
            return _run_trans(func, *args, **kwargs)
        if _app is None:
            raise RuntimeError('SQLAlchemy 未绑定 Flask app（init_SQLAlchemy 未执行）')
        with _app.app_context():
            return _run_trans(func, *args, **kwargs)
    return wrapper


def _run_trans(func, *args, **kwargs):
    if db is None:      # 防御：wrapper 已检查；这里让类型检查与运行期都明确非 None
        raise RuntimeError('SQLAlchemy 未初始化，请配置 SQLALCHEMY_URI')
    try:
        ret = func(*args, **kwargs)
        db.session.commit()
        return ret
    except Exception as e:
        db.session.rollback()
        Logger.error(f'sqlalchemy_trans : {e}', exc_info=True)
        raise


def in_app_context(func, *args, **kwargs):
    """在 app context 中执行函数（当前无 context 时自动创建），
    供非请求线程/独立调用场景访问 db.session / Model.query 使用"""
    if _app is None:
        raise RuntimeError('SQLAlchemy 未绑定 Flask app（init_SQLAlchemy 未执行）')
    from flask import has_app_context
    if has_app_context():
        return func(*args, **kwargs)
    with _app.app_context():
        return func(*args, **kwargs)
