# 启动 banner 与生产配置自检
# 供 server.py / wsgi.py / wsgi_gunicorn.py 入口调用（print 到控制台，不进日志文件）

from flask_server import config


def _status(enabled, extra=''):
    return f'ON {extra}'.strip() if enabled else 'OFF'


def build_banner():
    """构建启动摘要（不依赖运行时，纯字符串列表）"""
    lines = [
        '┌───────────────────────────── Flask Server ─────────────────────────────┐',
        f'  APP_ENV    : {config.app_env}',
        f'  LISTEN     : http://{config.host}:{config.port}',
        f'  API DOCS   : http://{config.host}:{config.port}{config.api_docs_url}',
        f'  DATABASE   : {_db_desc()}',
        f'  REDIS      : {_status(config.redis_url is not None)}',
        f'  AUTH       : {_status(config.auth_enabled, f"store={config.auth_store}")}',
        f'  METRICS    : {_status(config.metrics_enabled, "/metrics")}',
        f'  RATE LIMIT : {_status(config.rate_limit_enabled, f"{config.rate_limit_per_minute}/min per IP+path")}',
        f'  SOCKETIO   : {_status(config.socketio_enabled, f"async={config.socketio_async_mode}")}',
        f'  TCP        : {_status(config.tcp_enabled, f"{config.tcp_host}:{config.tcp_port}")}',
        f'  UDP        : {_status(config.udp_enabled, f"{config.udp_host}:{config.udp_port}")}',
        '└────────────────────────────────────────────────────────────────────────┘',
    ]
    return lines


def _db_desc():
    if config.sqlalchemy_uri:
        from flask_server.util import CommonUtil
        return f'SQLAlchemy ({CommonUtil.mask_uri(config.sqlalchemy_uri)})'
    if config.db_file_path:
        return f'SQLite ({config.db_file_path})'
    return 'not configured (in-memory)'


def check_production_config():
    """生产环境配置自检：返回警告列表（非生产环境返回空列表）"""
    if config.app_env == 'development':
        return []
    warnings = []
    if config.secret_key_is_default:
        warnings.append('SECRET_KEY 仍为模板默认值，生产环境必须设置强密钥')
    if config.cors_origins == '*':
        warnings.append('CORS_ORIGINS=* 允许任意来源跨域，建议收紧为具体域名')
    if config.debug:
        warnings.append('DEBUG=true 已开启（生产环境建议关闭）')
    if config.host == '127.0.0.1':
        warnings.append('SERVER_HOST=127.0.0.1 仅本机可访问，请确认是否置于反向代理之后')
    if _multi_worker() and config.redis_url is None:
        warnings.append('多 worker 部署未配置 REDIS_URL：memory_cache/限流计数/认证 token 为进程内，'
                        '多实例间数据不一致（如登录后 token 在另一 worker 失效），建议配置 REDIS_URL')
    if _multi_worker() and (config.tcp_enabled or config.udp_enabled):
        warnings.append('TCP/UDP 协议服务器每进程绑定同一端口：多 worker 部署会端口冲突，'
                        '请设置 WORKER_NUM=1 或将协议服务器独立进程部署')
    if _multi_worker() and config.log_to_file:
        warnings.append('多 worker 写同一日志文件存在轮转竞态（可能丢行/损坏），'
                        '建议 LOG_FILE_PATH= 禁用文件日志改用容器日志采集')
    return warnings


def _multi_worker():
    """是否多 worker 部署（gunicorn WORKER_NUM>1 或 socketio 多进程场景）"""
    from flask_server.config import _parse_int
    return _parse_int('WORKER_NUM', 1) > 1


def print_startup_banner():
    """打印启动 banner + 生产自检警告"""
    for line in build_banner():
        print(line)
    for warning in check_production_config():
        print(f'  [WARN] {warning}')
