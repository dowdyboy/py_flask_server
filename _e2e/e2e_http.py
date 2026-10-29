"""HTTP / 认证 / 限流 / 数据库 端到端测试（真实 waitress 进程 + 真实 Redis/MySQL）。"""

import json
import os
import re
import socket
import sqlite3
import time
import uuid
from pathlib import Path

import requests

from common import (
    Results, Server, TMP_DIR, base_env, cleanup_redis, dump_exception, free_port,
    get_json, install_probe_modules, parse_dotenv, redis_url, remove_probe_modules,
)

# 在导入 flask_server 之前把测试进程的环境变量也清理干净（.env 的 load_dotenv 不覆盖已有变量）
os.environ.update(base_env())

R = Results('HTTP/Auth/RateLimit/DB')


def _hdr(resp, name):
    return resp.headers.get(name)


def _recv_line(sock, timeout=5):
    sock.settimeout(timeout)
    buf = b''
    while not buf.endswith(b'\n'):
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
    return buf


def _udp_roundtrip(port, payload, timeout=2):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(payload, ('127.0.0.1', port))
        return s.recvfrom(65535)[0]
    except socket.timeout:
        return None
    finally:
        s.close()


# --------------------------------------------------------------------------
# 主服务：HTTP 基础能力 + 认证 + TCP/UDP 基础
# --------------------------------------------------------------------------

def test_main_server():
    port, tcp_port, udp_port = free_port(), free_port(), free_port()
    env = base_env(
        SERVER_PORT=port,
        AUTH_ENABLED='true',
        AUTH_LOGIN_MAX_FAILS='3',
        AUTH_LOGIN_LOCK_SECONDS='3',
        TCP_ENABLED='true', TCP_PORT=tcp_port, TCP_IDLE_TIMEOUT='2',
        TCP_MAX_CONNECTIONS='16',
        UDP_ENABLED='true', UDP_PORT=udp_port,
        METRICS_ENABLED='true',
    )
    with Server('http-main', port, env) as srv:
        c = requests.Session()

        # ---- 基础端点 ----
        r = c.get(srv.base + '/hello')
        R.eq('GET /hello status', r.status_code, 200)
        body = get_json(r)
        R.eq('GET /hello code', body.get('code'), 0)
        R.eq('GET /hello data', body.get('data', {}).get('message'), 'Hello, World!')
        R.check('GET /hello is JSON', r.headers.get('Content-Type', '').startswith('application/json'))

        r = c.get(srv.base + '/api/v1/healthz')
        R.eq('GET /api/v1/healthz status', r.status_code, 200)
        R.eq('healthz status field', get_json(r)['data']['status'], 'up')

        r = c.get(srv.base + '/api/v1/readyz')
        R.eq('GET /api/v1/readyz status', r.status_code, 200)
        R.eq('readyz status field', get_json(r)['data']['status'], 'ready')

        r = c.get(srv.base + '/api/v1/health')
        body = get_json(r)
        R.eq('GET /api/v1/health status', r.status_code, 200)
        R.eq('health version from config', body['data']['version'], 'v1')
        R.eq('health db ok (未配置视为通过)', body['data']['db'], 'ok')
        R.eq('health redis ok (未配置视为通过)', body['data']['redis'], 'ok')
        R.check('health uptime non-negative', isinstance(body['data']['uptime'], int) and body['data']['uptime'] >= 0)

        # ---- Swagger / OpenAPI ----
        r = c.get(srv.base + '/docs')
        R.eq('GET /docs status', r.status_code, 200)
        R.check('GET /docs html', 'text/html' in r.headers.get('Content-Type', '') and 'swagger-ui' in r.text)
        csp = r.headers.get('Content-Security-Policy', '')
        R.check('/docs CSP present', bool(csp))
        R.check('/docs CSP default-src self', "default-src 'self'" in csp)
        urls = re.findall(r'(?:src|href)="(https?://[^"]+)"', r.text)
        origins = {re.match(r'https?://[^/]+', u).group(0) for u in urls}
        R.check('/docs 引用了外部资源', bool(origins), f'urls={urls[:3]}')
        for origin in origins:
            R.check(f'/docs CSP 放行资源来源 {origin}', origin in csp, csp)

        r = c.get(srv.base + '/openapi.json')
        spec = get_json(r)
        R.eq('GET /openapi.json status', r.status_code, 200)
        R.eq('openapi info.version', spec['info']['version'], 'v1')
        for path in ('/api/v1/auth/login', '/api/v1/auth/register', '/api/v1/echo', '/api/v1/healthz'):
            R.check(f'openapi 包含 {path}', path in spec['paths'])

        # ---- 指标 ----
        r = c.get(srv.base + '/metrics')
        R.eq('GET /metrics status', r.status_code, 200)
        R.check('/metrics 是 Prometheus 文本', 'text/plain' in r.headers.get('Content-Type', ''))
        R.contains('/metrics 有请求计数', r.text, 'http_requests_total')
        R.contains('/metrics 有延迟直方图', r.text, 'http_request_duration_seconds')
        R.contains('/metrics 路由标签 /hello', r.text, 'route="/hello"')

        r = c.get(srv.base + '/metrics/', allow_redirects=False)
        R.eq('/metrics/ 308 到规范路径', r.status_code, 308)
        R.eq('/metrics/ Location', r.headers.get('Location'), '/metrics')

        # ---- 错误处理 / 统一响应 ----
        # AUTH_ENABLED=true 时 /api/ 下未登录请求会先被全局拦截器 401（路由前），
        # 因此这里先建一个会话用于后续 API 测试；未登录 401 的断言放在认证段。
        api_user = 'api_' + uuid.uuid4().hex[:8]
        r = c.post(srv.base + '/api/v1/auth/register', json={'username': api_user, 'password': 'secret123'})
        R.eq('API 测试用户注册 200', r.status_code, 200)
        r = c.post(srv.base + '/api/v1/auth/login', json={'username': api_user, 'password': 'secret123'})
        api_headers = {'X-AUTH-TOKEN': get_json(r)['data']['token']}

        r = c.get(srv.base + '/api/v1/does-not-exist', headers=api_headers)
        body = get_json(r)
        R.eq('404 status', r.status_code, 404)
        R.eq('404 code', body['code'], -1)
        R.eq('404 data', body['data'], '资源不存在')
        R.check('404 回写 X-Request-Id', bool(r.headers.get('X-Request-Id')))

        r = c.post(srv.base + '/hello')
        body = get_json(r)
        R.eq('405 status', r.status_code, 405)
        R.eq('405 code', body['code'], -1)
        R.check('405 data 为 description', 'method' in str(body['data']).lower())

        r = c.post(srv.base + '/api/v1/echo', json={'message': 'hello'}, headers=api_headers)
        R.eq('echo 正常 status', r.status_code, 200)
        R.eq('echo 正常 data', get_json(r)['data']['echo'], 'hello')

        r = c.post(srv.base + '/api/v1/echo', json={}, headers=api_headers)
        body = get_json(r)
        R.eq('echo 缺字段 422', r.status_code, 422)
        R.eq('echo 缺字段 code', body['code'], 1001)
        R.check('echo 422 字段级错误', isinstance(body['data'], dict) and 'json' in body['data'], body)

        r = c.post(srv.base + '/api/v1/echo', json=[], headers=api_headers)
        R.eq('echo 顶层数组归一后 422', r.status_code, 422)

        r = c.post(srv.base + '/api/v1/echo', json={'message': None}, headers=api_headers)
        R.eq('echo null 字段 422', r.status_code, 422)

        r = c.post(srv.base + '/api/v1/echo', data='{bad json',
                   headers={**api_headers, 'Content-Type': 'application/json'})
        body = get_json(r)
        R.eq('非法 JSON 400', r.status_code, 400)
        R.eq('非法 JSON code', body['code'], -1)

        r = c.post(srv.base + '/api/v1/echo', data='message=hi',
                   headers={**api_headers, 'Content-Type': 'application/x-www-form-urlencoded'})
        R.eq('表单 body 不满足 json schema -> 422', r.status_code, 422)

        # ---- request id ----
        r = c.get(srv.base + '/hello', headers={'X-Request-Id': 'trace-abc-123'})
        R.eq('自定义 X-Request-Id 透传', r.headers.get('X-Request-Id'), 'trace-abc-123')
        r = c.get(srv.base + '/hello', headers={'X-Request-Id': 'x' * 100})
        R.eq('超长 X-Request-Id 截断 64', len(r.headers.get('X-Request-Id', '')), 64)
        r = c.get(srv.base + '/hello', headers={'X-Request-Id': ''})
        R.eq('空 X-Request-Id 自动生成 32 hex', len(r.headers.get('X-Request-Id', '')), 32)

        # ---- 安全响应头 ----
        r = c.get(srv.base + '/hello')
        R.eq('nosniff', r.headers.get('X-Content-Type-Options'), 'nosniff')
        R.eq('X-Frame-Options', r.headers.get('X-Frame-Options'), 'DENY')
        R.eq('Referrer-Policy', r.headers.get('Referrer-Policy'), 'no-referrer')
        R.check('普通路径 CSP 严格', "script-src 'self'" in r.headers.get('Content-Security-Policy', ''))
        R.check('HSTS 默认不注入', 'Strict-Transport-Security' not in r.headers)

        # ---- CORS ----
        r = c.get(srv.base + '/hello', headers={'Origin': 'https://example.com'})
        R.check('CORS 简单请求 ACAO', r.headers.get('Access-Control-Allow-Origin') in ('*', 'https://example.com'),
                r.headers.get('Access-Control-Allow-Origin'))
        r = c.options(srv.base + '/api/v1/echo', headers={
            'Origin': 'https://example.com',
            'Access-Control-Request-Method': 'POST',
            'Access-Control-Request-Headers': 'Content-Type,X-AUTH-TOKEN',
        })
        R.eq('CORS 预检 200（认证开启也不 401）', r.status_code, 200)
        R.check('CORS 预检 ACAO', r.headers.get('Access-Control-Allow-Origin') in ('*', 'https://example.com'),
                r.headers.get('Access-Control-Allow-Origin'))
        R.check('CORS 预检 ACAM 含 POST', 'POST' in r.headers.get('Access-Control-Allow-Methods', ''))

        # ---- WebUI / 静态站点 ----
        r = c.get(srv.base + '/')
        R.eq('GET / 200', r.status_code, 200)
        R.contains('GET / 返回 index.html', r.text, 'Flask Server Scaffold')
        r = c.get(srv.base + '/dashboard')
        R.eq('SPA 回退 200', r.status_code, 200)
        R.contains('SPA 回退 index.html', r.text, 'Flask Server Scaffold')
        r = c.get(srv.base + '/missing.js')
        R.eq('缺失静态资源 404（不回退 index）', r.status_code, 404)
        r = c.get(srv.base + '/api')
        R.eq('/api 不回落 SPA', r.status_code, 404)
        r = c.get(srv.base + '/API/v1/healthz')
        R.eq('/API 大小写变体不绕过 SPA 404', r.status_code, 404)
        r = c.get(srv.base + '/..%2fREADME.md')
        R.eq('路径穿越 404', r.status_code, 404)
        r = c.get(srv.base + '/docs/', allow_redirects=False)
        R.eq('/docs/ 308 规范路径', r.status_code, 308)

        # ---- 认证 ----
        r = c.get(srv.base + '/api/v1/auth/me')
        R.eq('未登录 /me 401', r.status_code, 401)
        R.eq('未登录 /me code', get_json(r)['code'], 4002)

        r = c.post(srv.base + '/api/v1/echo', json={})
        R.eq('全局认证拦截 /api（未登录 401）', r.status_code, 401)

        user = 'alice_' + uuid.uuid4().hex[:8]
        r = c.post(srv.base + '/api/v1/auth/register', json={'username': user, 'password': 'secret123'})
        R.eq('注册 200', r.status_code, 200)
        R.check('注册返回 uid', bool(get_json(r)['data'].get('uid')))
        r = c.post(srv.base + '/api/v1/auth/register', json={'username': user, 'password': 'other123'})
        R.eq('重复注册 400', r.status_code, 400)
        R.eq('重复注册 code', get_json(r)['code'], 4001)
        r = c.post(srv.base + '/api/v1/auth/register', json={'username': 'bad name!', 'password': 'secret123'})
        R.eq('非法用户名 422', r.status_code, 422)
        r = c.post(srv.base + '/api/v1/auth/register', json={'username': 'okname', 'password': '123'})
        R.eq('过短密码 422', r.status_code, 422)

        r = c.post(srv.base + '/api/v1/auth/login', json={'username': user, 'password': 'wrong-pass'})
        R.eq('错误密码 401', r.status_code, 401)
        R.eq('错误密码 code', get_json(r)['code'], 4001)
        r = c.post(srv.base + '/api/v1/auth/login', json={'username': user, 'password': 'secret123'})
        data = get_json(r)['data']
        R.eq('登录 200', r.status_code, 200)
        token, refresh = data['token'], data['refresh_token']
        R.eq('access token 长度', len(token), 32)
        R.eq('refresh token 长度', len(refresh), 32)

        r = c.get(srv.base + '/api/v1/auth/me', headers={'X-AUTH-TOKEN': token})
        me = get_json(r)['data']
        R.eq('/me 200', r.status_code, 200)
        R.eq('/me username', me['username'], user)
        R.check('/me 不泄露 passwd', 'passwd' not in me)
        R.check('/me 包含 create_time', bool(me.get('create_time')))

        r = c.post(srv.base + '/api/v1/echo', headers={'X-AUTH-TOKEN': token}, json={})
        R.eq('全局认证拦截 /api（带 token 放行）', r.status_code, 422)
        r = c.post(srv.base + '/api/v1/echo', headers={'X-AUTH-TOKEN': token}, json={'message': 'authed'})
        R.eq('带 token 业务接口 200', r.status_code, 200)

        r = c.post(srv.base + '/api/v1/auth/logout', headers={'X-AUTH-TOKEN': token})
        R.eq('登出 200', r.status_code, 200)
        r = c.get(srv.base + '/api/v1/auth/me', headers={'X-AUTH-TOKEN': token})
        R.eq('登出后 token 失效', r.status_code, 401)

        # refresh 轮换
        r = c.post(srv.base + '/api/v1/auth/login', json={'username': user, 'password': 'secret123'})
        data = get_json(r)['data']
        old_refresh = data['refresh_token']
        r = c.post(srv.base + '/api/v1/auth/refresh', json={'refresh_token': old_refresh})
        R.eq('refresh 200', r.status_code, 200)
        new = get_json(r)['data']
        R.check('refresh 返回新 access/refresh',
                new['token'] != data['token'] and new['refresh_token'] != old_refresh)
        r = c.post(srv.base + '/api/v1/auth/refresh', json={'refresh_token': old_refresh})
        R.eq('旧 refresh 单次使用（重放 401）', r.status_code, 401)
        r = c.get(srv.base + '/api/v1/auth/me', headers={'X-AUTH-TOKEN': new['token']})
        R.eq('新 access 可用', r.status_code, 200)
        r = c.post(srv.base + '/api/v1/auth/logout', headers={'X-AUTH-TOKEN': new['token']},
                   json={'refresh_token': new['refresh_token']})
        R.eq('登出并吊销 refresh', r.status_code, 200)
        r = c.post(srv.base + '/api/v1/auth/refresh', json={'refresh_token': new['refresh_token']})
        R.eq('已吊销 refresh 不可用', r.status_code, 401)

        # 登录锁定按 (用户名, IP) 隔离 + 到期解锁
        lock_user = 'lock_' + uuid.uuid4().hex[:8]
        c.post(srv.base + '/api/v1/auth/register', json={'username': lock_user, 'password': 'secret123'})
        for i in range(3):
            r = c.post(srv.base + '/api/v1/auth/login', json={'username': lock_user, 'password': 'wrong-pass'},
                       headers={'X-Forwarded-For': '10.0.0.1'})
            R.eq(f'锁定前第 {i + 1} 次失败 401', r.status_code, 401)
        r = c.post(srv.base + '/api/v1/auth/login', json={'username': lock_user, 'password': 'secret123'},
                   headers={'X-Forwarded-For': '10.0.0.1'})
        R.eq('达到阈值后正确密码也 429', r.status_code, 429)
        R.eq('锁定 code 4003', get_json(r)['code'], 4003)
        r = c.post(srv.base + '/api/v1/auth/login', json={'username': lock_user, 'password': 'secret123'},
                   headers={'X-Forwarded-For': '10.0.0.2'})
        R.eq('锁定按 (用户名, IP) 隔离：另一 IP 可登录', r.status_code, 200)
        time.sleep(3.3)
        r = c.post(srv.base + '/api/v1/auth/login', json={'username': lock_user, 'password': 'secret123'},
                   headers={'X-Forwarded-For': '10.0.0.1'})
        R.eq('锁定到期后自动解锁', r.status_code, 200)

        # ---- TCP 基础（line framing） ----
        sock = socket.create_connection(('127.0.0.1', tcp_port), timeout=5)
        reader = sock.makefile('rb')
        try:
            sock.sendall(b'hello\n')
            R.eq('TCP line 回显', reader.readline(), b'echo:hello\n')
            sock.sendall(b'a\nb\n')
            R.eq('TCP 粘包第一帧', reader.readline(), b'echo:a\n')
            R.eq('TCP 粘包第二帧', reader.readline(), b'echo:b\n')
            sock.sendall(b'bo')
            time.sleep(0.05)
            sock.sendall(b'om\n')
            time.sleep(0.2)
            sock.sendall(b'still-alive\n')
            R.eq('TCP handler 异常不打断连接', reader.readline(), b'echo:still-alive\n')
        finally:
            reader.close()
            sock.close()

        idle = socket.create_connection(('127.0.0.1', tcp_port), timeout=5)
        try:
            idle.settimeout(4)
            t0 = time.time()
            data = idle.recv(16)
            R.eq('TCP 空闲超时断开', data, b'')
            R.check('TCP 空闲超时约 2s', 1.5 <= time.time() - t0 <= 4.5, f'{time.time() - t0:.2f}s')
        finally:
            idle.close()

        # ---- UDP 基础 ----
        R.eq('UDP 回显', _udp_roundtrip(udp_port, b'hello'), b'echo:hello')
        R.eq('UDP 返回 None 不回发', _udp_roundtrip(udp_port, b'none'), None)
        R.eq('UDP 返回 str 不回发（告警）', _udp_roundtrip(udp_port, b'str'), None)

        # ---- 日志 ----
        logs = srv.logs()
        R.contains('日志包含 request_id', logs, '[rid:')
        R.contains('TCP handler 异常被记录', logs, 'planned tcp handler error')
        R.contains('UDP 非 bytes 返回被告警', logs, 'only bytes will be auto-replied')

    # 404 指标标签防高基数（服务已停止前 /metrics 已抓取，这里只检查日志/无异常）


# --------------------------------------------------------------------------
# 限流：memory / redis / 降级
# --------------------------------------------------------------------------

def test_rate_limit_memory():
    port = free_port()
    env = base_env(SERVER_PORT=port, RATE_LIMIT_ENABLED='true', RATE_LIMIT_PER_MINUTE='5',
                   RATE_LIMIT_STORE='memory', AUTH_ENABLED='false')
    with Server('http-rate-memory', port, env) as srv:
        c = requests.Session()
        # 豁免端点不计数
        for path in ('/metrics', '/api/v1/healthz', '/api/v1/readyz', '/api/v1/health'):
            codes = [c.get(srv.base + path, headers={'X-Forwarded-For': '10.1.0.9'}).status_code
                     for _ in range(8)]
            R.check(f'限流豁免 {path}', all(code == 200 for code in codes), codes)

        # 路径级：同一 IP 同一路径 5 次后 429
        codes = [c.get(srv.base + '/hello', headers={'X-Forwarded-For': '10.1.0.1'}).status_code
                 for _ in range(6)]
        R.eq('路径级限流前 5 次 200', codes[:5], [200] * 5)
        R.eq('路径级限流第 6 次 429', codes[5], 429)
        r = c.get(srv.base + '/hello', headers={'X-Forwarded-For': '10.1.0.1'})
        R.eq('限流响应统一格式 code=-1', get_json(r)['code'], -1)

        # IP 级：不同路径也会被 IP 总配额拦住
        codes = [c.get(srv.base + f'/p{i}', headers={'X-Forwarded-For': '10.1.0.2'}).status_code
                 for i in range(6)]
        R.eq('IP 级前 5 次 200', codes[:5], [200] * 5)
        R.eq('IP 级第 6 次（不同路径）429', codes[5], 429)

        # XFF 取最右：左侧伪造值变化不应重置计数
        codes = [c.get(srv.base + '/hello',
                       headers={'X-Forwarded-For': f'1.1.1.{i}, 10.1.0.3'}).status_code
                 for i in range(5)]
        R.eq('XFF 右解析：前 5 次 200', codes, [200] * 5)
        r = c.get(srv.base + '/hello', headers={'X-Forwarded-For': '9.9.9.9, 10.1.0.3'})
        R.eq('XFF 左侧伪造不绕过限流', r.status_code, 429)

        # OPTIONS 预检不计数
        codes = [c.options(srv.base + '/api/v1/echo',
                           headers={'X-Forwarded-For': '10.1.0.4',
                                    'Origin': 'https://x.example',
                                    'Access-Control-Request-Method': 'POST'}).status_code
                 for _ in range(8)]
        R.check('OPTIONS 预检豁免限流', all(code == 200 for code in codes), codes)


def test_rate_limit_redis():
    url = redis_url(15)
    if not url:
        print('  SKIP  Redis 未配置，跳过 Redis 限流测试')
        return
    port = free_port()
    env = base_env(SERVER_PORT=port, RATE_LIMIT_ENABLED='true', RATE_LIMIT_PER_MINUTE='3',
                   RATE_LIMIT_STORE='redis', REDIS_URL=url, AUTH_ENABLED='false')
    try:
        with Server('http-rate-redis', port, env) as srv:
            c = requests.Session()
            ip = '10.9.9.1'
            codes = [c.get(srv.base + '/hello', headers={'X-Forwarded-For': ip}).status_code
                     for _ in range(4)]
            R.eq('Redis 限流前 3 次 200', codes[:3], [200] * 3)
            R.eq('Redis 限流第 4 次 429', codes[3], 429)
            import redis
            rc = redis.Redis.from_url(url, decode_responses=True)
            try:
                R.check('Redis 中写入限流计数', rc.exists(f'rate:{ip}') == 1)
            finally:
                rc.close()
    finally:
        cleanup_redis()


def test_rate_limit_degraded():
    """REDIS_URL 不可达 + RATE_LIMIT_STORE=redis：回退内存计数仍生效。"""
    port = free_port()
    env = base_env(SERVER_PORT=port, RATE_LIMIT_ENABLED='true', RATE_LIMIT_PER_MINUTE='2',
                   RATE_LIMIT_STORE='redis', REDIS_URL='redis://127.0.0.1:6399/0',
                   AUTH_ENABLED='false')
    with Server('http-rate-degraded', port, env) as srv:
        c = requests.Session()
        codes = [c.get(srv.base + '/hello', headers={'X-Forwarded-For': '10.9.9.2'}).status_code
                 for _ in range(3)]
        R.eq('Redis 不可达时内存降级前 2 次 200', codes[:2], [200, 200])
        R.eq('Redis 不可达时内存降级第 3 次 429', codes[2], 429)
        r = c.get(srv.base + '/api/v1/health')
        R.eq('health 中 redis=error', get_json(r)['data']['redis'], 'error')
        r = c.get(srv.base + '/api/v1/readyz')
        R.eq('readyz 依赖故障 503', r.status_code, 503)
        R.eq('readyz code 5030', get_json(r)['code'], 5030)


# --------------------------------------------------------------------------
# 认证：Redis 多进程共享 / SQLite 持久化 / MySQL 只读
# --------------------------------------------------------------------------

def test_auth_redis_sharing():
    """两个进程共享 Redis token + 共享 SQLite 用户表：跨进程登录态一致。"""
    url = redis_url(15)
    if not url:
        print('  SKIP  Redis 未配置，跳过跨进程 token 共享测试')
        return
    db_path = TMP_DIR / f'shared_{uuid.uuid4().hex[:8]}.db'
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            'CREATE TABLE user ('
            ' uid VARCHAR(64) NOT NULL PRIMARY KEY,'
            ' username VARCHAR(128) NOT NULL UNIQUE,'
            ' passwd VARCHAR(256) NOT NULL,'
            ' create_time DATETIME NOT NULL'
            ');'
            'CREATE INDEX ix_user_username ON user (username);'
        )
        conn.commit()
    finally:
        conn.close()
    uri = 'sqlite:///' + db_path.as_posix()
    port_a, port_b = free_port(), free_port()
    env_a = base_env(SERVER_PORT=port_a, AUTH_ENABLED='true', REDIS_URL=url,
                     AUTH_STORE='sqlalchemy', SQLALCHEMY_URI=uri,
                     DB_REFLECT_ON_START='false', AUTH_PBKDF2_ITERATIONS='10000')
    env_b = base_env(SERVER_PORT=port_b, AUTH_ENABLED='true', REDIS_URL=url,
                     AUTH_STORE='sqlalchemy', SQLALCHEMY_URI=uri,
                     DB_REFLECT_ON_START='false', AUTH_PBKDF2_ITERATIONS='10000')
    user = 'redis_' + uuid.uuid4().hex[:8]
    try:
        with Server('http-auth-redis-a', port_a, env_a) as a, \
                Server('http-auth-redis-b', port_b, env_b) as b:
            ca, cb = requests.Session(), requests.Session()
            r = ca.post(a.base + '/api/v1/auth/register', json={'username': user, 'password': 'secret123'})
            R.eq('Redis 共享：A 注册 200', r.status_code, 200)
            r = ca.post(a.base + '/api/v1/auth/login', json={'username': user, 'password': 'secret123'})
            data = get_json(r)['data']
            token, refresh = data['token'], data['refresh_token']
            R.eq('Redis 共享：A 登录 200', r.status_code, 200)
            r = cb.get(b.base + '/api/v1/auth/me', headers={'X-AUTH-TOKEN': token})
            R.eq('Redis 共享：B 校验 A 签发的 token 200', r.status_code, 200)
            r = cb.post(b.base + '/api/v1/auth/logout', headers={'X-AUTH-TOKEN': token},
                        json={'refresh_token': refresh})
            R.eq('Redis 共享：B 登出 200', r.status_code, 200)
            r = ca.get(a.base + '/api/v1/auth/me', headers={'X-AUTH-TOKEN': token})
            R.eq('Redis 共享：B 登出后 A 上 token 失效', r.status_code, 401)
            import redis
            rc = redis.Redis.from_url(url, decode_responses=True)
            try:
                R.check('Redis 中 token 已删除', rc.exists(f'auth:token:{token}') == 0)
                R.check('Redis 中 refresh 已删除', rc.exists(f'auth:refresh:{refresh}') == 0)
            finally:
                rc.close()
    finally:
        cleanup_redis()
        db_path.unlink(missing_ok=True)


def test_sqlite_auth_persistence():
    db_path = TMP_DIR / f'auth_{uuid.uuid4().hex[:8]}.db'
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            'CREATE TABLE user ('
            ' uid VARCHAR(64) NOT NULL PRIMARY KEY,'
            ' username VARCHAR(128) NOT NULL UNIQUE,'
            ' passwd VARCHAR(256) NOT NULL,'
            ' create_time DATETIME NOT NULL'
            ');'
            'CREATE INDEX ix_user_username ON user (username);'
        )
        conn.commit()
    finally:
        conn.close()

    uri = 'sqlite:///' + db_path.as_posix()
    port = free_port()
    env = base_env(SERVER_PORT=port, SQLALCHEMY_URI=uri, AUTH_STORE='sqlalchemy',
                   AUTH_ENABLED='true', DB_REFLECT_ON_START='false',
                   AUTH_PBKDF2_ITERATIONS='10000')
    user = 'sqlite_' + uuid.uuid4().hex[:8]
    try:
        with Server('http-auth-sqlite-1', port, env) as srv:
            c = requests.Session()
            r = c.get(srv.base + '/api/v1/readyz')
            R.eq('SQLite 数据库 readyz 200', r.status_code, 200)
            R.eq('SQLite db ok', get_json(r)['data'].get('db'), 'ok')
            r = c.post(srv.base + '/api/v1/auth/register', json={'username': user, 'password': 'secret123'})
            R.eq('SQLite 存储：注册 200', r.status_code, 200)
            r = c.post(srv.base + '/api/v1/auth/login', json={'username': user, 'password': 'secret123'})
            R.eq('SQLite 存储：登录 200', r.status_code, 200)
            token = get_json(r)['data']['token']
            r = c.get(srv.base + '/api/v1/auth/me', headers={'X-AUTH-TOKEN': token})
            R.eq('SQLite 存储：/me 200', r.status_code, 200)

        # 重启后用户仍在（真正持久化，而非进程内存储）
        with Server('http-auth-sqlite-2', port, env) as srv:
            c = requests.Session()
            r = c.post(srv.base + '/api/v1/auth/login', json={'username': user, 'password': 'secret123'})
            R.eq('SQLite 存储：重启后仍可登录', r.status_code, 200)
            r = c.get(srv.base + '/api/v1/auth/me',
                      headers={'X-AUTH-TOKEN': get_json(r)['data']['token']})
            R.eq('SQLite 存储：重启后 /me 200', r.status_code, 200)

        conn = sqlite3.connect(str(db_path))
        try:
            count = conn.execute('SELECT COUNT(*) FROM user WHERE username=?', (user,)).fetchone()[0]
            row = conn.execute('SELECT uid, passwd FROM user WHERE username=?', (user,)).fetchone()
            R.eq('SQLite 中只有 1 行', count, 1)
            R.check('SQLite 密码为 PBKDF2 哈希', row and row[1].count('$') == 2 and 'secret123' not in row[1])
        finally:
            conn.close()
    finally:
        db_path.unlink(missing_ok=True)


def test_mysql_readonly():
    env_file = parse_dotenv()
    uri = env_file.get('SQLALCHEMY_URI')
    if not uri or not uri.startswith('mysql'):
        print('  SKIP  MySQL 未配置，跳过真实 MySQL 连通性测试')
        return
    port = free_port()
    env = base_env(SERVER_PORT=port, SQLALCHEMY_URI=uri, AUTH_STORE='memory',
                   AUTH_ENABLED='false', DB_REFLECT_ON_START='false',
                   DB_POOL_SIZE='2', DB_POOL_TIMEOUT='5')
    with Server('http-mysql', port, env) as srv:
        c = requests.Session()
        r = c.get(srv.base + '/api/v1/health')
        R.eq('MySQL health 200', r.status_code, 200)
        R.eq('MySQL health db=ok', get_json(r)['data']['db'], 'ok')
        r = c.get(srv.base + '/api/v1/readyz')
        R.eq('MySQL readyz 200', r.status_code, 200)
        R.eq('MySQL readyz db=ok', get_json(r)['data'].get('db'), 'ok')


# --------------------------------------------------------------------------
# 进程内：应用工厂 / 配置注入 / 错误处理 / 安全工具
# --------------------------------------------------------------------------

def test_in_process():
    from flask import Flask

    from flask_server.app import create_app
    from flask_server.component.auth import _is_exempt_path
    from flask_server.config import Config
    from flask_server.module.local_file_storage import LocalFileStorage
    from flask_server.util import CommonUtil, DataEncryptUtil, KeyGenerator
    from flask_server.util.key_generator import SnowflakeIdWorker

    # 配置注入：两个 app 的请求期配置隔离
    app_v9 = create_app({'TESTING': True}, framework_config=Config({'api_version': 'v9'}))
    app_v10 = create_app({'TESTING': True}, framework_config=Config({'api_version': 'v10'}))
    R.eq('注入 app v9 health version', get_json(app_v9.test_client().get('/api/v1/health'))['data']['version'], 'v9')
    R.eq('注入 app v10 health version', get_json(app_v10.test_client().get('/api/v1/health'))['data']['version'], 'v10')
    from flask_server import app as default_app
    R.check('默认 app 不受注入影响',
            get_json(default_app.test_client().get('/api/v1/health'))['data']['version'] != 'v9')

    # 按 app 隔离 AUTH_ENABLED
    app_on = create_app({'TESTING': True}, framework_config=Config({'auth_enabled': True}))
    app_off = create_app({'TESTING': True}, framework_config=Config({'auth_enabled': False}))
    r = app_on.test_client().post('/api/v1/echo', json={'message': 'x'})
    R.eq('注入 auth_enabled=true 拦截', r.status_code, 401)
    r = app_off.test_client().post('/api/v1/echo', json={'message': 'x'})
    R.eq('注入 auth_enabled=false 放行', r.status_code, 200)
    R.check('默认配置未被注入改写', default_app.framework_config.auth_enabled is False)

    # 生产/开发 500 脱敏
    for env_name, expected in (('development', 'secret-detail'), ('production', '服务器内部错误')):
        app = create_app({'TESTING': False, 'PROPAGATE_EXCEPTIONS': False},
                         framework_config=Config({'app_env': env_name}))
        @app.route('/boom')
        def boom():
            raise RuntimeError('secret-detail')
        r = app.test_client().get('/boom')
        body = get_json(r)
        R.eq(f'{env_name} 异常 500', r.status_code, 500)
        R.eq(f'{env_name} 500 code', body['code'], -1)
        R.eq(f'{env_name} 500 详情策略', body['data'], expected)

    # 413 统一 JSON
    app = create_app({'TESTING': False}, framework_config=Config({'max_content_length': 100}))
    r = app.test_client().post('/api/v1/echo', data=b'x' * 500,
                               headers={'Content-Type': 'application/json'})
    R.eq('超限 body 413', r.status_code, 413)
    R.eq('413 统一格式 code', get_json(r)['code'], -1)

    # HSTS 可配置
    app = create_app({'TESTING': True},
                     framework_config=Config({'hsts_enabled': True, 'hsts_max_age': 123}))
    r = app.test_client().get('/hello')
    R.eq('HSTS 头', r.headers.get('Strict-Transport-Security'), 'max-age=123; includeSubDomains')

    # 自定义 Swagger CDN：CSP 自动放行
    app = create_app({'TESTING': True},
                     framework_config=Config({'swagger_ui_url': 'https://swagger.internal.example/ui/'}))
    r = app.test_client().get('/docs')
    csp = r.headers.get('Content-Security-Policy', '')
    R.check('自定义 Swagger CDN 被 CSP 放行', 'https://swagger.internal.example' in csp, csp)

    # 豁免路径精确匹配
    R.check('/docs 豁免', _is_exempt_path('/docs'))
    R.check('/docs/x 豁免', _is_exempt_path('/docs/x'))
    R.check('/docsanything 不豁免', not _is_exempt_path('/docsanything'))
    R.check('/api/v1/auth/login 豁免', _is_exempt_path('/api/v1/auth/login'))
    R.check('/api/v1/authx/login 不豁免', not _is_exempt_path('/api/v1/authx/login'))
    R.check('大小写不敏感豁免', _is_exempt_path('/API/V1/AUTH/LOGIN'))

    # X-Forwarded-For 解析
    class _Req:
        def __init__(self, remote, xff=None):
            self.remote_addr = remote
            self.headers = {'X-Forwarded-For': xff} if xff is not None else {}

    trusted = ['127.0.0.1', '10.0.0.0/8']
    R.eq('XFF 取最右非可信 IP', CommonUtil.get_real_ip(_Req('127.0.0.1', '1.2.3.4, 8.8.8.8'), trusted), '8.8.8.8')
    R.eq('XFF 右侧可信则继续向左', CommonUtil.get_real_ip(_Req('127.0.0.1', '1.2.3.4, 10.0.0.1'), trusted), '1.2.3.4')
    R.eq('XFF 跳过可信代理', CommonUtil.get_real_ip(_Req('127.0.0.1', '1.2.3.4, 127.0.0.1'), trusted), '1.2.3.4')
    R.eq('不可信来源忽略 XFF', CommonUtil.get_real_ip(_Req('8.8.8.8', '1.2.3.4'), trusted), '8.8.8.8')
    R.eq('XFF 垃圾值跳过', CommonUtil.get_real_ip(_Req('127.0.0.1', 'garbage, 127.0.0.1'), trusted), '127.0.0.1')
    R.eq('CIDR 可信代理', CommonUtil.get_real_ip(_Req('10.1.2.3', '1.2.3.4, 10.9.8.7'), trusted), '1.2.3.4')

    # 日志脱敏 / 密码哈希 / ID
    R.eq('mask_uri 含 @ 密码', CommonUtil.mask_uri('mysql://u:p@ss@host/db'), 'mysql://u:***@host/db')
    R.eq('mask_uri 空用户名', CommonUtil.mask_uri('redis://:pass@host:6379/0'), 'redis://:***@host:6379/0')
    R.eq('mask_uri 无密码原样', CommonUtil.mask_uri('redis://host:6379/0'), 'redis://host:6379/0')
    h = DataEncryptUtil.pbkdf2_hmac('secret123', iterations=10000)
    R.check('PBKDF2 正确密码校验', DataEncryptUtil.verify_pbkdf2('secret123', h))
    R.check('PBKDF2 错误密码拒绝', not DataEncryptUtil.verify_pbkdf2('wrong', h))
    R.check('PBKDF2 损坏哈希不抛异常', not DataEncryptUtil.verify_pbkdf2('x', 'bad$hash'))
    R.check('PBKDF2 旧格式 salt$hash 兼容',
            DataEncryptUtil.verify_pbkdf2('secret123', h.split('$')[0] + '$' +
                                          __import__('hashlib').pbkdf2_hmac(
                                              'sha256', b'secret123', bytes.fromhex(h.split('$')[0]), 100000).hex()))
    uuids = {KeyGenerator.generate_uuid7() for _ in range(200)}
    R.eq('UUIDv7 200 个唯一', len(uuids), 200)
    R.check('UUIDv7 版本位为 7', all(u[14] == '7' for u in uuids))
    w = SnowflakeIdWorker(3, 5)
    ids = {str(w.next_id()) for _ in range(500)}
    R.eq('雪花 ID 500 个唯一', len(ids), 500)

    # 本地文件存储：读写 + 路径穿越
    root = TMP_DIR / f'storage_{uuid.uuid4().hex[:6]}'
    root.mkdir(parents=True, exist_ok=True)
    storage = LocalFileStorage(str(root))
    storage.save('a/b.txt', b'hello')
    R.eq('文件存储写入/读取', storage.load('a/b.txt'), b'hello')
    R.check('文件存储 exists', storage.exists('a/b.txt'))
    R.raises('文件存储路径穿越拒绝', lambda: storage.load('../outside.txt'), ValueError)
    storage.delete('a/b.txt')
    R.check('文件存储删除', not storage.exists('a/b.txt'))

    # 内存缓存 TTL / incr
    from flask_server.module.simple_memory_cache import SimpleMemoryCache
    mc = SimpleMemoryCache()
    mc.set('k', 'v', ttl=0.2)
    R.eq('内存缓存读取', mc.get('k'), 'v')
    time.sleep(0.3)
    R.eq('内存缓存 TTL 过期', mc.get('k'), None)
    R.eq('内存缓存 incr', [mc.incr('n'), mc.incr('n')], [1, 2])

    # 请求 ID 清洗（控制字符/截断）
    from flask_server.app import _sanitize_request_id
    R.eq('request_id 去控制字符', _sanitize_request_id('a\x01b\x02c'), 'abc')
    R.eq('request_id 截断 64', len(_sanitize_request_id('z' * 100)), 64)
    R.eq('request_id 全非法回退 None', _sanitize_request_id('\x01\x02'), None)


def main():
    install_probe_modules()
    try:
        test_main_server()
        test_rate_limit_memory()
        test_rate_limit_redis()
        test_rate_limit_degraded()
        test_auth_redis_sharing()
        test_sqlite_auth_persistence()
        test_mysql_readonly()
        test_in_process()
    except Exception as e:  # noqa: BLE001
        dump_exception('e2e_http', e)
        R.check('e2e_http 未发生未捕获异常', False, f'{type(e).__name__}: {e}')
    finally:
        remove_probe_modules()
        cleanup_redis()
    return 0 if R.summary() else 1


if __name__ == '__main__':
    raise SystemExit(main())
