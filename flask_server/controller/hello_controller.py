from flask.views import MethodView
from flask import current_app, has_app_context
from flask_smorest import Blueprint
from ..app import json_response
from ..util import Logger, GraceResult
from ..schema import GraceResultSchema, EchoSchema
from ..config import config
import time
import threading

Logger.info("hello_controller.py loaded")

# 进程启动时间（用于健康检查的 uptime 字段）
_start_time = time.time()

# 依赖检查结果缓存：探针高频轮询时避免每次真实连库/连 Redis。
# 进程级共享（模块级字典）：这是刻意的已知单例，同进程多个 app 会共用同一份结果。
# 是否启用缓存改由**请求期**判断（TESTING / debug），不再在导入期按 config.debug 决定——
# 导入期决策会让 create_app(framework_config=Config({'debug': True})) 无法关闭缓存。
_health_cache_ttl = 5
_health_cache = {'ts': 0.0, 'db': 'ok', 'redis': 'ok'}
_health_cache_lock = threading.Lock()


# === @app.route 风格（最简示例，无校验无文档） ===
# 这类直接挂在 app 上的路由通过模块级 register(app) 钩子装配（由 create_app 调用），
# 保证同一份代码可以为不同的 app 实例注册路由。

@json_response
def hello():
    """最简示例（统一 JSON 响应格式）"""
    return GraceResult.ok({'message': 'Hello, World!'})


def register(app):
    """把 @app.route 风格的路由挂到指定 app（endpoint 名保持 'hello' 不变）"""
    app.add_url_rule('/hello', 'hello', hello, methods=['GET'])


# === flask-smorest Blueprint 风格（推荐：参数校验 + 自动 API 文档） ===

blp = Blueprint('hello', 'hello', url_prefix='/api/v1', description='示例接口')


@blp.route('/health')
class HealthView(MethodView):
    @blp.response(200, GraceResultSchema)
    def get(self):
        """健康检查（含 DB/Redis 连通性，结果短 TTL 缓存）"""
        status = {
            'status': 'up',
            'version': config.api_version,
            'uptime': int(time.time() - _start_time),
        }

        db_ok, redis_ok = _check_dependencies()
        status['db'] = 'ok' if db_ok else 'error'
        status['redis'] = 'ok' if redis_ok else 'error'

        return GraceResult.ok(status)


@blp.route('/healthz')
class HealthzView(MethodView):
    @blp.response(200, GraceResultSchema)
    def get(self):
        """存活探针（liveness）：仅表示进程存活，不检查依赖，恒 200"""
        return GraceResult.ok({'status': 'up'})


def _check_dependencies():
    """实时检查 DB/Redis 连通性，返回 (db_ok, redis_ok)。

    非 debug / 非测试模式下结果缓存 _health_cache_TTL 秒，避免探针高频轮询压垮依赖。
    """
    # debug 或 TESTING 模式（开发/测试）不缓存，保证每次真实探测。
    # 这里读的是**当前 app** 的 debug 标志，而不是导入期的 config.debug。
    cached_ok = has_app_context() and not (current_app.config.get('TESTING') or current_app.debug)

    now = time.time()
    if cached_ok:
        with _health_cache_lock:
            if now - _health_cache['ts'] < _health_cache_ttl:
                return _health_cache['db'] == 'ok', _health_cache['redis'] == 'ok'

    db_ok = _live_db_check()
    redis_ok = _live_redis_check()
    if cached_ok:
        with _health_cache_lock:
            _health_cache['ts'] = now
            _health_cache['db'] = 'ok' if db_ok else 'error'
            _health_cache['redis'] = 'ok' if redis_ok else 'error'
    return db_ok, redis_ok


def _live_db_check():
    """实时 DB 连通性探测"""
    from flask_server.module import sqlalchemy
    db = sqlalchemy()
    if db is None:
        return True   # 未配置数据库视为通过
    try:
        with db.engine.connect() as conn:
            conn.execute(db.text('SELECT 1'))
        return True
    except Exception as e:
        # 不带 exc_info：探针每几秒就跑一次，DB 故障（含"app 未绑定 SQLAlchemy"这类
        # 多实例场景）会持续刷完整 traceback，把真正的告警淹掉。异常摘要已足够定位。
        Logger.warn(f'health db check failed: {e}')
        return False


def _live_redis_check():
    """实时 Redis 连通性探测（尊重 RedisCache 的冷却逻辑：故障期间不真实连接，快速返回）"""
    from flask_server.module import redis_cache
    if redis_cache is None:
        return True   # 未配置 Redis 视为通过
    try:
        return bool(redis_cache.ping())
    except Exception as e:
        Logger.warn(f'health redis check failed: {e}')
        return False


@blp.route('/readyz')
class ReadyzView(MethodView):
    @blp.response(200, GraceResultSchema)
    def get(self):
        """就绪探针（readiness）：依赖故障时返回 503（供容器/编排系统判定就绪）"""
        status = {'status': 'ready'}

        # 直接复用 health 的探测实现（不经过缓存）：修复前这里内联复制了一份逻辑，
        # 且失败分支一行日志都不记，两处实现必然继续漂移。
        from flask_server.module import redis_cache, sqlalchemy
        db_ok = _live_db_check()
        redis_ok = _live_redis_check()
        if sqlalchemy() is not None:
            status['db'] = 'ok' if db_ok else 'error'
        if redis_cache is not None:
            status['redis'] = 'ok' if redis_ok else 'error'
        if not db_ok or not redis_ok:
            return GraceResult.business_error(5030, 'dependency not ready', status), 503
        return GraceResult.ok(status)


@blp.route('/echo')
class EchoView(MethodView):
    @blp.arguments(EchoSchema)
    @blp.response(200, GraceResultSchema)
    def post(self, data):
        """回显消息（演示参数校验）"""
        return GraceResult.ok({'echo': data['message']})
