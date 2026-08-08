from flask.views import MethodView
from flask_smorest import Blueprint
from flask_server.app import app, json_response
from flask_server.util import Logger, GraceResult
from flask_server.schema import GraceResultSchema, EchoSchema
from flask_server import config
import time

Logger.info("hello_controller.py loaded")

# 进程启动时间（用于健康检查的 uptime 字段）
_start_time = time.time()


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
        """健康检查（含 DB/Redis 连通性）"""
        status = {
            'status': 'up',
            'version': config.api_version,
            'uptime': int(time.time() - _start_time),
        }

        # 检查数据库连通性
        from flask_server.module import sqlalchemy
        db = sqlalchemy()
        if db is not None:
            try:
                with db.engine.connect() as conn:
                    conn.execute(db.text('SELECT 1'))
                status['db'] = 'ok'
            except Exception as e:
                status['db'] = f'error: {e}'

        # 检查 Redis 连通性
        from flask_server.module import redis_cache
        if redis_cache is not None:
            try:
                redis_cache.client.ping()
                status['redis'] = 'ok'
            except Exception as e:
                status['redis'] = f'error: {e}'

        return GraceResult.ok(status)


@blp.route('/healthz')
class HealthzView(MethodView):
    @blp.response(200, GraceResultSchema)
    def get(self):
        """存活探针（liveness）：仅表示进程存活，不检查依赖，恒 200"""
        return GraceResult.ok({'status': 'up'})


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

        # 检查 Redis 连通性
        from flask_server.module import redis_cache
        if redis_cache is not None:
            try:
                redis_cache.client.ping()
                status['redis'] = 'ok'
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
