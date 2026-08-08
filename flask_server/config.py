import os
import logging
from pathlib import Path

# 自动加载项目根目录下的 .env（复制 .env.example 即可生效，无需手动 export）
from dotenv import load_dotenv

_PROJECT_DIR = Path(__file__).parent.parent
load_dotenv(_PROJECT_DIR / '.env', override=False)


def _parse_int(name: str, default: int) -> int:
    """安全解析整数环境变量，非数字时回退默认值并警告"""
    val = os.environ.get(name)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        print(f'[Config WARNING] {name}={val!r} is not a valid integer, using default {default}')
        return default


# APP_ENV 预设档：development / staging / production
_APP_ENV = os.environ.get('APP_ENV', 'development').lower()
_ENV_PRESETS = {
    'development': {'debug': True, 'host': '127.0.0.1', 'log_level': logging.DEBUG, 'log_to_console': True},
    'staging': {'debug': False, 'host': '0.0.0.0', 'log_level': logging.INFO, 'log_to_console': False},
    'production': {'debug': False, 'host': '0.0.0.0', 'log_level': logging.INFO, 'log_to_console': False},
}
_preset = _ENV_PRESETS.get(_APP_ENV, _ENV_PRESETS['development'])


class Config:
    def __init__(self, ):
        # 项目根目录绝对路径
        self.project_dir = _PROJECT_DIR

        # 环境标识
        self.app_env = _APP_ENV

        # 系统配置（支持环境变量覆盖；APP_ENV 提供预设档）
        self.port = _parse_int('SERVER_PORT', 5000)
        self.host = os.environ.get('SERVER_HOST', _preset['host'])
        self.thread_num = _parse_int('THREAD_NUM', 10)
        self.async_task_queue_max = _parse_int('ASYNC_TASK_QUEUE_MAX', 500)   # 异步任务队列上限
        self.debug = os.environ.get('DEBUG', str(_preset['debug'])).lower() in ('1', 'true', 'yes')

        # SocketIO 配置（默认关闭，纯 HTTP 项目零负担）
        self.socketio_enabled = os.environ.get('SOCKETIO_ENABLED', 'false').lower() in ('1', 'true', 'yes')
        self.socketio_async_mode = os.environ.get('SOCKETIO_ASYNC_MODE', 'threading')
        # WebSocket 单条消息大小上限（默认 1MB，防止超大消息内存 DoS）
        self.socketio_max_http_buffer_size = _parse_int('SOCKETIO_MAX_HTTP_BUFFER_SIZE', 1_000_000)

        # CORS 允许的来源
        _cors_raw = os.environ.get('CORS_ORIGINS', '*')
        self.cors_origins = _cors_raw if _cors_raw == '*' else [o.strip() for o in _cors_raw.split(',') if o.strip()]

        # API 文档配置（flask-smorest）
        self.api_title = os.environ.get('API_TITLE', 'Flask Server API')
        self.api_version = os.environ.get('API_VERSION', 'v1')
        self.api_spec_url = os.environ.get('API_SPEC_URL', '/openapi.json')
        self.api_docs_url = os.environ.get('API_DOCS_URL', '/docs')
        self.swagger_ui_url = os.environ.get('SWAGGER_UI_URL', 'https://cdn.jsdelivr.net/npm/swagger-ui-dist/')

        # 安全配置
        self.default_secret_key = 'flask-server-scaffold-default-key-change-in-production'
        self.secret_key = os.environ.get('SECRET_KEY', self.default_secret_key)
        self.secret_key_is_default = (self.secret_key == self.default_secret_key)
        self.max_content_length = _parse_int('MAX_CONTENT_LENGTH', 16 * 1024 * 1024)   # 默认 16MB

        # logger配置
        self.log_filename = os.path.join(self.project_dir, 'server.log')
        self.log_level = logging.DEBUG if self.debug else logging.INFO
        self.log_max_bytes = _parse_int('LOG_MAX_BYTES', 10 * 1024 * 1024)
        self.log_backup_count = _parse_int('LOG_BACKUP_COUNT', 5)
        _log_to_console_env = os.environ.get('LOG_TO_CONSOLE')
        self.log_to_console = (_log_to_console_env.lower() in ('1', 'true', 'yes')) if _log_to_console_env is not None else _preset['log_to_console']
        self.log_format = os.environ.get('LOG_FORMAT', 'text')   # text / json

        # sqlite相关配置（默认不启用）
        self.db_file_path = os.environ.get('SQLITE_DB_PATH')
        self.db_init_sql_list = []
        # S4: 支持从外部 SQL 文件加载初始化脚本
        _init_sql_path = os.environ.get('INIT_SQL_PATH')
        if _init_sql_path and os.path.isfile(_init_sql_path):
            with open(_init_sql_path, 'r', encoding='utf-8') as f:
                self.db_init_sql_list = [s.strip() for s in f.read().split(';') if s.strip()]

        # sqlalchemy相关配置（默认不启用）
        self.sqlalchemy_uri = os.environ.get('SQLALCHEMY_URI')
        self.sqlalchemy_track_modify = False
        self.db_reflect_on_start = os.environ.get('DB_REFLECT_ON_START', 'true').lower() in ('1', 'true', 'yes')

        # S3: 数据库连接池参数
        self.db_pool_size = _parse_int('DB_POOL_SIZE', 10)
        self.db_pool_recycle = _parse_int('DB_POOL_RECYCLE', 3600)
        self.db_pool_pre_ping = os.environ.get('DB_POOL_PRE_PING', 'true').lower() in ('1', 'true', 'yes')
        self.db_pool_timeout = _parse_int('DB_POOL_TIMEOUT', 30)

        # Redis 缓存配置（默认不启用）
        self.redis_url = os.environ.get('REDIS_URL')

        # 限流配置（默认关闭）
        self.rate_limit_enabled = os.environ.get('RATE_LIMIT_ENABLED', 'false').lower() in ('1', 'true', 'yes')
        self.rate_limit_per_minute = _parse_int('RATE_LIMIT_PER_MINUTE', 60)

        # 可信代理配置（get_real_ip 仅在来自可信代理时才信任 X-Forwarded-For）
        _trusted_raw = os.environ.get('TRUSTED_PROXIES', '127.0.0.1,::1')
        self.trusted_proxies = [t.strip() for t in _trusted_raw.split(',') if t.strip()]

        # 安全响应头（默认开启）
        self.security_headers_enabled = os.environ.get('SECURITY_HEADERS_ENABLED', 'true').lower() in ('1', 'true', 'yes')

        # 静态文件存储配置
        self.file_saved_path = os.path.join(self.project_dir, 'storage')

        # Webui配置
        self.webui_dir = os.path.join(self.project_dir, 'webui')

        # 自定义配置


config = Config()
