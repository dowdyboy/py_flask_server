import threading
import time

from flask import request, abort, g
from ..config import config
from ..module import memory_cache, redis_cache
from ..util import Logger, CommonUtil
from . import metrics

# 简单限流组件：按 (客户端IP, 请求路径) 做顺延窗口计数（每次请求刷新窗口 TTL）
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


RATE_LIMIT_WINDOW = 60   # 窗口大小（秒；每次自增顺延，活动期内计数不重置）

# 豁免限流的端点：监控抓取与探针高频轮询不应被限流。
# 若 readyz 被 429，编排系统会判实例不健康并摘除流量，导致剩余实例流量更集中、
# 更易触发 429 —— 形成雪崩循环，故探针必须豁免。
# 豁免判定对尾斜杠归一（/metrics/ 与 /metrics 同豁免），防 Prometheus 等
# 采集端配置尾斜杠 URL 时被误限流。
RATE_LIMIT_EXEMPT_PATHS = (
    '/metrics',
    '/api/v1/healthz',
    '/api/v1/readyz',
    '/api/v1/health',
)

# 限流缓存键中路径部分的最大长度：超长 URL（MAX_CONTENT_LENGTH 不管 URL 长度）
# 会直接撑爆缓存键内存（每键 TTL 60s 存活），超限时改用 SHA-256 哈希截断
_PATH_KEY_MAX_LEN = 512


def _path_key(path):
    """限流计数键的路径部分：超长路径哈希截断，防超长 URL 内存放大"""
    if len(path) <= _PATH_KEY_MAX_LEN:
        return path
    import hashlib
    return hashlib.sha256(path.encode('utf-8')).hexdigest()


# 429 告警冷却：攻击者可以每秒打上万次超限请求，逐条 WARN 会把日志/采集端打爆
# （再加一条 http_exception_handler 的 4xx 日志就是每请求两行）。窗口内只告警一次，
# 并汇总被抑制的条数。
_LIMIT_WARN_COOLDOWN = 5.0
_limit_warn_state = {'ts': 0.0, 'suppressed': 0}
_limit_warn_lock = threading.Lock()


def _warn_limited(msg):
    now = time.time()
    with _limit_warn_lock:
        if now - _limit_warn_state['ts'] < _LIMIT_WARN_COOLDOWN:
            _limit_warn_state['suppressed'] += 1
            return
        suppressed = _limit_warn_state['suppressed']
        _limit_warn_state['ts'] = now
        _limit_warn_state['suppressed'] = 0
    if suppressed:
        msg = f'{msg} (+{suppressed} similar suppressed in {_LIMIT_WARN_COOLDOWN:.0f}s)'
    Logger.warn(msg)


def _get_store(cfg=None):
    """限流计数存储：redis（多实例）或内存（单实例）"""
    cfg = cfg if cfg is not None else config
    if cfg.rate_limit_store == 'redis' and redis_cache is not None:
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


def _remember_rate_limit_state(count):
    """记录本请求的配额状态，供 after_request 写 X-RateLimit-* 头（含 429 响应）"""
    limit = config.rate_limit_per_minute
    remaining = max(0, limit - int(count or 0))
    current = getattr(g, 'rate_limit_state', None)
    if current is not None:
        remaining = min(current['remaining'], remaining)
        current['remaining'] = remaining
        return
    g.rate_limit_state = {
        'limit': limit,
        'remaining': remaining,
        'reset': RATE_LIMIT_WINDOW,
    }


def set_rate_limit_headers(resp):
    """把配额状态写入 X-RateLimit-Limit / Remaining / Reset（未启用限流时无状态，不写）"""
    state = getattr(g, 'rate_limit_state', None)
    if not state:
        return resp
    resp.headers.setdefault('X-RateLimit-Limit', str(state['limit']))
    resp.headers.setdefault('X-RateLimit-Remaining', str(state['remaining']))
    # 窗口是"顺延窗口"（每次请求刷新 TTL），这里给的是最长等待秒数，客户端按此退避即可
    resp.headers.setdefault('X-RateLimit-Reset', str(state['reset']))
    return resp


def rate_limit_check():
    """限流拦截器（由 init_rate_limit 注册为 before_request）"""
    if not config.rate_limit_enabled:
        return None
    # 探针/监控端点豁免（见 RATE_LIMIT_EXEMPT_PATHS 注释：防 429 雪崩）；
    # 尾斜杠归一（/metrics/ == /metrics），防采集端尾斜杠 URL 被误限流
    if request.path.rstrip('/') in RATE_LIMIT_EXEMPT_PATHS:
        return None
    # CORS 预检（OPTIONS）不承载业务，豁免避免大量 preflight 挤占配额
    if request.method == 'OPTIONS':
        return None
    cache = _get_store()
    ip = CommonUtil.get_real_ip(request, config.trusted_proxies) or 'unknown'
    path_key = _path_key(request.path)

    # 路径级配额（可被随机路径绕过，仅防单端点滥用；超长路径哈希截断防内存放大）
    path_count = _incr(cache, f'rate:{ip}:{path_key}')
    _remember_rate_limit_state(path_count)
    if _over_limit(path_count):
        # 日志也用截断后的 key：原样记录 100KB 的 URL 会让一条 429 变成一行 100KB 日志
        _warn_limited(f'rate limit exceeded (path): rate:{ip}:{path_key}')
        metrics.inc_rate_limited('path')
        g.retry_after = RATE_LIMIT_WINDOW   # 供 set_retry_after_header 写 Retry-After
        abort(429)
    # IP 级总配额（兜底，防路径随机化绕过）
    ip_count = _incr(cache, f'rate:{ip}')
    _remember_rate_limit_state(ip_count)
    if _over_limit(ip_count):
        _warn_limited(f'rate limit exceeded (ip): rate:{ip}')
        metrics.inc_rate_limited('ip')
        g.retry_after = RATE_LIMIT_WINDOW
        abort(429)
    return None


def init_rate_limit(app, cfg=None):
    """把限流拦截器注册到指定 app（由 create_app 调用）"""
    cfg = cfg if cfg is not None else config
    if cfg.rate_limit_store == 'redis' and cfg.redis_url is None:
        # 与 AUTH_STORE=sqlalchemy 漏配 URI 的处理方式对齐：启动期明确告警。
        # 这里不直接失败（限流是可选能力），但必须让"多实例准确"的配置承诺
        # 不至于在无声无息中退化成"每个进程各限各的"。
        Logger.warn('RATE_LIMIT_STORE=redis 但未配置 REDIS_URL：限流计数将退化为'
                    '进程内内存，多实例/多 worker 下实际配额是单实例的 N 倍')
    app.before_request(rate_limit_check)
    # 配额状态头（X-RateLimit-*）在请求结束后统一写入；未启用限流时 g 上无状态，自动跳过
    app.after_request(set_rate_limit_headers)
