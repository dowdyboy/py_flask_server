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


def _parse_bytes_env(name: str, default: bytes) -> bytes:
    """安全解析字节串环境变量（支持转义写法：\\n / \\r\\n / \\xaa\\x55）。

    使用 latin-1 往返解码，保证 \\x80-\\xff 等二进制字节保持单字节不变
    （unicode_escape 会先把 \\xaa 解码为码点 U+00AA，再经 utf-8 编码会变成两字节）。
    空值或非法转义回退默认值并告警。
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = raw.encode('latin-1').decode('unicode_escape').encode('latin-1')
    except (UnicodeEncodeError, UnicodeDecodeError):
        value = b''
    if not value:
        print(f'[Config WARNING] {name}={raw!r} is empty/invalid, using default {default!r}')
        return default
    return value


# APP_ENV 预设档：development / staging / production
_APP_ENV = (os.environ.get('APP_ENV', 'development') or 'development').strip().lower()
_ENV_PRESETS = {
    'development': {'debug': True, 'host': '127.0.0.1', 'log_level': logging.DEBUG, 'log_to_console': True},
    'staging': {'debug': False, 'host': '0.0.0.0', 'log_level': logging.INFO, 'log_to_console': False},
    'production': {'debug': False, 'host': '0.0.0.0', 'log_level': logging.INFO, 'log_to_console': False},
}
if _APP_ENV not in _ENV_PRESETS:
    print(f'[Config WARNING] APP_ENV={_APP_ENV!r} is unknown '
          '(development/staging/production), falling back to development preset')
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

        # TCP 协议服务器配置（默认关闭；处理器注册见 flask_server/handler/）
        self.tcp_enabled = os.environ.get('TCP_ENABLED', 'false').lower() in ('1', 'true', 'yes')
        self.tcp_host = os.environ.get('TCP_HOST', '0.0.0.0')
        self.tcp_port = _parse_int('TCP_PORT', 9000)
        # 消息定界：line（分隔符）/ fixed（固定长度）/ head_tail（帧头帧尾）/ raw（原始流，自行拆包）
        self.tcp_framing = (os.environ.get('TCP_FRAMING', 'line') or 'line').strip().lower()
        if self.tcp_framing not in ('line', 'fixed', 'head_tail', 'raw'):
            print(f'[Config WARNING] TCP_FRAMING={self.tcp_framing!r} is invalid '
                  '(line/fixed/head_tail/raw), falling back to line')
            self.tcp_framing = 'line'
        # 行帧分隔符（line 模式）：支持转义写法（\n / \r\n）
        self.tcp_frame_separator = _parse_bytes_env('TCP_FRAME_SEPARATOR', b'\n')
        # 固定长度帧长（fixed 模式）：非法值（≤0）告警并回退 line
        self.tcp_frame_length = _parse_int('TCP_FRAME_LENGTH', 1024)
        if self.tcp_framing == 'fixed' and self.tcp_frame_length <= 0:
            print(f'[Config WARNING] TCP_FRAME_LENGTH={self.tcp_frame_length!r} is invalid '
                  '(must be > 0), falling back to line framing')
            self.tcp_framing = 'line'
        # 帧头/帧尾（head_tail 模式）：支持二进制转义写法（如 \xaa\x55）；
        # 配置不完整（缺任一）告警并回退 line
        self.tcp_frame_head = _parse_bytes_env('TCP_FRAME_HEAD', b'')
        self.tcp_frame_tail = _parse_bytes_env('TCP_FRAME_TAIL', b'')
        if self.tcp_framing == 'head_tail' and (not self.tcp_frame_head or not self.tcp_frame_tail):
            print('[Config WARNING] TCP_FRAME_HEAD / TCP_FRAME_TAIL must be set for '
                  'head_tail framing, falling back to line')
            self.tcp_framing = 'line'
        # 单条消息上限（超限视为协议错误断开连接，防内存 DoS；≤0 非法回退默认，
        # 否则 max=0 时 line/head_tail 每条消息都"超长"断开、raw 模式 recv(0) 秒断连接）
        self.tcp_max_message_length = _parse_int('TCP_MAX_MESSAGE_LENGTH', 64 * 1024)
        if self.tcp_max_message_length <= 0:
            print(f'[Config WARNING] TCP_MAX_MESSAGE_LENGTH={self.tcp_max_message_length!r} '
                  'is invalid (must be > 0), using default 65536')
            self.tcp_max_message_length = 64 * 1024
        # 每连接一线程：并发连接上限（超限拒绝新连接，防线程耗尽 DoS；≤0 表示不限制）
        self.tcp_max_connections = _parse_int('TCP_MAX_CONNECTIONS', 256)
        # fixed 模式帧长与消息上限的语义提示（固定帧可以合法地大于消息上限，
        # 但若大于默认 64KB 大概率是配置失误，给出告警）
        if self.tcp_framing == 'fixed' and self.tcp_frame_length > self.tcp_max_message_length:
            print(f'[Config WARNING] TCP_FRAME_LENGTH={self.tcp_frame_length} > '
                  f'TCP_MAX_MESSAGE_LENGTH={self.tcp_max_message_length}：固定帧可超过消息上限，'
                  '请确认配置是否符合预期')

        # UDP 协议服务器配置（默认关闭；处理器注册见 flask_server/handler/）
        self.udp_enabled = os.environ.get('UDP_ENABLED', 'false').lower() in ('1', 'true', 'yes')
        self.udp_host = os.environ.get('UDP_HOST', '0.0.0.0')
        self.udp_port = _parse_int('UDP_PORT', 9001)
        # 单数据报大小上限（recvfrom 缓冲大小；数据报超过此大小会被操作系统截断；
        # ≤0 非法回退默认，否则 recvfrom(0) 收到空数据报）
        self.udp_max_message_length = _parse_int('UDP_MAX_MESSAGE_LENGTH', 64 * 1024)
        if self.udp_max_message_length <= 0:
            print(f'[Config WARNING] UDP_MAX_MESSAGE_LENGTH={self.udp_max_message_length!r} '
                  'is invalid (must be > 0), using default 65536')
            self.udp_max_message_length = 64 * 1024
        # 每数据报一线程：并发上限（超限丢弃数据报，防洪泛线程爆炸 DoS；≤0 表示不限制）
        self.udp_max_concurrency = _parse_int('UDP_MAX_CONCURRENCY', 256)

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
        # LOG_FILE_PATH：日志文件路径（默认项目根 server.log）；设为空字符串则禁用文件日志（仅控制台）
        _log_file_path = os.environ.get('LOG_FILE_PATH')
        self.log_to_file = _log_file_path is not None and bool(_log_file_path.strip())
        self.log_filename = _log_file_path.strip() if _log_file_path and _log_file_path.strip() \
            else os.path.join(self.project_dir, 'server.log')
        self.log_level = logging.DEBUG if self.debug else logging.INFO
        self.log_max_bytes = _parse_int('LOG_MAX_BYTES', 10 * 1024 * 1024)
        self.log_backup_count = _parse_int('LOG_BACKUP_COUNT', 5)
        _log_to_console_env = os.environ.get('LOG_TO_CONSOLE')
        # 空字符串视为未设置（.env.example 中留空时不应覆盖 APP_ENV 预设档）
        self.log_to_console = (_log_to_console_env.strip().lower() in ('1', 'true', 'yes')) \
            if (_log_to_console_env is not None and _log_to_console_env.strip()) else _preset['log_to_console']
        self.log_format = os.environ.get('LOG_FORMAT', 'text')   # text / json
        # 是否打印 SQL 语句（开发调试用，默认关闭）
        self.debug_sql = os.environ.get('DEBUG_SQL', 'false').lower() in ('1', 'true', 'yes')

        # sqlite相关配置（默认不启用；空值视为未配置）
        self.db_file_path = os.environ.get('SQLITE_DB_PATH') or None
        # S4: 支持从外部 SQL 文件加载初始化脚本（保存原始文本，由 sqlite 模块 executescript 执行，
        # 避免按分号拆分破坏存储过程/字符串内的分号）
        self.db_init_sql = None
        _init_sql_path = os.environ.get('INIT_SQL_PATH') or None
        if _init_sql_path and os.path.isfile(_init_sql_path):
            with open(_init_sql_path, 'r', encoding='utf-8') as f:
                self.db_init_sql = f.read()
        self.db_init_sql_list = []
        if self.db_init_sql:
            # 兼容旧接口：按分号拆分一份语句列表（简单建表脚本场景可用）
            self.db_init_sql_list = [s.strip() for s in self.db_init_sql.split(';') if s.strip()]

        # sqlalchemy相关配置（默认不启用；空值视为未配置）
        self.sqlalchemy_uri = os.environ.get('SQLALCHEMY_URI') or None
        self.sqlalchemy_track_modify = False
        self.db_reflect_on_start = os.environ.get('DB_REFLECT_ON_START', 'true').lower() in ('1', 'true', 'yes')

        # S3: 数据库连接池参数
        self.db_pool_size = _parse_int('DB_POOL_SIZE', 10)
        self.db_pool_recycle = _parse_int('DB_POOL_RECYCLE', 3600)
        self.db_pool_pre_ping = os.environ.get('DB_POOL_PRE_PING', 'true').lower() in ('1', 'true', 'yes')
        self.db_pool_timeout = _parse_int('DB_POOL_TIMEOUT', 30)

        # Redis 缓存配置（默认不启用；空值视为未配置）
        self.redis_url = os.environ.get('REDIS_URL') or None

        # 限流配置（默认关闭）
        self.rate_limit_enabled = os.environ.get('RATE_LIMIT_ENABLED', 'false').lower() in ('1', 'true', 'yes')
        self.rate_limit_per_minute = _parse_int('RATE_LIMIT_PER_MINUTE', 60)
        # 限流存储：memory（默认，进程内）/ redis（多实例准确，需配置 REDIS_URL）
        self.rate_limit_store = os.environ.get('RATE_LIMIT_STORE', 'memory')

        # 认证模块配置（默认关闭；AUTH_STORE=sqlalchemy 时需配置 SQLALCHEMY_URI 并迁移建表）
        self.auth_enabled = os.environ.get('AUTH_ENABLED', 'false').lower() in ('1', 'true', 'yes')
        self.auth_token_ttl = _parse_int('AUTH_TOKEN_TTL', 7 * 24 * 3600)   # token 有效期（秒，默认 7 天）
        self.auth_refresh_token_ttl = _parse_int('AUTH_REFRESH_TOKEN_TTL', 30 * 24 * 3600)   # refresh token 有效期（秒，默认 30 天）
        self.auth_store = os.environ.get('AUTH_STORE', 'memory')             # memory / sqlalchemy
        # 登录防爆破：连续失败 N 次锁定 M 秒
        self.auth_login_max_fails = _parse_int('AUTH_LOGIN_MAX_FAILS', 5)
        self.auth_login_lock_seconds = _parse_int('AUTH_LOGIN_LOCK_SECONDS', 300)

        # Prometheus 指标（/metrics，依赖 prometheus-client，未安装或关闭时自动降级）
        self.metrics_enabled = os.environ.get('METRICS_ENABLED', 'true').lower() in ('1', 'true', 'yes')

        # 雪花 ID 机器标识（多进程部署时每进程应配置不同值；未配置时按 PID 自动派生）
        # 非法（非数字）或越界（非 0-31）时告警并视为未配置，避免 KeyGenerator 初始化崩溃
        _worker_id_raw = os.environ.get('SNOWFLAKE_WORKER_ID')
        if _worker_id_raw:
            _parsed_worker_id = _parse_int('SNOWFLAKE_WORKER_ID', -1)
            if 0 <= _parsed_worker_id <= 31:
                self.snowflake_worker_id = _parsed_worker_id
            else:
                print(f'[Config WARNING] SNOWFLAKE_WORKER_ID={_worker_id_raw!r} is invalid '
                      '(must be 0-31), falling back to PID-derived worker id')
                self.snowflake_worker_id = None
        else:
            self.snowflake_worker_id = None

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
