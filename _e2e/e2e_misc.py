"""脚手架生成、部署编排、CI/文档一致性、脚本入口等杂项端到端检查。"""

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import requests
import yaml

from common import (
    PY, ROOT, Results, Server, TMP_DIR, base_env, dump_exception, free_port,
    run_script, wait_http,
)

os.environ.update(base_env())

R = Results('Misc/Scaffold/Deploy/Docs')


def _run(args, cwd=ROOT, env=None, timeout=300):
    return subprocess.run(
        args, cwd=str(cwd), env=env or base_env(),
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )


# --------------------------------------------------------------------------
# 脚手架
# --------------------------------------------------------------------------

def test_scaffold():
    target = ROOT.parent / f'scaffold_e2e_{uuid.uuid4().hex[:8]}'
    if target.exists():
        shutil.rmtree(target)
    try:
        cp = _run([PY, 'scripts/scaffold.py', str(target), '--author', 'E2E Author'])
        R.check('scaffold 生成退出码 0', cp.returncode == 0, cp.stdout + cp.stderr)
        R.check('scaffold 目标目录存在', target.is_dir())
        R.check('scaffold 复制主应用', (target / 'flask_server' / 'app.py').is_file())
        R.check('scaffold 复制测试', (target / 'tests' / 'test_app.py').is_file())
        R.check('scaffold 复制文档', (target / 'docs' / 'architecture.md').is_file())
        R.check('scaffold 不含 .env', not (target / '.env').exists())
        R.check('scaffold 不含 .coverage', not (target / '.coverage').exists())
        R.check('scaffold 不含 PROJECT_EVALUATION.md', not (target / 'PROJECT_EVALUATION.md').exists())
        R.check('scaffold 不含 server.log', not (target / 'server.log').exists())
        storage_files = [p.name for p in (target / 'storage').iterdir()] if (target / 'storage').exists() else []
        R.eq('scaffold storage 仅保留 .gitkeep', storage_files, ['.gitkeep'])
        pycache = list(target.rglob('__pycache__'))
        R.check('scaffold 不含 __pycache__', not pycache, pycache[:3])
        license_text = (target / 'LICENSE').read_text(encoding='utf-8')
        R.contains('scaffold LICENSE 署名替换', license_text, 'Copyright (c) 2026 E2E Author')
        pyproject = (target / 'pyproject.toml').read_text(encoding='utf-8')
        R.contains('scaffold pyproject 版本', pyproject, 'version = "0.5.0"')

        # 生成的项目应能独立跑通自己的测试
        env = base_env(PYTHONPATH=str(target))
        # 模板测试默认 WORKER_NUM=4；E2E 环境为 gunicorn 用例设了 WORKER_NUM=1，
        # 这里去掉，避免把宿主环境带进生成项目的“默认值”断言。
        env.pop('WORKER_NUM', None)
        cp = _run([PY, '-m', 'pytest', 'tests/', '-q'], cwd=target, env=env, timeout=300)
        R.check('生成项目测试全通过', cp.returncode == 0,
                (cp.stdout[-2500:] + cp.stderr[-2500:]) if cp.returncode else '')
        R.check('生成项目测试无失败摘要', 'FAILED' not in cp.stdout, cp.stdout[-1000:])

        # 拒绝目标在模板内部
        inside = TMP_DIR / f'inside_{uuid.uuid4().hex[:6]}'
        cp = _run([PY, 'scripts/scaffold.py', str(inside)])
        R.check('scaffold 拒绝模板内部目标', cp.returncode == 1, cp.stdout + cp.stderr)
        R.contains('scaffold 内部目标提示', cp.stdout, '不能位于模板目录内部')

        # 拒绝非空目标
        nonempty = TMP_DIR / f'nonempty_{uuid.uuid4().hex[:6]}'
        nonempty.mkdir(parents=True, exist_ok=True)
        (nonempty / 'x.txt').write_text('x', encoding='utf-8')
        cp = _run([PY, 'scripts/scaffold.py', str(nonempty)])
        R.check('scaffold 拒绝非空目标', cp.returncode == 1, cp.stdout + cp.stderr)
        R.contains('scaffold 非空提示', cp.stdout, '已存在且非空')
        shutil.rmtree(nonempty, ignore_errors=True)
    finally:
        shutil.rmtree(target, ignore_errors=True)


# --------------------------------------------------------------------------
# 部署编排 / CI
# --------------------------------------------------------------------------

def test_compose_and_ci():
    env = os.environ.copy()
    env.update({'SECRET_KEY': 'e2e-secret', 'MYSQL_ROOT_PASSWORD': 'e2e-root',
                'MYSQL_PASSWORD': 'e2e-app', 'REDIS_PASSWORD': ''})
    cp = _run(['docker', 'compose', '-f', 'docker-compose.prod.yml', 'config', '--format', 'json'], env=env)
    R.check('prod compose config 退出码 0', cp.returncode == 0, cp.stdout[-1500:] + cp.stderr[-1500:])
    if cp.returncode == 0:
        data = json.loads(cp.stdout)
        services = data.get('services', {})
        for name in ('app', 'mysql', 'redis'):
            R.check(f'prod compose 包含服务 {name}', name in services, list(services))
        R.check('prod compose 默认不含 worker（profile 默认不启动）', 'worker' not in services, list(services))

    cp = _run(['docker', 'compose', '--profile', 'worker', '-f', 'docker-compose.prod.yml',
               'config', '--format', 'json'], env=env)
    R.check('prod compose --profile worker config 退出码 0', cp.returncode == 0,
            cp.stdout[-1500:] + cp.stderr[-1500:])
    if cp.returncode == 0:
        data = json.loads(cp.stdout)
        services = data.get('services', {})
        worker = services.get('worker', {})
        R.check('--profile worker 包含 worker 服务', 'worker' in services, list(services))
        R.eq('worker profile', worker.get('profiles'), ['worker'])
        R.check('worker 优雅停止 60s', str(worker.get('stop_grace_period')) in ('60s', '1m0s'),
                worker.get('stop_grace_period'))
        R.check('worker 命令运行 scripts/worker.py',
                'scripts/worker.py' in ' '.join(worker.get('command', [])), worker.get('command'))
        R.eq('app 优雅停止 15s', str(services.get('app', {}).get('stop_grace_period')), '15s')
        R.check('mysql 不暴露宿主端口', not services.get('mysql', {}).get('ports'))
        R.check('redis 不暴露宿主端口', not services.get('redis', {}).get('ports'))

    cp = _run(['docker', 'compose', '-f', 'docker-compose.yml', 'config', '--format', 'json'], env=env)
    R.check('dev compose config 退出码 0', cp.returncode == 0, cp.stdout[-1500:] + cp.stderr[-1500:])

    ci = yaml.safe_load((ROOT / '.github' / 'workflows' / 'ci.yml').read_text(encoding='utf-8'))
    jobs = set(ci.get('jobs', {}))
    expected = {'test', 'test-mysql', 'test-windows', 'test-locked-deps',
                'test-gunicorn-multiprocess', 'lint', 'deps-sync', 'security', 'docker-build'}
    R.eq('CI 流水线数量 9', len(jobs), 9)
    R.eq('CI job 集合', jobs, expected)

    dockerfile = (ROOT / 'Dockerfile').read_text(encoding='utf-8')
    R.contains('Dockerfile 多阶段 builder', dockerfile, 'AS builder')
    R.contains('Dockerfile 非 root 用户', dockerfile, 'USER appuser')
    R.contains('Dockerfile 生产入口 waitress', dockerfile, 'CMD ["python", "wsgi.py"]')
    R.contains('Dockerfile 应用依赖约束', dockerfile, '-c constraints.txt')

    req_names = set()
    for line in (ROOT / 'requirements.txt').read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            m = re.match(r'^([A-Za-z0-9_.-]+)', line)
            if m:
                req_names.add(m.group(1).lower())
    cons_names = set()
    for line in (ROOT / 'constraints.txt').read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            m = re.match(r'^([A-Za-z0-9_.-]+)', line)
            if m:
                cons_names.add(m.group(1).lower())
    R.check('constraints 覆盖全部运行时依赖', req_names <= cons_names, sorted(req_names - cons_names))


# --------------------------------------------------------------------------
# 文档 / 配置一致性
# --------------------------------------------------------------------------

def test_docs_config_consistency():
    config_text = (ROOT / 'flask_server' / 'config.py').read_text(encoding='utf-8')
    env_names = set(re.findall(r"os\.environ\.get\('([A-Z][A-Z0-9_]*)'", config_text))
    env_names |= set(re.findall(r"_parse_(?:int|bytes_env)\('([A-Z][A-Z0-9_]*)'", config_text))
    env_names |= set(re.findall(r"_parse_worker_num\('([A-Z][A-Z0-9_]*)'", config_text))
    # WORKER_NUM 由 _parse_worker_num 内部 _parse_int 读取，正则已覆盖；过滤非配置名
    env_names.discard('')
    R.check('config.py 解析出环境变量', len(env_names) > 40, len(env_names))

    env_example = (ROOT / '.env.example').read_text(encoding='utf-8')
    example_keys = set(re.findall(r'^\s*#?\s*([A-Z][A-Z0-9_]*)\s*=', env_example, re.MULTILINE))
    missing_example = sorted(env_names - example_keys)
    R.check('.env.example 覆盖 config.py 全部变量', not missing_example, missing_example)
    R.check('.env.example 有 API_TITLE', 'API_TITLE' in example_keys)
    R.check('.env.example 有 API_VERSION', 'API_VERSION' in example_keys)
    R.check('.env.example 有 API_SPEC_URL', 'API_SPEC_URL' in example_keys)
    R.check('.env.example 有 API_DOCS_URL', 'API_DOCS_URL' in example_keys)
    R.check('.env.example 有 TASK_STATUS_MAX', 'TASK_STATUS_MAX' in example_keys)

    docs_config = (ROOT / 'docs' / 'configuration.md').read_text(encoding='utf-8')
    missing_docs = sorted(n for n in env_names if f'`{n}`' not in docs_config)
    R.check('docs/configuration.md 覆盖全部变量', not missing_docs, missing_docs)

    task_docs = (ROOT / 'docs' / 'task-queue.md').read_text(encoding='utf-8')
    for key in ('TASK_QUEUE_STREAM', 'TASK_QUEUE_GROUP', 'TASK_WORKER_CONCURRENCY',
                'TASK_MAX_RETRIES', 'TASK_RETRY_BACKOFF', 'TASK_VISIBILITY_TIMEOUT',
                'TASK_STATUS_TTL', 'TASK_DEAD_LETTER_MAX', 'TASK_STATUS_MAX',
                'TASK_RESERVE_TIMEOUT', 'TASK_WORKER_EMBEDDED'):
        R.check(f'task-queue.md 配置表包含 {key}', key in task_docs)

    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    R.contains('README 标注 582 用例', readme, '582')
    R.contains('README 标注九流水线', readme, '九流水线')
    R.check('README 覆盖率数字与实际接近', '91.3' in readme or '91.27' in readme)

    # 任务队列死信重放示例：死信记录现在保留 args/kwargs，示例应按原参数重放。
    R.contains('task-queue.md 死信重放示例', task_docs, "letter['name']")
    replay_section = task_docs.split('重放一条死信')[1][:800]
    R.check('task-queue.md 重放示例保留参数',
            "letter.get('args')" in replay_section and "letter.get('kwargs')" in replay_section,
            '示例应使用 letter.get(\'args\') / letter.get(\'kwargs\') 重放')

    # benchmark 脚本 docstring 与依赖位置一致性
    bench = (ROOT / 'scripts' / 'benchmark.py').read_text(encoding='utf-8')
    R.check('benchmark docstring 与 requests 移到 dev 依赖一致',
            'requirements-dev' in bench or '仅依赖 requests' not in bench,
            'docstring 仍称 requests 在 requirements.txt')

    # 示例代码语法
    cp = _run([PY, '-m', 'compileall', '-q', 'examples', 'scripts'])
    R.check('examples/scripts 编译通过', cp.returncode == 0, cp.stdout + cp.stderr)


# --------------------------------------------------------------------------
# 入口脚本 / 基准 / Windows 跳过
# --------------------------------------------------------------------------

def test_dev_entry_and_benchmark():
    port = free_port()
    env = base_env(SERVER_PORT=port, DEBUG='false', AUTH_ENABLED='false')
    out = TMP_DIR / f'dev_entry_{uuid.uuid4().hex[:6]}.log'
    f = open(out, 'w', encoding='utf-8')
    proc = subprocess.Popen([PY, 'server.py'], cwd=str(ROOT), env=env,
                            stdout=f, stderr=subprocess.STDOUT,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        wait_http(f'http://127.0.0.1:{port}/api/v1/healthz', proc=proc)
        r = requests.get(f'http://127.0.0.1:{port}/hello', timeout=5)
        R.eq('server.py 开发入口 200', r.status_code, 200)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        f.close()
        out.unlink(missing_ok=True)

    # benchmark 脚本真实压测 40 次
    port = free_port()
    with Server('benchmark', port, base_env(SERVER_PORT=port, AUTH_ENABLED='false')) as srv:
        cp = _run([PY, 'scripts/benchmark.py', '--url', srv.base, '--requests', '40',
                   '--concurrency', '4'], timeout=120)
        R.check('benchmark 退出码 0', cp.returncode == 0, cp.stdout + cp.stderr)
        R.contains('benchmark 输出请求数', cp.stdout, '请求数=')
        R.contains('benchmark 无错误', cp.stdout, '错误=0')

    # Windows 下 gunicorn 相关脚本应明确跳过/报错，而不是堆栈崩溃
    cp = _run([PY, 'scripts/smoke_gunicorn.py'])
    R.eq('smoke_gunicorn Windows 退出码 0', cp.returncode, 0)
    R.contains('smoke_gunicorn Windows 跳过提示', cp.stdout, '[skip]')
    cp = _run([PY, 'wsgi_gunicorn.py'])
    R.eq('wsgi_gunicorn Windows 退出码 1', cp.returncode, 1)
    R.contains('wsgi_gunicorn Windows 提示', cp.stdout + cp.stderr, 'does not support Windows')


def main():
    try:
        test_scaffold()
        test_compose_and_ci()
        test_docs_config_consistency()
        test_dev_entry_and_benchmark()
    except Exception as e:  # noqa: BLE001
        dump_exception('e2e_misc', e)
        R.check('e2e_misc 未发生未捕获异常', False, f'{type(e).__name__}: {e}')
    return 0 if R.summary() else 1


if __name__ == '__main__':
    raise SystemExit(main())
