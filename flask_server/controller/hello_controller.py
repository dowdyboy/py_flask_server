from flask.views import MethodView
from flask_smorest import Blueprint
from flask_server.app import app, json_response
from flask_server.util import Logger, GraceResult
from flask_server.schema import GraceResultSchema, EchoSchema

Logger.info("hello_controller.py loaded")


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
        status = {'status': 'up'}

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


@blp.route('/echo')
class EchoView(MethodView):
    @blp.arguments(EchoSchema)
    @blp.response(200, GraceResultSchema)
    def post(self, data):
        """回显消息（演示参数校验）"""
        return GraceResult.ok({'echo': data['message']})
