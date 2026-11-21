# 贡献指南

感谢参与！这是一个 Python/Flask 脚手架**模板**：改动既影响模板本身，也会被
`scripts/scaffold.py` 复制进别人生成的项目，所以下面几条约定请一起遵守。

## 1. 开发环境

```bash
git clone <repo-url> py_flask_server
cd py_flask_server
python -m venv .venv
# Windows: .venv\Scripts\activate      Linux / macOS: source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env                   # Windows: copy .env.example .env
python server.py                       # http://127.0.0.1:5000/docs
```

- 要求 **Python ≥ 3.10**（CI 覆盖 3.10 / 3.12 / 3.13）；
- `.env` 只放本地占位值，**不要提交**（见 [SECURITY.md](SECURITY.md) 的凭据说明）；
- 纯 Windows 环境可以直接用 `scripts/dev.ps1`（`make` 需要 POSIX shell：Git Bash / WSL）。

## 2. 常用 `make` 目标

| 目标 | 作用 |
|---|---|
| `make install` | 安装运行时 + 开发依赖 |
| `make dev` / `make prod` | 开发服务器（`server.py`）/ 生产 waitress（`wsgi.py`） |
| `make test` | 全量测试 + 覆盖率（门槛 85%） |
| `make lint` | ruff 代码风格检查（范围与 CI `lint` job 一致） |
| `make audit` | `pip-audit` 依赖安全审计 |
| `make verify` | 真实环境自检（需配置 MySQL/Redis） |
| `make smoke-gunicorn` | gunicorn 多 worker 冒烟（Linux/WSL） |
| `make worker` | 启动后台任务消费者 |
| `make e2e` | **手动**真实环境端到端（需 MySQL/Redis/Docker，见 [`_e2e/README.md`](_e2e/README.md)） |
| `make help` | 列出全部目标 |

## 3. 提交前必须通过

```bash
make test     # pytest tests/ --cov=flask_server --cov=wsgi --cov-report=term（输出覆盖率）
make lint     # ruff check flask_server/ tests/ examples/ scripts/ _e2e/ server.py wsgi.py wsgi_gunicorn.py
```

CI 跑的是同一套检查（覆盖率 85% 由 CI `test` job 的 `--cov-fail-under=85` 强制）：
任何一项红都合不进去。新增/修改的行为请补对应测试
（`tests/`，用 `FakeRedis`/测试客户端即可，不需要真实中间件）。

`make e2e` **不是**提交门禁：它要真实 MySQL/Redis/Docker，只在改到对应链路时手动跑，
详见 [`_e2e/README.md`](_e2e/README.md)。

## 4. 新增 controller / handler / task

三者都是**自动发现**，加文件即可生效，不需要修改任何入口或注册表。

### controller（HTTP 接口）

在 `flask_server/controller/` 下新建 `xxx_controller.py`：

```python
from flask_smorest import Blueprint
from flask_server.schema import ...        # 请求/响应 schema
from flask_server.service import ...       # 业务逻辑

blp = Blueprint('xxx', 'xxx', url_prefix='/api/v1/xxx', description='...')


@blp.route('/items')
class Items(MethodView):
    @blp.response(200, ItemSchema(many=True))
    def get(self):
        ...
```

- 推荐用 `flask_smorest.Blueprint`（自动参数校验 + OpenAPI 文档）；
- 也可以在模块里定义 `def register(app)`，用 `app.add_url_rule` / `app.errorhandler`
  挂原生路由（两种写法可混用）；
- 注册顺序按文件名排序（确定性）；**controller 导入失败会 fail-fast**（接口静默缺失比启动失败更糟），
  报错信息会点名是哪个模块。

### handler（TCP / UDP 消息处理器）

在 `flask_server/handler/` 下新建 `xxx.py`，用装饰器注册：

```python
from flask_server.module import tcp_server, udp_server


@tcp_server.on_message
def on_tcp(conn, data, addr):
    conn.sendall(b'echo:' + data)


@udp_server.on_message
def on_udp(data, addr):
    return b'echo:' + data        # 返回 None 表示不回发
```

- 注册是 import 副作用；服务器由入口的 `start_protocol_servers()` 启动，
  开关在 `.env`（`TCP_ENABLED` / `UDP_ENABLED` 等）；
- 单个 handler 模块导入失败只记 ERROR 并跳过（不拖垮 HTTP 服务）；
- 处理器异常不会中断服务器：记完整 traceback 后走 `on_error` 钩子。完整样例见 `examples/protocol/`。

### task（后台任务）

在 `flask_server/task/` 下新建 `xxx.py`：

```python
from flask_server.module.task_queue import task


@task('send_email', max_retries=5)
def send_email(to, subject):
    ...
```

- 参数必须可 JSON 序列化（队列里传的是 JSON 信封）；
- handler 必须**幂等**（队列 at-least-once，崩溃恢复可能重复投递）；
- worker 启动时自动导入（`auto_import_tasks()`），`python scripts/worker.py` 消费；
- 多进程部署需要 `REDIS_URL`（否则是进程内内存队列，仅单进程可用），详见
  [`docs/task-queue.md`](docs/task-queue.md)。

## 5. 配置项：`.env.example` 与文档一起改

新增 / 重命名配置项时，下面三处必须**一起**改：

1. `flask_server/config.py`：从环境变量读取（`_resolve_config`，含默认值与类型解析）；
2. `.env.example`：占位值 + 中文注释（仓库里唯一允许提交的 env 文件）；
3. `docs/configuration.md`：配置表里补一行（变量名、默认值、说明）。

CI 的 `deps-sync` job 校验 `pyproject.toml` 与 `requirements.txt` 一致；
`_e2e/e2e_misc.py` 会校验 `.env.example` / `docs/configuration.md` 覆盖了 `config.py`
解析出的全部变量。

## 6. 提交信息风格

格式：`<type>: <中文简述>`，必要时加范围 `type(scope):`。本仓库实际使用的 type：

| type | 用于 |
|---|---|
| `fix` | 缺陷修复（含 `fix(ci)`、`fix(deploy)` 等范围） |
| `feat` | 新功能 / 新能力 |
| `docs` | 文档、注释、README |
| `chore` | 依赖、构建、工具链、忽略规则、发版记录 |

示例（摘自真实历史）：

```
fix: 修复审计发现的 P0/P1 缺陷（定界/日志/撤销/任务队列）
feat: 可持久化的后台任务队列（Redis Streams / 内存双后端）
docs: 补充 _e2e 手动运行说明
chore: 收敛构建产物忽略规则与工具链范围
fix(ci): gunicorn 冒烟等待全部 worker 就绪，消除"1/2 worker"竞态
```

要求：标题一句话说清"改了什么 / 为什么"，不要写 `update`、`修改一下` 这类无信息标题；
一个提交只做一件事，重构与行为变更分开；关联 Issue 写正文（`Closes #123`）。

## 7. 文档与变更记录

- 行为变化同步更新受影响文档（`docs/`、`README.md`）；
- 用户可见的变更在 `CHANGELOG.md` 的 `## [Unreleased]` 下加一条（中文，按“修复/新增/变更”分组）；
- 发版由维护者按 [`docs/development.md`](docs/development.md) 的发布流程打
  `v<major>.<minor>.<patch>` tag（可带 `-rc.1` 之类预发布后缀）；
- 安全漏洞**不要**开公开 Issue，按 [SECURITY.md](SECURITY.md) 走私密渠道。

## 8. Pull Request

- 描述写清：动机、影响面、验证方式（实际跑了哪些命令、关键输出）；
- 按 `.github/pull_request_template.md` 的清单逐项勾选；
- 不要提交 `.env`、`server.log`、`_e2e/logs/`、`__pycache__` 等本地产物。
