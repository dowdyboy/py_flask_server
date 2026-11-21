# 开发与 FAQ

## 测试

```bash
# 安装开发依赖
pip install -r requirements-dev.txt

# 运行测试并输出覆盖率（85% 门槛由 CI `test` job 的 --cov-fail-under=85 强制）
pytest tests/ --cov=flask_server --cov=wsgi --cov-report=term
# 或 make test
```

测试覆盖：HTTP 集成（统一响应/422/404/request_id/安全头/探针/限流/健康检查故障分支）、
认证模块（Redis 存储降级与内存兜底、防爆破、refresh 轮换、登出吊销 refresh、入参限长）、
Prometheus 指标、缓存降级恢复（含故障/脏数据自愈）、事务提交/回滚（CI 含真实 MySQL）、
路径穿越防护、反射与声明式模型共存、雪花 ID 并发、SubprocessTask 哨兵停止等
**742 个用例**。覆盖率快照（2026-11-21，`pytest tests/ --cov=flask_server --cov=wsgi --cov-report=term`）：
**91.86%**，门槛 85%（快照仅供参考，随代码演进会变）。覆盖率包含 `flask_server/` 与生产入口
`wsgi.py`（`server.py`/`wsgi_gunicorn.py` 依赖 Windows 不支持的运行时，不计入）。

真实环境自检（云服务器 MySQL/Redis 全链路验证）：

```bash
python scripts/verify_real_env.py        # 连接→建库→集成测试→迁移→HTTP 全流程→启动冒烟
# 可选：--skip-pytest / --skip-migrate / --skip-reflect / --skip-boot / --keep-migrations
# 已存在 migrations/ 或 alembic.ini 时默认拒绝覆盖，确需删除历史请显式 --force-migrations
```

多进程冒烟（Linux/WSL，gunicorn 不支持 Windows）：

```bash
pip install gunicorn
python scripts/smoke_gunicorn.py     # 或 make smoke-gunicorn
```

后台任务消费者：

```bash
python scripts/worker.py --concurrency 4    # 或 make worker；多进程请配置 REDIS_URL
python scripts/worker.py --once             # 处理完当前任务即退出（联调/定时触发）
python scripts/task_replay.py --list        # 查看死信
python scripts/task_replay.py --replay 0    # 重放最近一条死信（先入队后移除）
```

校验两件事：`WORKER_NUM=2 + TCP_ENABLED=true` 时协议服务器被自动跳过且两个 worker 都存活
（修复前会端口冲突 → worker 崩溃 → master 重拉的死循环）；`WORKER_NUM=1` 时协议服务器
正常监听、HTTP 可用、SIGTERM 后退出码为 0。

CI 流水线（`.github/workflows/ci.yml`，共 11 个 job）：
1. **test** — Python 3.10/3.11/3.12/3.13 矩阵 + 覆盖率门槛 85%（pip 依赖缓存）
2. **test-mysql** — 真实 MySQL 8.0 集成测试（`TEST_DB_URI` 环境变量）
3. **test-redis** — 真实 Redis 7（`redis:7-alpine` service + healthcheck）+ 只跑标了
   `redis_live` 的用例（`tests/test_redis_live.py`）；Streams / `GETDEL` / `XAUTOCLAIM` /
   `XPENDING` 这些语义只有连真 Redis 才验得到（其余用例走 `FakeRedis` 替身），
   同时用 `RATE_LIMIT_STORE=redis` 让至少一条用例走真实 Redis 限流存储
4. **test-windows** — Windows + Python 3.12 全量测试（覆盖 Windows 专属路径）
5. **test-locked-deps** — 用 `constraints.txt` 锁定版本再跑一遍（与镜像内版本一致）
6. **test-gunicorn-multiprocess** — gunicorn 多 worker 真实冒烟（协议服务器不被重复绑定）
7. **lint** — ruff 代码风格检查（命令与根目录 `make lint` 完全一致）
8. **typecheck** — mypy 静态类型检查（配置在 `pyproject.toml` 的 `[tool.mypy]`，
   只覆盖 `flask_server/`，等价于本地 `make typecheck`）
9. **deps-sync** — pyproject.toml 与 requirements.txt 依赖清单一致性检查
10. **security** — pip-audit 依赖漏洞审计
11. **docker-build** — 多阶段镜像构建 + 容器启动冒烟

每个 job 都设了 `timeout-minutes`（10–20，gunicorn/Windows/镜像构建 20），
避免挂起的 job 长时间占着 runner；pytest 侧另有 `--timeout=120`（`pytest-timeout`），
单个用例死锁会立刻判失败而不是拖到 CI 上限。

## 真实环境端到端（`_e2e`，仅手动运行）

`_e2e/` 下是 5 个可执行脚本 + 1 个公共库，用**真实进程 + 真实中间件**验证替身覆盖不到的行为
（Redis Streams 的 `XAUTOCLAIM`、多进程 token 共享、TCP/UDP 真实分帧、docker compose 编排等）。

> **仅手动运行，未纳入 CI（需要真实 MySQL/Redis 与 Docker）**。CI 里最接近的是
> `test-mysql` 与 `test-redis` 两个 job。

| 脚本 | 覆盖内容 | 需要 |
|---|---|---|
| `e2e_http.py` | HTTP / 认证 / 限流 / 数据库（含 Redis 限流与 SQLite 持久化） | MySQL + Redis |
| `e2e_tasks.py` | 任务队列双后端、worker 生命周期、Redis Streams、worker CLI | Redis |
| `e2e_protocols.py` | TCP 四种定界、UDP、并发上限与空闲超时 | 无（纯 loopback） |
| `e2e_misc.py` | 脚手架生成、compose 编排、CI 与文档一致性 | Docker + PyYAML |
| `probe_logging.py` | 日志 JSON / 文件 handler / `request_id` 探针 | 无 |
| `common.py` | 公共库（不单独运行） | — |

```bash
# 依赖：运行时 + 开发依赖 + PyYAML（PyYAML 不在其它依赖清单里，只在 e2e 用到）
pip install -r requirements-e2e.txt

# 项目根 .env 里配好 SQLALCHEMY_URI（mysql）与 REDIS_URL 后：
python _e2e/e2e_http.py      # 跑单个脚本
make e2e                     # 全部（先打印前置条件，任一脚本失败即停）
```

退出码：`0` 全通过、`1` 有用例失败、`2` 环境没配好（脚本打印 `[ERROR] 需要 …` 后
`sys.exit(2)`，不会甩 traceback）。脚本清单、副作用与清理、常见问题见
[`_e2e/README.md`](../_e2e/README.md)。

## 应用工厂（create_app）

> 分层/生命周期/进程模型的完整说明见 [架构说明](architecture.md)。

`flask_server/factory.py` 提供 `create_app()`；模块级实例只在 `flask_server/wsgi.py` 创建
（`from flask_server import app` 仍可用，但改为首次访问时才创建：
`from flask_server.wsgi import app` / `FLASK_APP=flask_server.wsgi:app`）。

```python
from flask_server import create_app
from flask_server.config import Config

app = create_app({'TESTING': True})                       # 只改 Flask 级配置

cfg = Config({'api_version': 'v9', 'auth_enabled': True})  # 框架配置注入（可覆盖任意字段）
app = create_app(framework_config=cfg)                     # 该实例的装配与请求期配置都用它
```

装配流程（顺序即行为）：

1. `Flask(__name__)` + 配置扩展 + `Logger.configure(app, cfg)` + CORS + SECRET_KEY / MAX_CONTENT_LENGTH
2. flask-smorest `Api`（OpenAPI 文档与参数校验）
3. SocketIO（按配置，默认关闭）
4. SQLAlchemy / Flask-Migrate（db/migrate 登记到 `app.extensions`）
5. `register_request_hooks()`：参数解析等 before/after 钩子
6. `register_error_handlers()`：HTTPException / 422 / KeyError / 兜底异常
7. `init_components()`：限流 → 认证 → 指标（指标 bundle 写入 `app.extensions`；顺序决定 before_request 执行序）
8. `register_controllers()`：`controller/` 下的 Blueprint 与 `register(app)` 钩子

**app 容器（0.9）**：logger / metrics / db / migrate 都可通过 `app.extensions` 取用，
统一键与访问器在 `flask_server.module.container`（`get_db/get_logger/get_metrics`）；
无 app 上下文时回退进程默认对象（脚本/测试直调）。`db` 对象本身仍是进程级单例。

**仍是进程级单例**（有意为之）：SQLAlchemy 的 `db` 对象、Flask-Migrate、SocketIO、
内存缓存/限流计数。logger / Prometheus metrics / 认证用户存储 / db 引用已经按 app
登记在 `app.extensions`；同进程多个 app 仍共享上述单例，需要"多套完全独立配置"请用
多进程部署。

**配置解析优先级**（`flask_server/config.py::_resolve_config`）：

```
create_app(framework_config=Config({...}))   ← 该 app 的装配与请求期配置
        > 环境变量 > 项目根 .env > 代码默认值
```

`config` 是一个代理对象：请求内自动解析到当前 app 注入的配置，app 上下文之外
（后台线程 / CLI 脚本 / 导入期）回退进程默认配置，因此既有代码与测试写法不变。

**扩展点**：controller 模块里除了 Blueprint，还可以定义 `def register(app)`，
在装配时拿到 app 做任意注册（如 `app.add_url_rule`、`app.errorhandler`）。

## 依赖锁定

`requirements.txt` 写的是上下界都有的版本区间（如 `Flask>=3.1.2,<4.0`），
区间内的具体版本仍可能漂移；CI 与生产每次装出的具体版本可能不同。
`constraints.txt` 记录当前验证过的完整版本组合，**只锁版本、不改依赖关系**：

```bash
# 安装时应用锁定（CI / Dockerfile 已这样用）
pip install -r requirements.txt -c constraints.txt

# 升级依赖后重新生成
pip install -r requirements.txt && python scripts/freeze_constraints.py
```

生成脚本只收录 `requirements.txt` 直接依赖及其传递依赖闭包，不会把环境里的无关包写进去。

## 发布流程

`.github/workflows/release.yml` 由 `v*` tag 触发，顺序是**先测试、再校验、最后发 Release**：

1. **test** — 与 CI `test` job 相同的快速检查（安装 `requirements.txt` + `requirements-dev.txt`，
   跑 `pytest tests/ -q`）；`release` job 依赖它（`needs: test`），失败就不会发布；
2. **标签格式校验** — 只接受 `v<major>.<minor>.<patch>`，可带预发布后缀（如 `v0.7.0-rc.1`）；
   不匹配直接 `::error::` 失败，不会发出畸形版本；
3. **CHANGELOG 提取** — 从 `CHANGELOG.md` 取 `## [<version>] - <日期>` 小节作为 Release 正文；
   提取为空则失败，并提示补上该小节（避免"有 tag、正文是空的"）；
4. **创建 Release** — `softprops/action-gh-release` 用上一步的正文发布（`contents: write` 只在
   这个 workflow 里声明）。

发版步骤：

1. 在 `CHANGELOG.md` 里加版本小节（放在 `## [Unreleased]` 之下），填好本次内容：

   ```markdown
   ## [0.7.0] - 2026-11-20

   ### 修复
   - ...
   ```

2. 同步 `pyproject.toml` 的 `version` 与受影响文档；
3. 打 annotated tag 并推送：

   ```bash
   git tag -a v0.7.0 -m "0.7.0"
   git push origin v0.7.0
   ```

> tag 去掉前缀 `v` 后必须与 `CHANGELOG.md` 的标题**完全一致**：`v0.6.0` → `## [0.6.0] - <日期>`；
> 带预发布后缀也一样：`v0.7.0-rc.1` → `## [0.7.0-rc.1] - <日期>`。

## 代码风格

```bash
pip install ruff
ruff check flask_server/ tests/ examples/ scripts/ _e2e/ server.py wsgi.py wsgi_gunicorn.py
# 或 make lint
```

规则：E/F/W（排除 E501 行宽；`import *` 聚合与 monkey_patch 顺序有 per-file 豁免）。
本地可配置 pre-commit hooks（`.pre-commit-config.yaml`）：

```bash
pip install pre-commit && pre-commit install
```

## 依赖安全审计

```bash
pip install pip-audit
pip-audit -r requirements.txt
# 或 make audit
```

CI 的 `security` job 每次 push / PR 都跑同一条命令。**发现漏洞请走私密渠道**（GitHub 私密
安全公告或 `SECURITY.md` 里的安全邮箱），不要开公开 Issue；轮换 `SECRET_KEY` / 数据库 /
Redis 凭据的步骤也写在 [SECURITY.md](../SECURITY.md) 里。

## 性能基准

```bash
# 先启动服务：python server.py
python scripts/benchmark.py                                          # 默认参数
python scripts/benchmark.py --concurrency 20 --requests 2000         # 自定义并发/请求数
python scripts/benchmark.py --duration 10 --endpoints /api/v1/healthz,/hello
```

输出：QPS、平均/中位数/P95/P99 延迟（毫秒）、错误数。

## 分布式追踪（OpenTelemetry，可选）

模板未内置 OTel 依赖（避免无关依赖），需要时可自行接入：

```python
# 1. pip install opentelemetry-sdk opentelemetry-instrumentation-flask
# 2. flask_server/factory.py 中初始化：
from opentelemetry import trace
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

trace.set_tracer_provider(TracerProvider())
trace.get_tracer_provider().add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint='http://otel-collector:4318/v1/traces')))
FlaskInstrumentor().instrument_app(app)
```

请求日志已含 `request_id`，可与追踪 span 关联。

## 常见问题

### 端口被占用（`Address already in use`）

```bash
SERVER_PORT=5001 python server.py
# 或修改 .env 中的 SERVER_PORT
```

### 数据库连不上

1. 确认数据库服务已启动
2. 确认 `.env` 中 `SQLALCHEMY_URI` 配置正确（ORM/迁移走它）；只有使用轻量封装
   `flask_server.module.sqlite` 时才检查 `SQLITE_DB_PATH`
3. MySQL 确认用户名、密码、数据库名无误；SQLite 确认路径可写
4. Docker 部署时确认 mysql 服务健康（`docker-compose ps`）

### Swagger UI 打不开（`/docs` 404）

1. 确认已安装 flask-smorest：`pip install flask-smorest`
2. 查看启动日志是否有报错
3. 内网环境参考 [部署指南](deployment.md#swagger-ui-内网离线部署)

### `ModuleNotFoundError: No module named 'xxx'`

```bash
pip install -r requirements.txt
# 确认在虚拟环境中执行
```

### Docker 容器启动失败

```bash
docker-compose logs app
# 常见原因：
# 1. .env 文件不存在或配置有误
# 2. MySQL/Redis 未就绪（检查 docker-compose ps 健康状态）
# 3. 端口冲突（修改 docker-compose.yml 端口映射）
```

### `flask db` 命令找不到

使用迁移一键脚本（自动设置 FLASK_APP）：

```bash
python scripts/db.py migrate "your message"
python scripts/db.py upgrade
```

### 参数校验返回 422

检查请求体是否符合 Schema 定义（必填字段、字段类型），错误结构见
[API 约定](api-conventions.md#422-校验错误结构)。查看 `/docs` 了解所需参数。

### 启动时 SECRET_KEY 警告

非 development 环境使用默认 SECRET_KEY 会告警。在 `.env` 中设置：

```bash
SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
```

## 贡献

欢迎提交 Issue 和 Pull Request！开发环境搭建、`make` 目标、提交信息风格
（`fix:` / `feat:` / `docs:` / `chore:` + 中文简述）、新增 controller / handler / task 的
自动发现约定、`.env.example` 与 `docs/configuration.md` 的同步规则，见
[CONTRIBUTING.md](../CONTRIBUTING.md)；安全问题请按 [SECURITY.md](../SECURITY.md)
的私密渠道报告，不要开公开 Issue。
