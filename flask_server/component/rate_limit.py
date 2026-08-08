from flask import request, abort
from flask_server import app, config
from flask_server.module import memory_cache
from flask_server.util import Logger, CommonUtil

# 简单限流组件：按 (客户端IP, 请求路径) 做固定窗口计数
# 基于内存缓存（memory_cache，带 TTL），多实例部署时可改用 redis_cache
# 默认关闭（RATE_LIMIT_ENABLED=false），启用后每个 IP+路径 每分钟最多
# RATE_LIMIT_PER_MINUTE 次请求，超出返回 429


RATE_LIMIT_WINDOW = 60   # 固定窗口大小（秒）


@app.before_request
def rate_limit_check():
    if not config.rate_limit_enabled:
        return None
    ip = CommonUtil.get_real_ip(request, config.trusted_proxies)
    key = f'rate:{ip}:{request.path}'
    count = memory_cache.get(key) or 0
    if count >= config.rate_limit_per_minute:
        Logger.warn(f'rate limit exceeded: {key}')
        abort(429)
    memory_cache.set(key, count + 1, ttl=RATE_LIMIT_WINDOW)
    return None
