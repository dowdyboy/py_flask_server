# Py Flask Server

一个基于 Flask 的生产级 Web 服务脚手架：**复制即用、建文件即出接口**。

## ✨ 特性

- 📐 **分层架构**：Controller-Service-Model-Module，目录即约定
- ⚡ **零注册接口**：在 `controller/` 新建文件定义 `blp`，路由自动注册
- 🔌 **TCP/UDP 协议服务器**：在 `handler/` 新建文件注册装饰器处理器即接收 TCP/UDP 消息（分隔符/固定长度/帧头帧尾/raw 四种定界；**UDP** 返回 bytes 自动回发，**TCP** 需在处理器里自行 `conn.sendall`）
- 🧵 **后台任务队列**：`task/` 建文件定义任务即自动发现；Redis Streams 持久化、重试退避、死信、状态查询
- ✅ **参数校验 + Swagger**：flask-smorest 自动校验入参并生成 `/docs` 文档
- 🔐 **认证骨架**：注册/登录/Token 开箱可用（默认关闭，一键开启）
- 📊 **可观测性**：request_id 全链路、JSON 日志（`Logger.debug/info/warn/error/exception`）、
  Prometheus `/metrics`（请求量/延迟 + 限流拒绝/登录锁定/任务拒绝计数，支持多进程聚合）、
  healthz/readyz 探针
- 🗄️ **数据库**：SQLAlchemy + Flask-Migrate（MySQL/SQLite）、缓存（内存/Redis 自动降级；
  内存缓存有**条目上限**，超出淘汰最久未写入的键，不会无界增长）
- 🧪 **质量门禁**：`ruff` + `mypy` 静态检查（只覆盖 `flask_server/`）、`pytest` 覆盖率门槛 85%、
  单测 `--timeout=120` 防挂死
- 🛡️ **安全基线**：路径穿越防护、URI 脱敏、限流、安全响应头（CSP/HSTS）、可信代理 XFF 最右解析
- 🐳 **容器化**：多阶段 Dockerfile + dev/prod compose + CI（`.github/workflows/` 2 个 workflow：
  `ci.yml` 的 11 个 job + `release.yml`；含真实 MySQL/Redis、Windows、多进程冒烟、类型检查、依赖锁定）

## 🚀 快速开始（5 分钟）

**前置要求**：Python >= 3.10，pip

```bash
# 1. 安装依赖
pip install -r requirements.txt -r requirements-dev.txt

# 2. 配置（可选，默认零配置即可启动）
cp .env.example .env

# 3. 启动
python server.py          # 或 make dev / .\scripts\dev.ps1 (Windows)
```

打开 `http://127.0.0.1:5000/docs` 查看 Swagger UI，`/api/v1/healthz` 验证服务。

**生成新项目**（而非复制粘贴）：

```bash
python scripts/scaffold.py my_project --author "Your Name"
```

## 📁 项目结构

```
py_flask_server/
├── flask_server/              # 主应用
│   ├── app.py                 # Flask 核心：create_app() 工厂 + CORS/安全/异常处理/探针
│   ├── config.py              # 配置（环境变量 + .env 自动加载）
│   ├── component/             # 组件：认证 / 限流 / Prometheus / 拦截器
│   ├── controller/            # 路由层（★ 新建文件即自动注册）
│   ├── handler/               # 协议处理器层（★ 新建文件即接收 TCP/UDP 消息）
│   ├── task/                  # 后台任务层（★ 新建文件即被 worker 发现）
│   ├── schema/                # marshmallow 校验 Schema
│   ├── service/               # 业务层（简单业务可省略）
│   ├── model/po/              # ORM 模型
│   ├── module/                # 基础设施：DB / 缓存 / 文件存储 / TCP·UDP 服务器
│   └── util/                  # 工具：日志 / 加密 / ID / 异步任务 / banner
├── examples/                  # 可运行的教学样例（含 api.http 测试集）
├── scripts/                   # 脚手架 / 迁移 / 任务消费者 / 冒烟与压测脚本
├── tests/                     # 663 个用例（门槛 85%；实测 90.85%）
├── webui/                     # 静态前端（webui_controller 提供页面与静态资源）
├── storage/                   # 上传文件根目录（运行期生成，勿提交）
├── deployments/               # ARMv7 等离线部署资料
├── _e2e/                      # 跨进程 / 真实依赖的 E2E 脚本
├── docs/                      # 详细文档
├── Makefile                   # 统一命令入口
├── Dockerfile / docker-compose*.yml
└── server.py / wsgi.py / wsgi_gunicorn.py   # 三个启动入口
```

> 覆盖率快照（2026-11-20）：`pytest tests/ --cov=flask_server --cov-report=term` 实测 **90.85%**（门槛 85%）。663 个用例中 6 个默认跳过——1 个依赖可选包 Flask-SocketIO，5 个是需真实 Redis 的 `redis_live` 用例（CI 的 `test-redis` job 会跑它们）。

## 🔧 常用命令

```bash
make dev        # 启动开发服务器           python server.py
make test       # 测试 + 覆盖率            pytest tests/ --cov=flask_server
make lint       # 代码风格                 ruff check flask_server/ tests/ ...
make typecheck  # 静态类型检查（mypy，只覆盖 flask_server/）
make audit      # 依赖安全审计            pip-audit -r requirements.txt
make verify     # 真实环境自检（MySQL/Redis 全链路，需配置 .env）
make smoke-gunicorn  # 多进程冒烟（Linux/WSL；校验协议服务器不被重复绑定）
make e2e        # 真实环境端到端（仅手动：需 MySQL/Redis/Docker，见 _e2e/README.md）
make worker     # 启动任务队列消费者        python scripts/worker.py --concurrency 4
make migrate m="create users table"  # 生成迁移   make upgrade   # 执行迁移
python scripts/scaffold.py my_project   # 生成新项目
```

## 📚 文档

| 文档 | 内容 |
|---|---|
| [架构说明](docs/architecture.md) | 分层与依赖方向、启动流程、请求生命周期、配置体系、进程模型、扩展点 |
| [快速上手与教程](docs/getting-started.md) | 从零创建第一个完整接口（DB/校验/文档全流程） |
| [配置说明](docs/configuration.md) | 环境变量表、APP_ENV 预设档、依赖说明 |
| [TCP/UDP 协议服务器](docs/protocol-servers.md) | 在 handler/ 注册处理器接收 TCP/UDP 消息（帧格式/回发/部署注意） |
| [任务队列](docs/task-queue.md) | 后台任务：定义/入队/消费、重试与死信、状态查询、运维 |
| [部署指南](docs/deployment.md) | Docker、生产入口选型、健康检查、安全建议 |
| [API 约定](docs/api-conventions.md) | 统一响应、错误码表、分页/ETag、认证、限流 |
| [开发与 FAQ](docs/development.md) | 测试、lint、类型检查、E2E、发布流程、常见问题 |
| [代码/文档审计报告](docs/AUDIT_REPORT.md) | 两轮全量审计：发现的问题、判定依据、修复状态 |
| [贡献指南](CONTRIBUTING.md) | 开发环境、`make` 目标、自动发现约定、提交与 PR 要求 |
| [安全策略](SECURITY.md) | 漏洞报告渠道、凭据轮换步骤、安全配置建议 |

## 📄 许可证

本项目为模板项目，可自由使用和修改（MIT）。

## 📞 联系方式

- 功能问题 / Bug：[Issue](https://github.com/dowdyboy/py_flask_server/issues)
- **安全漏洞**：请勿开公开 Issue，按 [SECURITY.md](SECURITY.md) 走私密渠道
