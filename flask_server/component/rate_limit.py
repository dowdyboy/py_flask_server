from flask import request, abort
from flask_server import app, config
from flask_server.module import memory_cache, redis_cache
from flask_server.util import Logger, CommonUtil

# 简单限流组件：按 (客户端IP, 请求路径) 做固定窗口计数
# 存储：RATE_LIMIT_STORE=memory（默认，进程内）/ redis（多实例准确，需配置 REDIS_URL）
# 默认关闭（RATE_LIMIT_ENABLED=false），启用后每个 IP+路径 每分钟最多
# RATE_LIMIT_PER_MINUTE 次请求，超出返回 429
#
# 计数使用原子 INCR（两个存储均支持），避免并发下 get-then-set 竞态绕过阈值；
# Redis 主存储不可用时回退内存计数（与 auth 模块的降级策略一致），单实例下限流仍生效。
#
# 双层配额：
#   - rate:{ip}           IP 级总配额（每分钟总请求数上限），防止攻击者用随机路径
#                         绕过基于路径的计数（webui catch-all 使任意唯一路径各有独立计数键）
#   - rate:{ip}:{path}    路径级配额（同一路径的高频滥用限制）
# 任一超限即返回 429。


RATE_LIMIT_WINDOW = 60   # 固定窗口大小（秒）

# 豁免限流的端点：监控抓取与探针高频轮询不应被限流。
# 若 readyz 被 429，编排系统会判实例不健康并摘除流量，导致剩余实例流量更集中、
# 更易触发 429 —— 形成雪崩循环，故探针必须豁免。
RATE_LIMIT_EXEMPT_PATHS = (
    '/metrics',
    '/api/v1/healthz',
    '/api/v1/readyz',
    '/api/v1/health',
)


def _get_store():
    """限流计数存储：redis（多实例）或内存（单实例）"""
    if config.rate_limit_store == 'redis' and redis_cache is not None:
        return redis_cache
    return memory_cache


def _incr(cache, key):
    """原子自增：主存储失败时回退内存计数。

    降级不逐条告警（RedisCache 已在故障开始/恢复时告警过，冷却期内每条请求都告警会刷屏）。
    """
    count = cache.incr(key, ttl=RATE_LIMIT_WINDOW)
    if count is not None or cache is memory_cache:
        return count
    return memory_cache.incr(key, ttl=RATE_LIMIT_WINDOW)


def _over_limit(count):
    """超限判定：计数超过 RATE_LIMIT_PER_MINUTE 即 429（IP 级与路径级共用同一阈值）"""
    return count is not None and count > config.rate_limit_per_minute


@app.before_request
def rate_limit_check():
    if not config.rate_limit_enabled:
        return None
    # 探针/监控端点豁免（见 RATE_LIMIT_EXEMPT_PATHS 注释：防 429 雪崩）
    if request.path in RATE_LIMIT_EXEMPT_PATHS:
        return None
    cache = _get_store()
    ip = CommonUtil.get_real_ip(request, config.trusted_proxies)

    # 路径级配额（可被随机路径绕过，仅防单端点滥用）
    path_count = _incr(cache, f'rate:{ip}:{request.path}')
    if _over_limit(path_count):
        Logger.warn(f'rate limit exceeded (path): rate:{ip}:{request.path}')
        abort(429)
    # IP 级总配额（兜底，防路径随机化绕过）
    ip_count = _incr(cache, f'rate:{ip}')
    if _over_limit(ip_count):
        Logger.warn(f'rate limit exceeded (ip): rate:{ip}')
        abort(429)
    return None
