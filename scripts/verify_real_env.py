#!/usr/bin/env python
"""真实环境自检脚本：验证脚手架在真实 MySQL / Redis 上的可用性与正确性

用法：
    python scripts/verify_real_env.py                        # 全流程（推荐）
    python scripts/verify_real_env.py --skip-pytest          # 跳过 pytest 集成测试
    python scripts/verify_real_env.py --skip-migrate         # 跳过 Flask-Migrate 建表
    python scripts/verify_real_env.py --skip-boot            # 跳过真实 server.py 启动冒烟
    python scripts/verify_real_env.py --keep-migrations      # 保留生成的 migrations/ 目录
    python scripts/verify_real_env.py --test-db flask_scaffold_test

配置：
    - MySQL / Redis 连接默认读取项目 .env（SQLALCHEMY_URI / REDIS_URL）
    - 可用 --mysql-uri / --redis-url 显式覆盖（优先级高于 .env）
    - 集成测试库名默认 = 应用库名 + "_test"，可用 --test-db 指定

验证内容：
    1. 连接探测（MySQL SELECT 1 + 版本 / Redis AUTH + PING）
    2. 确保数据库存在（CREATE DATABASE IF NOT EXISTS，utf8mb4）
    3. pytest 集成测试（sqlalchemy_trans 提交/回滚、CRUD，走真实 MySQL）
    4. Flask-Migrate 建表（init/migrate/upgrade → 远端 user 表生成）
    5. HTTP 全流程（注册/登录/me/refresh 轮换/登出/防爆破 429/限流 429）
    6. 数据落点断言（user 行在 MySQL、auth:token 键在 Redis）
    7. 真实 server.py 启动冒烟（healthz/readyz/health）

安全：脚本不打印密码；连接串中的密码在输出时自动脱敏。
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.request

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 脚本以 `python scripts/verify_real_env.py` 运行时，sys.path[0] 是 scripts/，
# 需手动将项目根加入，才能 import flask_server
sys.path.insert(0, PROJECT_ROOT)


# ------------------------- 工具 -------------------------


def log(msg):
    print(f'[verify] {msg}')


def step_ok(name):
    print(f'  [PASS] {name}')


def step_fail(name, detail=''):
    print(f'  [FAIL] {name} {detail}')


def mask_uri(uri):
    """脱敏连接串中的密码，避免输出泄漏（密码可含 @，脱敏到凭据分隔符）"""
    import re
    return re.sub(r'(://[^:/@]*:)[^/?#]*(@)', r'\1***\2', uri)


def check_db_identifier(name):
    """校验库名是合法 MySQL 标识符（CREATE/DROP DATABASE 不支持参数化，防标识符注入）"""
    import re
    if not re.fullmatch(r'[A-Za-z0-9_]+', name or ''):
        raise ValueError(f'illegal database name: {name!r}')


# ------------------------- 连接与建库 -------------------------


def ensure_mysql(args):
    """探测 MySQL 并确保目标数据库存在"""
    import pymysql
    from sqlalchemy.engine import make_url

    url = make_url(args.mysql_uri)
    check_db_identifier(url.database)
    conn = pymysql.connect(
        host=url.host, port=url.port or 3306,
        user=url.username, password=url.password or '',
        charset='utf8mb4', connect_timeout=8, read_timeout=8,
    )
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT VERSION()')
            version = cur.fetchone()[0]
            cur.execute(
                f'CREATE DATABASE IF NOT EXISTS `{url.database}` '
                f'CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
        conn.commit()
        log(f'MySQL {version} ok, database `{url.database}` ensured')
    finally:
        conn.close()
    return url


def ensure_redis(args):
    """探测 Redis"""
    import redis
    client = redis.Redis.from_url(
        args.redis_url, decode_responses=True,
        socket_connect_timeout=8, socket_timeout=8,
    )
    if not client.ping():
        raise RuntimeError('Redis PING failed')
    log(f'Redis {client.info("server").get("redis_version")} ok')
    return client


# ------------------------- 步骤实现 -------------------------


def step_pytest(args):
    """CI 对齐的 SQLAlchemy 集成测试（真实 MySQL）"""
    test_db_uri = f'{args.mysql_uri.rsplit("/", 1)[0]}/{args.test_db}?charset=utf8mb4'
    env = dict(os.environ)
    env['TEST_DB_URI'] = test_db_uri
    log(f'pytest integration (TEST_DB_URI={mask_uri(test_db_uri)})')
    result = subprocess.run(
        [sys.executable, '-m', 'pytest',
         'tests/test_sqlalchemy_integration.py', 'tests/test_sqlite.py', '-q'],
        cwd=PROJECT_ROOT, env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f'pytest integration failed (rc={result.returncode})')
    step_ok('pytest integration')


def _table_exists(url, table):
    """检查远端 MySQL 中表是否存在"""
    import pymysql
    conn = pymysql.connect(
        host=url.host, port=url.port or 3306,
        user=url.username, password=url.password or '',
        database=url.database, charset='utf8mb4', connect_timeout=8, read_timeout=8,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema=%s AND table_name=%s", (url.database, table))
            return cur.fetchone()[0] > 0
    finally:
        conn.close()


def step_migrate(args):
    """Flask-Migrate 建表：init → migrate → upgrade，验证 user 表在远端生成

    幂等：若 user 表已存在（上次运行已建），跳过迁移直接验证，避免
    alembic_version 指向旧 revision 导致 upgrade 失败。
    """
    from sqlalchemy.engine import make_url
    url = make_url(args.mysql_uri)
    if _table_exists(url, 'user'):
        log('table `user` already exists, skipping migrations')
        step_ok(f'migrations: `user` table present on real MySQL ({url.database})')
        return
    migrations_dir = os.path.join(PROJECT_ROOT, 'migrations')
    if os.path.exists(migrations_dir):
        shutil.rmtree(migrations_dir)
    try:
        for cmd, label in (
            (['init'], 'flask db init'),
            (['migrate', 'create user table'], 'flask db migrate'),
            (['upgrade'], 'flask db upgrade'),
        ):
            log(f'{label} ...')
            result = subprocess.run(
                [sys.executable, os.path.join(PROJECT_ROOT, 'scripts', 'db.py')] + cmd,
                cwd=PROJECT_ROOT,
            )
            if result.returncode != 0:
                raise RuntimeError(f'{label} failed (rc={result.returncode})')
        if not _table_exists(url, 'user'):
            raise RuntimeError(f'table `user` not found in database `{url.database}`')
        step_ok(f'migrations: `user` table created on real MySQL ({url.database})')
    finally:
        if not args.keep_migrations:
            for p in ('migrations', 'alembic.ini'):
                full = os.path.join(PROJECT_ROOT, p)
                if os.path.isdir(full):
                    shutil.rmtree(full)
                elif os.path.isfile(full):
                    os.remove(full)
            log('migrations/ + alembic.ini removed (use --keep-migrations to keep)')


def step_http_flow(args, redis_client, mysql_url):
    """HTTP 全流程：注册/登录/me/refresh 轮换/登出/防爆破/限流 + 数据落点断言"""
    # 限流阈值说明：限流现为「路径级 + IP 级总配额」双层（IP 级兜底防随机路径绕过）。
    # 本流程共 ~31 个非探针请求（认证/防爆破），阈值必须大于该数才能让流程通过，
    # 再由后续 burst（>阈值）触发 429。100/min 下第 ~101 个总请求被 IP 级配额拦截。
    # 必须在导入 flask_server（读配置）之前设置；RATE_LIMIT_ENABLED 显式开启，
    # 不再依赖 .env 是否启用限流（修复前 .env 未开启时限流步骤必然 FAIL）
    os.environ['RATE_LIMIT_ENABLED'] = 'true'
    os.environ['RATE_LIMIT_PER_MINUTE'] = '100'
    from flask_server import app

    client = app.test_client()
    suffix = str(int(time.time()))
    username = f'verify_{suffix}'
    password = 'secret123'
    results = []

    def check(name, cond, detail=''):
        results.append((name, cond, detail))
        if cond:
            step_ok(name)
        else:
            step_fail(name, detail)

    # 注册 + 重复注册
    r = client.post('/api/v1/auth/register', json={'username': username, 'password': password})
    check('register', r.status_code == 200 and r.get_json()['code'] == 0, f'status={r.status_code}')
    r = client.post('/api/v1/auth/register', json={'username': username, 'password': 'other123'})
    check('register duplicate -> 400/4001',
          r.status_code == 400 and r.get_json()['code'] == 4001, f'status={r.status_code}')

    # 登录 → token 落远端 Redis
    r = client.post('/api/v1/auth/login', json={'username': username, 'password': password})
    check('login', r.status_code == 200 and 'token' in r.get_json().get('data', {}),
          f'status={r.status_code}')
    token = r.get_json()['data']['token']
    refresh_token = r.get_json()['data']['refresh_token']
    access_key = f'auth:token:{token}'
    redis_client.get(access_key)  # 触发一次读取验证连通
    exists = redis_client.exists(access_key) > 0
    check('access token stored in remote Redis', exists, 'auth:token:*')

    # me
    r = client.get('/api/v1/auth/me')
    check('me without token -> 401',
          r.status_code == 401 and r.get_json()['code'] == 4002, f'status={r.status_code}')
    r = client.get('/api/v1/auth/me', headers={'X-AUTH-TOKEN': token})
    data = r.get_json().get('data', {})
    check('me with token', r.status_code == 200 and data.get('username') == username,
          f'status={r.status_code}, username={data.get("username")}')
    check('me hides passwd', 'passwd' not in data)

    # refresh 轮换
    r = client.post('/api/v1/auth/refresh', json={'refresh_token': refresh_token})
    check('refresh', r.status_code == 200 and 'token' in r.get_json().get('data', {}),
          f'status={r.status_code}')
    r = client.post('/api/v1/auth/refresh', json={'refresh_token': refresh_token})
    check('refresh old token rejected (rotation)',
          r.status_code == 401 and r.get_json()['code'] == 4002, f'status={r.status_code}')

    # 登出
    r = client.post('/api/v1/auth/logout', headers={'X-AUTH-TOKEN': token})
    check('logout', r.status_code == 200, f'status={r.status_code}')
    r = client.get('/api/v1/auth/me', headers={'X-AUTH-TOKEN': token})
    check('me after logout -> 401', r.status_code == 401, f'status={r.status_code}')

    # 防爆破：连续失败 5 次后锁定，正确密码也 429
    locked_user = f'locked_{suffix}'
    client.post('/api/v1/auth/register', json={'username': locked_user, 'password': password})
    for _ in range(5):
        client.post('/api/v1/auth/login', json={'username': locked_user, 'password': 'wrong-pass'})
    r = client.post('/api/v1/auth/login', json={'username': locked_user, 'password': password})
    check('brute-force lock -> 429/4003',
          r.status_code == 429 and r.get_json()['code'] == 4003, f'status={r.status_code}')

    # 限流：burst 触发 429（IP 级总配额在第 ~101 个总请求触发，含认证流程的 ~31 个）
    got_429 = False
    for _ in range(110):
        r = client.get('/hello')
        if r.status_code == 429:
            got_429 = True
            break
    check('rate limit (redis store) -> 429', got_429)

    # 就绪探针（真实 DB/Redis 连通）
    r = client.get('/api/v1/readyz')
    check('readyz -> 200 (db+redis ok)', r.status_code == 200, f'status={r.status_code}')
    r = client.get('/api/v1/health')
    body = r.get_json().get('data', {})
    check('health shows db/redis ok',
          body.get('db') == 'ok' and body.get('redis') == 'ok', f'body={body}')

    # 用户数据落点：真实 MySQL 表
    import pymysql
    url = mysql_url
    conn = pymysql.connect(
        host=url.host, port=url.port or 3306,
        user=url.username, password=url.password or '',
        database=url.database, charset='utf8mb4', connect_timeout=8, read_timeout=8,
    )
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT uid, username FROM `user` WHERE username=%s', (username,))
            row = cur.fetchone()
    finally:
        conn.close()
    check('user row persisted in real MySQL', row is not None, f'row={row}')

    failed = [n for n, ok, _ in results if not ok]
    if failed:
        raise RuntimeError(f'HTTP flow failed: {failed}')
    log(f'HTTP flow: {len(results) - len(failed)}/{len(results)} checks passed')


def step_reflect(args):
    """DB_REFLECT_ON_START=true 反射容错：临时空库 + 反射启动，health 应 db=ok"""
    import json
    import pymysql
    from sqlalchemy.engine import make_url

    url = make_url(args.mysql_uri)
    reflect_db = args.reflect_db
    check_db_identifier(reflect_db)
    conn = pymysql.connect(
        host=url.host, port=url.port or 3306,
        user=url.username, password=url.password or '',
        charset='utf8mb4', connect_timeout=8, read_timeout=8,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                f'CREATE DATABASE IF NOT EXISTS `{reflect_db}` '
                f'CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
        conn.commit()
    finally:
        conn.close()

    reflect_uri = f'{args.mysql_uri.rsplit("/", 1)[0]}/{reflect_db}?charset=utf8mb4'
    env = dict(os.environ)
    env['SQLALCHEMY_URI'] = reflect_uri
    env['DB_REFLECT_ON_START'] = 'true'
    env['SERVER_PORT'] = str(args.reflect_port)
    env['DEBUG'] = 'false'
    proc = subprocess.Popen(
        [sys.executable, 'server.py'], cwd=PROJECT_ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        base = f'http://127.0.0.1:{args.reflect_port}'
        deadline = time.time() + 40
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f'{base}/api/v1/healthz', timeout=2) as resp:
                    if resp.status == 200:
                        break
            except Exception:
                time.sleep(1)
        else:
            raise RuntimeError('reflect boot: server.py failed to become healthy within 40s')
        with urllib.request.urlopen(f'{base}/api/v1/health', timeout=5) as resp:
            body = json.loads(resp.read().decode('utf-8'))
            assert body.get('data', {}).get('db') == 'ok', f'health body={body}'
        step_ok(f'reflect on real MySQL (empty db `{reflect_db}`) boots, health db=ok')
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        conn = pymysql.connect(
            host=url.host, port=url.port or 3306,
            user=url.username, password=url.password or '',
            charset='utf8mb4', connect_timeout=8, read_timeout=8,
        )
        try:
            with conn.cursor() as cur:
                cur.execute(f'DROP DATABASE IF EXISTS `{reflect_db}`')
            conn.commit()
        finally:
            conn.close()
        log(f'temporary db `{reflect_db}` dropped')


def step_boot_smoke(args):
    """真实 server.py 启动冒烟（healthz/readyz/health）"""
    import json

    port = args.boot_port
    env = dict(os.environ)
    env['SERVER_PORT'] = str(port)
    env['DEBUG'] = 'false'   # 禁用 debug reloader，避免双进程残留
    proc = subprocess.Popen(
        [sys.executable, 'server.py'], cwd=PROJECT_ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f'http://127.0.0.1:{port}'
    try:
        deadline = time.time() + 40
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f'{base}/api/v1/healthz', timeout=2) as resp:
                    if resp.status == 200:
                        break
            except Exception:
                time.sleep(1)
        else:
            raise RuntimeError('server.py failed to become healthy within 40s')
        with urllib.request.urlopen(f'{base}/api/v1/healthz', timeout=5) as resp:
            assert resp.status == 200
        with urllib.request.urlopen(f'{base}/api/v1/readyz', timeout=5) as resp:
            assert resp.status == 200
        with urllib.request.urlopen(f'{base}/api/v1/health', timeout=5) as resp:
            body = json.loads(resp.read().decode('utf-8'))
            assert body.get('data', {}).get('db') == 'ok', f'health body={body}'
            assert body.get('data', {}).get('redis') == 'ok', f'health body={body}'
        step_ok(f'real server boot smoke on :{port} (healthz/readyz/health)')
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


# ------------------------- 主流程 -------------------------


def main():
    parser = argparse.ArgumentParser(description='真实 MySQL/Redis 环境自检')
    parser.add_argument('--mysql-uri', help='MySQL URI（默认读 .env SQLALCHEMY_URI）')
    parser.add_argument('--redis-url', help='Redis URL（默认读 .env REDIS_URL）')
    parser.add_argument('--test-db', help='集成测试库名（默认 应用库名_test）')
    parser.add_argument('--skip-pytest', action='store_true', help='跳过 pytest 集成测试')
    parser.add_argument('--skip-migrate', action='store_true', help='跳过 Flask-Migrate 建表')
    parser.add_argument('--skip-reflect', action='store_true', help='跳过反射容错冒烟')
    parser.add_argument('--skip-boot', action='store_true', help='跳过 server.py 启动冒烟')
    parser.add_argument('--keep-migrations', action='store_true', help='保留 migrations/ 目录')
    parser.add_argument('--boot-port', type=int, default=5057, help='启动冒烟端口（默认 5057）')
    parser.add_argument('--reflect-db', default=None, help='反射测试临时库名（默认 应用库_reflect）')
    parser.add_argument('--reflect-port', type=int, default=5058, help='反射冒烟端口（默认 5058）')
    args = parser.parse_args()

    # 配置：.env 由 flask_server.config 加载；显式参数优先（先写环境变量再导入）
    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT_ROOT, '.env'), override=False)
    args.mysql_uri = args.mysql_uri or os.environ.get('SQLALCHEMY_URI')
    args.redis_url = args.redis_url or os.environ.get('REDIS_URL')
    if not args.mysql_uri or not args.redis_url:
        print('[ERROR] 缺少 SQLALCHEMY_URI / REDIS_URL（配置 .env 或传参）')
        sys.exit(1)

    from sqlalchemy.engine import make_url
    app_db = make_url(args.mysql_uri).database
    args.test_db = args.test_db or f'{app_db}_test'
    args.reflect_db = args.reflect_db or f'{app_db}_reflect'

    log(f'MySQL:  {mask_uri(args.mysql_uri)}')
    log(f'Redis:  {mask_uri(args.redis_url)}')
    log(f'Test DB: {args.test_db}')

    steps = []
    try:
        mysql_url = ensure_mysql(args)
        steps.append('mysql-connect')
        redis_client = ensure_redis(args)
        steps.append('redis-connect')
        log('')

        if not args.skip_pytest:
            step_pytest(args)
            steps.append('pytest-integration')
            log('')

        if not args.skip_migrate:
            step_migrate(args)
            steps.append('migrate')
            log('')

        if not args.skip_reflect:
            step_reflect(args)
            steps.append('reflect')
            log('')

        # HTTP 全流程（导入 flask_server 前已设置 env 覆盖）
        step_http_flow(args, redis_client, mysql_url)
        steps.append('http-flow')
        log('')

        if not args.skip_boot:
            step_boot_smoke(args)
            steps.append('boot-smoke')

        print()
        print(f'[PASS] 全部步骤通过: {", ".join(steps)}')
        print(f'       测试库: {args.test_db} / 应用库: {app_db}（均未改动 halo* 库）')
        sys.exit(0)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f'[FAIL] {type(e).__name__}: {e}')
        if steps:
            print(f'       已通过步骤: {", ".join(steps)}')
        sys.exit(1)


if __name__ == '__main__':
    main()
