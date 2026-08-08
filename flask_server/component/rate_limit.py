from flask import request, abort
from flask_server import app, config
from flask_server.module import memory_cache, redis_cache
from flask_server.util import Logger, CommonUtil

# 简单限流组件：按 (客户端IP, 请求路径) 做固定窗口计数
# 存储：RATE_LIMIT_STORE=memory（默认，进程内）/ redis（多实例准确，需配置 REDIS_URL）
# 默认关闭（RATE_LIMIT_ENABLED=false），启用后每个 IP+路径 每分钟最多
# RATE_LIMIT_PER_MINUTE 次请求，超出返回 429


RATE_LIMIT_WINDOW = 60   # 固定窗口大小（秒）


def _get_store():
    """限流计数存储：redis（多实例）或内存（单实例）"""
    if config.rate_limit_store == 'redis' and redis_cache is not None:
        return redis_cache
    return memory_cache


@app.before_request
def rate_limit_check():
    if not config.rate_limit_enabled:
        return None
    cache = _get_store()
    ip = CommonUtil.get_real_ip(request, config.trusted_proxies)
    key = f'rate:{ip}:{request.path}'
    count = cache.get(key) or 0
    if count >= config.rate_limit_per_minute:
        Logger.warn(f'rate limit exceeded: {key}')
        abort(429)
    cache.set(key, count + 1, ttl=RATE_LIMIT_WINDOW)
    return None
