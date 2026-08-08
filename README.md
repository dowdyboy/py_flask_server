# Py Flask Server

一个基于 Flask 的生产级 Web 服务脚手架：**复制即用、建文件即出接口**。

## ✨ 特性

- 📐 **分层架构**：Controller-Service-Model-Module，目录即约定
- ⚡ **零注册接口**：在 `controller/` 新建文件定义 `blp`，路由自动注册
- ✅ **参数校验 + Swagger**：flask-smorest 自动校验入参并生成 `/docs` 文档
- 🔐 **认证骨架**：注册/登录/Token 开箱可用（默认关闭，一键开启）
- 📊 **可观测性**：request_id 全链路、JSON 日志、Prometheus `/metrics`、healthz/readyz 探针
- 🗄️ **数据库**：SQLAlchemy + Flask-Migrate（MySQL/SQLite）、缓存（内存/Redis 自动降级）
- 🛡️ **安全基线**：路径穿越防护、URI 脱敏、限流、安全响应头、可信代理
- 🐳 **容器化**：多阶段 Dockerfile + dev/prod compose + CI 四流水线

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
│   ├── app.py                 # Flask 核心（CORS/安全/异常处理/探针）
│   ├── config.py              # 配置（环境变量 + .env 自动加载）
│   ├── component/             # 组件：认证 / 限流 / Prometheus / 拦截器
│   ├── controller/            # 路由层（★ 新建文件即自动注册）
│   ├── schema/                # marshmallow 校验 Schema
│   ├── service/               # 业务层（简单业务可省略）
│   ├── model/po/              # ORM 模型
│   ├── module/                # 基础设施：DB / 缓存 / 文件存储
│   └── util/                  # 工具：日志 / 加密 / ID / 异步任务 / banner
├── examples/                  # 可运行的教学样例（含 api.http 测试集）
├── scripts/                   # 脚手架 / 迁移 / 开发启动脚本
├── tests/                     # 175 个用例（覆盖率门槛 80%）
├── docs/                      # 详细文档
├── Makefile                   # 统一命令入口
├── Dockerfile / docker-compose*.yml
└── server.py / wsgi.py / wsgi_gunicorn.py   # 三个启动入口
```

## 🔧 常用命令

```bash
make dev        # 启动开发服务器           python server.py
make test       # 测试 + 覆盖率            pytest tests/ --cov=flask_server
make lint       # 代码风格                 ruff check flask_server/ tests/ ...
make audit      # 依赖安全审计            pip-audit -r requirements.txt
make migrate m="create users table"  # 生成迁移   make upgrade   # 执行迁移
python scripts/scaffold.py my_project   # 生成新项目
```

## 📚 文档

| 文档 | 内容 |
|---|---|
| [快速上手与教程](docs/getting-started.md) | 从零创建第一个完整接口（DB/校验/文档全流程） |
| [配置说明](docs/configuration.md) | 环境变量表、APP_ENV 预设档、依赖说明 |
| [部署指南](docs/deployment.md) | Docker、生产入口选型、健康检查、安全建议 |
| [API 约定](docs/api-conventions.md) | 统一响应、错误码表、分页/ETag、认证、限流 |
| [开发与 FAQ](docs/development.md) | 测试、lint、常见问题 |

## 📄 许可证

本项目为模板项目，可自由使用和修改。

## 📞 联系方式

如有问题，请提交 [Issue](https://github.com/dowdyboy/py_flask_server/issues)。
