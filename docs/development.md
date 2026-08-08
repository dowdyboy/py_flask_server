# 开发与 FAQ

## 测试

```bash
# 安装开发依赖
pip install -r requirements-dev.txt

# 运行测试（含覆盖率检查，分支覆盖率阈值 80%）
pytest tests/ --cov=flask_server --cov-report=term
# 或 make test
```

测试覆盖：HTTP 集成（统一响应/422/404/request_id/安全头/探针/限流）、
认证模块、Prometheus 指标、缓存降级恢复、事务提交/回滚（CI 含真实 MySQL）、
路径穿越防护、雪花 ID 并发、SubprocessTask 哨兵停止等 **175 个用例**。

CI 流水线（`.github/workflows/ci.yml`）：
1. **test** — Python 3.10/3.12 矩阵 + 覆盖率门槛 80%
2. **test-mysql** — 真实 MySQL 8.0 集成测试（`TEST_DB_URI` 环境变量）
3. **lint** — ruff 代码风格检查
4. **security** — pip-audit 依赖漏洞审计

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
