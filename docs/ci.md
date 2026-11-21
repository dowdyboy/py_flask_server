# CI 与本地等价命令

`.github/workflows/ci.yml` 共 **11 个 job**（矩阵内部的 Python 版本不增加 job 数）。
本页回答两个问题：**每个 job 在本地怎么复现**、**需要哪些额外依赖/环境变量**。

---

## 1. Job 对照表

| # | job | 做什么 | 本地等价命令 | 额外前置 |
|---|---|---|---|---|
| 1 | `test` | 全量测试 + 覆盖率门槛 85%（Python 3.10 / **3.11** / 3.12 / 3.13） | `make test` | 无（`pip install -r requirements.txt -r requirements-dev.txt`） |
| 2 | `test-mysql` | 真实 MySQL 8.0 集成测试 | `pytest tests/test_sqlalchemy_integration.py tests/test_sqlite.py` | 真实 MySQL + `TEST_DB_URI`（CI 自动注入） |
| 3 | `test-redis` | 真实 Redis 7 的 Streams/GETDEL/XAUTOCLAIM 语义 | `pytest tests/test_redis_live.py -m redis_live` | 真实 Redis + `REDIS_URL`（CI 还设 `RATE_LIMIT_STORE=redis`） |
| 4 | `test-windows` | Windows + Python 3.12 全量测试（Windows 专属路径） | 本机 `pytest tests/ --cov=flask_server` | Windows |
| 5 | `test-locked-deps` | 用 `constraints.txt` 锁版本再跑一遍 | `pip install -r requirements.txt -c constraints.txt -r requirements-dev.txt && make test` | `constraints.txt` |
| 6 | `test-gunicorn-multiprocess` | gunicorn 多 worker 真实冒烟（协议服务器不被重复绑定） | `make smoke-gunicorn` | Linux/WSL + `pip install "gunicorn>=23,<24"` |
| 7 | `lint` | ruff 全仓库 | `make lint` | `ruff`（dev 依赖已含） |
| 8 | `typecheck` | mypy 仅 `flask_server/` | `make typecheck` | `mypy`（dev 依赖已含） |
| 9 | `deps-sync` | `requirements.txt` ↔ `pyproject.toml` 运行时依赖一致 | 见下方脚本 | 无 |
| 10 | `security` | `pip-audit -r requirements.txt` | `make audit` | `pip-audit`（已加入 dev 依赖） |
| 11 | `docker-build` | 多阶段镜像构建 + 容器启动冒烟 | `docker build -t flask-server:ci . && docker run --rm -p 5000:5000 flask-server:ci` | Docker |

> `test` job 的矩阵含 3.11；`pyproject.toml` 的 classifiers 也声明了 3.11/3.13。

---

## 2. 常用本地命令

```bash
# 安装（含 pip-audit / ruff / mypy / pytest）
pip install -r requirements.txt -r requirements-dev.txt

# 与 CI 完全一致的覆盖率门槛
pytest tests/ --cov=flask_server --cov-report=term --cov-fail-under=85

# 只跑真实外部依赖相关的用例（需 .env 配置 SQLALCHEMY_URI / REDIS_URL）
pytest tests/test_sqlalchemy_integration.py -v
pytest tests/test_redis_live.py -m redis_live -v
```

### deps-sync 的本地等价脚本

```bash
python - <<'PY'
import tomllib

req = {line.strip() for line in open('requirements.txt', encoding='utf-8')
       if line.strip() and not line.startswith('#')}
deps = set(tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies'])
assert deps == req, f'仅 pyproject: {sorted(deps - req)}，仅 requirements: {sorted(req - deps)}'
print(f'deps in sync: {len(deps)} packages')
PY
```

> `pyproject.toml` 的 `[project.optional-dependencies].dev` 不在同步检查范围内——
> 它只是给 `pip install -e ".[dev]"` 用的便捷入口，与 `requirements-dev.txt` 保持内容一致即可。

---

## 3. 需要真实依赖的 job

| 依赖 | 需要的环境变量 | 缺失时的表现 |
|---|---|---|
| MySQL | `TEST_DB_URI`（CI 形态：`mysql+pymysql://root:root@127.0.0.1:3306/testdb`） | 集成测试 skip 或连接失败 |
| Redis | `REDIS_URL`（CI 形态：`redis://127.0.0.1:6379/0`） | `redis_live` 用例 skip |
| Docker | 本地 Docker daemon | `docker-build` 无法复现 |

这些 job 在 CI 里都由 `services:` 自动起依赖容器；本地请用 `docker compose up -d mysql redis`
或现有实例，并保证**测试库名不是应用库**（`scripts/verify_real_env.py` 有同名护栏）。

---

## 4. 失败排查提示

- **覆盖率 84.x%**：新增代码没有测试；`pytest --cov-report=term-missing` 看缺失行。
- **`test-redis` 失败但本地全绿**：本地多半在走 `FakeRedis` 替身；用 `REDIS_URL` 指向真实
  Redis 再跑 `-m redis_live`。
- **`test-gunicorn-multiprocess` 失败**：看 `scripts/smoke_gunicorn.py` 输出的
  `[smoke]` 日志；Windows 不支持 gunicorn，只能用 Linux/WSL。
- **`security` 失败**：`pip-audit` 报的 CVE 需要升级依赖并更新 `constraints.txt`；
  不要用 `--ignore-vuln` 掩盖，除非有明确记录。
