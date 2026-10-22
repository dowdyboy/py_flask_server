# 开发与 FAQ

## 测试

```bash
# 安装开发依赖
pip install -r requirements-dev.txt

# 运行测试（含覆盖率检查，覆盖率门槛 85%）
pytest tests/ --cov=flask_server --cov-report=term
# 或 make test
```

测试覆盖：HTTP 集成（统一响应/422/404/request_id/安全头/探针/限流/健康检查故障分支）、
认证模块（Redis 存储降级与内存兜底、防爆破、refresh 轮换、登出吊销 refresh、入参限长）、
Prometheus 指标、缓存降级恢复（含故障/脏数据自愈）、事务提交/回滚（CI 含真实 MySQL）、
路径穿越防护、反射与声明式模型共存、雪花 ID 并发、SubprocessTask 哨兵停止等
**566 个用例**（覆盖率 91.2%）。

真实环境自检（云服务器 MySQL/Redis 全链路验证）：

```bash
python scripts/verify_real_env.py        # 连接→建库→集成测试→迁移→HTTP 全流程→启动冒烟
# 可选：--skip-pytest / --skip-migrate / --skip-reflect / --skip-boot / --keep-migrations
```

多进程冒烟（Linux/WSL，gunicorn 不支持 Windows）：

```bash
pip install gunicorn
python scripts/smoke_gunicorn.py     # 或 make smoke-gunicorn
```

校验两件事：`WORKER_NUM=2 + TCP_ENABLED=true` 时协议服务器被自动跳过且两个 worker 都存活
（修复前会端口冲突 → worker 崩溃 → master 重拉的死循环）；`WORKER_NUM=1` 时协议服务器
正常监听、HTTP 可用、SIGTERM 后退出码为 0。

CI 流水线（`.github/workflows/ci.yml`）：
1. **test** — Python 3.10/3.12 矩阵 + 覆盖率门槛 85%
2. **test-mysql** — 真实 MySQL 8.0 集成测试（`TEST_DB_URI` 环境变量）
3. **test-windows** — Windows + Python 3.12 全量测试（覆盖 Windows 专属路径）
4. **test-locked-deps** — 用 `constraints.txt` 锁定版本再跑一遍（与镜像内版本一致）
5. **test-gunicorn-multiprocess** — gunicorn 多 worker 真实冒烟（协议服务器不被重复绑定）
6. **lint** — ruff 代码风格检查
7. **security** — pip-audit 依赖漏洞审计
8. **docker-build** — 多阶段镜像构建 + 容器启动冒烟
9. **deps-sync** — pyproject.toml 与 requirements.txt 依赖清单一致性检查

## 应用工厂（create_app）

> 分层/生命周期/进程模型的完整说明见 [架构说明](architecture.md)。

`flask_server/app.py` 提供 `create_app()`，模块底部的 `app = create_app()` 保留原有用法
（`from flask_server import app` / `FLASK_APP=flask_server.app:app` 都不变）。

```python
from flask_server import create_app
from flask_server.config import Config

app = create_app({'TESTING': True})                       # 只改 Flask 级配置

cfg = Config({'api_version': 'v9', 'auth_enabled': True})  # 框架配置注入（可覆盖任意字段）
app = create_app(framework_config=cfg)                     # 该实例的装配与请求期配置都用它
```

装配流程（顺序即行为）：

1. `Flask(__name__)` + CORS + SECRET_KEY / MAX_CONTENT_LENGTH
2. flask-smorest `Api`（OpenAPI 文档与参数校验）
3. SocketIO（按配置，默认关闭）
4. SQLAlchemy / Flask-Migrate
5. `register_request_hooks()`：参数解析等 before/after 钩子
6. `register_error_handlers()`：HTTPException / 422 / KeyError / 兜底异常
7. `init_components()`：限流 → 认证 → 指标（顺序决定 before_request 执行序）
8. `register_controllers()`：`controller/` 下的 Blueprint 与 `register(app)` 钩子

**仍是进程级单例**（有意为之）：SQLAlchemy 的 `db`、Flask-Migrate、SocketIO、
Prometheus 指标对象、内存缓存/限流/认证存储。同进程创建多个 app 会共享这些；
需要"多套完全独立配置"请用多进程部署。

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

`requirements.txt` 只写版本区间（下界），CI 与生产每次装出的具体版本可能不同。
`constraints.txt` 记录当前验证过的完整版本组合，**只锁版本、不改依赖关系**：

```bash
# 安装时应用锁定（CI / Dockerfile 已这样用）
pip install -r requirements.txt -c constraints.txt

# 升级依赖后重新生成
pip install -r requirements.txt && python scripts/freeze_constraints.py
```

生成脚本只收录 `requirements.txt` 直接依赖及其传递依赖闭包，不会把环境里的无关包写进去。

## 代码风格

```bash
pip install ruff
ruff check flask_server/ tests/ examples/ server.py wsgi.py wsgi_gunicorn.py
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
# 2. flask_server/app.py 中初始化：
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
2. 确认 `.env` 中 `SQLALCHEMY_URI` 或 `SQLITE_DB_PATH` 配置正确
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

欢迎提交 Issue 和 Pull Request！
