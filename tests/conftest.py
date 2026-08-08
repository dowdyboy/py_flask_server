import os
import sys

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
    'DB_REFLECT_ON_START': 'false',
    'APP_ENV': 'development',
    # 测试日志不写项目根 server.log（LOG_FILE_PATH 为空 = 禁用文件 handler）
    'LOG_FILE_PATH': '',
    # 固定 DEBUG=true：禁用 health 依赖检查缓存等仅生产生效的逻辑，保证测试实时探测
    'DEBUG': 'true',
}.items():
    os.environ.setdefault(_key, _val)


@pytest.fixture
def client():
    """Flask 测试客户端（HTTP 层集成测试用）"""
    from flask_server import app
    app.config['TESTING'] = True
    return app.test_client()

