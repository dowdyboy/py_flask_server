from flask.views import MethodView
from flask_smorest import Blueprint
from flask_server.app import app, json_response
from flask_server.util import Logger, GraceResult
from flask_server.schema import GraceResultSchema, EchoSchema
from flask_server import config
import time
import threading

Logger.info("hello_controller.py loaded")

# 进程启动时间（用于健康检查的 uptime 字段）
_start_time = time.time()

# 依赖检查结果缓存：探针高频轮询时避免每次真实连库/连 Redis。
# 仅非 debug 模式启用（与 webui_controller 的静态缓存同模式，保证开发/测试实时）。
# readyz（就绪探针）保持实时检查，不受此缓存影响。
_HEALTH_CACHE_TTL = 5
_health_cache = {'ts': 0.0, 'db': 'ok', 'redis': 'ok'}
_health_cache_lock = threading.Lock() if not config.debug else None


# === @app.route 风格（最简示例，无校验无文档） ===

@app.route('/hello', methods=['GET'])
@json_response
def hello():
    """最简示例（统一 JSON 响应格式）"""
    return GraceResult.ok({'message': 'Hello, World!'})


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

    非 debug / 非测试模式下结果缓存 _HEALTH_CACHE_TTL 秒，避免探针高频轮询压垮依赖。
    """
    # debug 或 TESTING 模式（开发/测试）不缓存，保证每次真实探测
    if _health_cache_lock is None or app.config.get('TESTING'):
        return _live_db_check(), _live_redis_check()

    now = time.time()
    with _health_cache_lock:
        if now - _health_cache['ts'] < _HEALTH_CACHE_TTL:
            return _health_cache['db'] == 'ok', _health_cache['redis'] == 'ok'

    db_ok = _live_db_check()
    redis_ok = _live_redis_check()
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
    except Exception:
        Logger.warn('health db check failed', exc_info=True)
        return False


def _live_redis_check():
    """实时 Redis 连通性探测（尊重 RedisCache 的冷却逻辑：故障期间不真实连接，快速返回）"""
    from flask_server.module import redis_cache
    if redis_cache is None:
        return True   # 未配置 Redis 视为通过
    try:
        return bool(redis_cache.ping())
    except Exception:
        Logger.warn('health redis check failed', exc_info=True)
        return False


@blp.route('/readyz')
class ReadyzView(MethodView):
    @blp.response(200, GraceResultSchema)
    def get(self):
        """就绪探针（readiness）：依赖故障时返回 503（供容器/编排系统判定就绪）"""
        status = {'status': 'ready'}

        # 检查数据库连通性
        from flask_server.module import sqlalchemy
        db = sqlalchemy()
        if db is not None:
            try:
                with db.engine.connect() as conn:
                    conn.execute(db.text('SELECT 1'))
                status['db'] = 'ok'
            except Exception:
                status['db'] = 'error'
                return GraceResult.business_error(5030, 'dependency not ready', status), 503

        # 检查 Redis 连通性（尊重冷却逻辑，故障期间快速返回 error）
        from flask_server.module import redis_cache
        if redis_cache is not None:
            try:
                if redis_cache.ping():
                    status['redis'] = 'ok'
                else:
                    status['redis'] = 'error'
                    return GraceResult.business_error(5030, 'dependency not ready', status), 503
            except Exception:
                status['redis'] = 'error'
                return GraceResult.business_error(5030, 'dependency not ready', status), 503

        return GraceResult.ok(status)


@blp.route('/echo')
class EchoView(MethodView):
    @blp.arguments(EchoSchema)
    @blp.response(200, GraceResultSchema)
    def post(self, data):
        """回显消息（演示参数校验）"""
        return GraceResult.ok({'echo': data['message']})
