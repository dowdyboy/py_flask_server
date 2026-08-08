# Py Flask Server

一个基于 Flask 的轻量级 Python Web 服务模板，采用分层架构设计，提供快速搭建 RESTful API 后端服务的脚手架。

## ✨ 特性

- 🚀 **快速启动**: 开箱即用的 Flask 项目模板，零配置即可运行
- 📐 **分层架构**: 采用 Controller-Service-Model 分层设计，代码结构清晰
- ✅ **参数校验**: 集成 flask-smorest + marshmallow，自动校验入参并生成 Swagger UI 文档
- 🔄 **异步支持**: WebSocket 可选启用，异步任务执行能力
- 🗄️ **数据库支持**: SQLAlchemy ORM + Flask-Migrate 迁移，支持 MySQL 和 SQLite
- 💾 **文件存储**: 本地文件存储模块，简化文件上传管理
- 🔒 **鉴权骨架**: 提供拦截器骨架与完整样例（见 examples/）
- 📝 **日志系统**: 统一日志管理工具，支持 request_id 链路追踪与轮转
- 🔧 **工具丰富**: 提供加密（sha256/pbkdf2）、缓存（内存/Redis）、日期处理等工具类
- 🌐 **跨域支持**: 内置 CORS 配置
- 📦 **统一响应**: 标准化的 JSON 响应格式 + RESTful 状态码
- 🐳 **容器化**: 内置 Dockerfile + docker-compose（app + MySQL + Redis）

## 🚀 从模板创建新项目

### 前置条件

- 已安装 Python >= 3.10（推荐 3.12）
- 已安装 pip

### 步骤

1. **复制模板**为你的新项目目录：
   ```bash
   cp -r py_flask_server my_new_project
   cd my_new_project
   ```

2. **修改署名**：编辑 `LICENSE`，将 `Copyright (c) 2026 dowdyboy` 改为你的名字

3. **创建虚拟环境**（推荐）：
   ```bash
   python -m venv .venv
   # Linux/macOS
   source .venv/bin/activate
   # Windows
   .venv\Scripts\activate
   ```

4. **安装依赖**：
   ```bash
   pip install -r requirements.txt
   # 如需运行测试
   pip install -r requirements-dev.txt
   ```

5. **配置环境变量**：
   ```bash
   cp .env.example .env
   # 编辑 .env，按需配置（数据库、端口等）
   ```
   > 框架启动时会自动加载项目根目录的 `.env` 文件，无需手动 export。

6. **启动服务**：
   ```bash
   python server.py
   ```
   服务默认在 `http://127.0.0.1:5000` 启动。访问 `http://127.0.0.1:5000/docs` 查看 API 文档。

7. **开始编写接口**：
   - 在 `flask_server/controller/` 下新建 `your_controller.py`（参考 `hello_controller.py` 或下方教程）
   - 在 `controller/__init__.py` 中添加 `from .your_controller import blp as your_blp` + `api.register_blueprint(your_blp)`
   - 业务复杂时新增 `service/`，简单业务可直接在 controller 实现
   - 需要数据库时配置 `SQLALCHEMY_URI` 或 `SQLITE_DB_PATH`
   - 需要鉴权时参考 `examples/component/interceptor_example.py`

> **命名约定**：controller 文件名小写下划线，类名用 `XxxService`/`XxxPO`；简单业务可省略 Service 层。

## 📋 环境要求

- Python >= 3.10（flask-smorest 0.45+ 要求 3.9+，0.47 要求 3.10+）
- pip

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置数据库（可选）

默认不启用数据库。如需启用，通过环境变量配置：

```bash
# MySQL（SQLAlchemy，建议显式指定 charset=utf8mb4 保证中文正确）
export SQLALCHEMY_URI='mysql+pymysql://username:password@host:3306/database_name?charset=utf8mb4'

# 或 SQLite
export SQLITE_DB_PATH='storage/app.db'
```

> 也可直接编辑 `flask_server/config.py` 中的默认值。

### 3. 启动服务

```bash
python server.py
```

服务默认在 `http://127.0.0.1:5000` 启动（仅本地访问）。对外暴露请设置 `SERVER_HOST=0.0.0.0`。

## 📁 项目结构

```
py_flask_server/
├── flask_server/              # 主应用目录
│   ├── __init__.py           # 模块初始化
│   ├── app.py                # Flask 应用核心配置
│   ├── config.py             # 配置文件
│   │
│   ├── component/            # 组件层（拦截器、中间件等）
│   │   └── interceptor.py   # 请求拦截器骨架
│   │
│   ├── controller/           # 控制器层（路由处理）
│   │   ├── webui_controller.py     # WebUI 静态资源服务
│   │   └── hello_controller.py     # 示例接口（含 flask-smorest Blueprint）
│   │
│   ├── schema/               # 参数校验 Schema（marshmallow）
│   │   └── common.py         # 通用 Schema
│   │
│   ├── service/              # 服务层（业务逻辑，样例见 examples/）
│   │
│   ├── model/                # 数据模型层
│   │   └── po/               # 持久化对象
│   │       └── base.py       # 声明式 Model 基类示例
│   │
│   ├── module/               # 核心模块
│   │   ├── sqlalchemy.py    # SQLAlchemy + Flask-Migrate
│   │   ├── sqlite.py        # SQLite 数据库模块
│   │   ├── simple_memory_cache.py  # 内存缓存模块
│   │   ├── redis_cache.py   # Redis 缓存模块
│   │   └── local_file_storage.py   # 本地文件存储
│   │
│   └── util/                 # 工具类
│       ├── logger.py        # 日志工具（支持 request_id）
│       ├── grace_result.py  # 统一响应格式
│       ├── async_task_util.py     # 异步任务工具
│       ├── data_encrypt_util.py   # 数据加密工具
│       ├── date_time_util.py      # 日期时间工具
│       ├── random_generator.py    # 随机数生成器
│       ├── key_generator.py       # 密钥生成器
│       └── common.py        # 通用工具
│
├── examples/                  # 参考样例（不参与工程运行，仅教学参考）
│   ├── README.md              # 样例使用说明
│   ├── component/
│   │   └── interceptor_example.py   # 鉴权拦截器样例
│   ├── controller/
│   │   ├── user_controller.py       # 用户接口样例
│   │   └── article_controller.py    # 文章接口样例
│   ├── service/
│   │   ├── user_service.py          # 用户业务样例
│   │   └── article_service.py       # 文章业务样例
│   └── model/
│       ├── user.py                  # 反射式模型样例
│       ├── user_declared.py         # 声明式模型正例（推荐）
│       └── article.py               # 反射式模型样例
│
├── webui/                   # 前端静态资源
│   └── index.html          # 脚手架占位页
│
├── tests/                   # 单元测试（pytest）
├── storage/                # 文件存储目录
│   └── .gitkeep
│
├── Dockerfile               # 容器构建文件
├── docker-compose.yml       # 容器编排（app + mysql + redis）
├── .dockerignore
├── .env.example             # 环境变量示例
├── requirements.txt         # 依赖列表
├── requirements-dev.txt     # 开发依赖（pytest）
├── server.py               # 开发启动入口
├── wsgi.py                 # 生产部署入口（waitress）
└── README.md               # 项目说明文档
```

## 🏗️ 架构设计

### 分层架构

```
┌─────────────────────────────────────┐
│         Controller Layer            │  ← 路由定义、参数解析
├─────────────────────────────────────┤
│          Service Layer              │  ← 业务逻辑处理
├─────────────────────────────────────┤
│           Model Layer               │  ← 数据模型定义
├─────────────────────────────────────┤
│           Module Layer              │  ← 核心功能模块（DB、Cache、Storage）
└─────────────────────────────────────┘
```

### 请求处理流程

1. **请求拦截** → `component/interceptor.py`（骨架，样例见 examples/）
2. **request_id** → `app.py` 中的 `before_request` 生成/透传 `X-Request-Id`
3. **参数解析** → `app.py` 中的 `before_request` 钩子（或 flask-smorest `@blp.arguments` 校验）
4. **路由处理** → `controller/*.py`
5. **业务逻辑** → `service/*.py`
6. **数据操作** → `module/*.py`
7. **响应封装** → `util/grace_result.py`

## 📚 教程：创建第一个完整接口

本教程带你从零创建一个带数据库、参数校验、API 文档的完整用户接口。

### 第 1 步：配置数据库

编辑 `.env` 文件（或设置环境变量）：

```bash
# 使用 SQLite（最简单，无需额外安装）
SQLITE_DB_PATH=storage/app.db

# 或使用 MySQL
# SQLALCHEMY_URI=mysql+pymysql://root:password@localhost:3306/mydb?charset=utf8mb4
```

> 若使用 SQLite，本框架会自动创建数据库文件。若使用 MySQL，需先创建数据库，建议显式加 `?charset=utf8mb4`。

### 第 2 步：声明 Model

创建 `flask_server/model/po/user.py`：

```python
from flask_server.module.sqlalchemy import sqlalchemy
from datetime import datetime

db = sqlalchemy()


class UserPO(db.Model):
    __tablename__ = 'user'

    uid = db.Column(db.String(64), primary_key=True)
    username = db.Column(db.String(128), unique=True, nullable=False)
    passwd = db.Column(db.String(128), nullable=False)
    create_time = db.Column(db.DateTime, default=datetime.now)
```

在 `flask_server/model/po/__init__.py` 中导出：

```python
from .user import UserPO

__all__ = ['UserPO']
```

### 第 3 步：运行数据库迁移

```bash
# 设置 FLASK_APP（或在 .env 中配置）
export FLASK_APP=flask_server.app:app    # Linux/macOS
set FLASK_APP=flask_server.app:app       # Windows

# 首次初始化迁移目录
flask db init

# 生成迁移脚本（会检测到 user 表）
flask db migrate -m "create user table"

# 执行迁移（真正建表）
flask db upgrade
```

> 之后每次修改 Model 字段，重复 `flask db migrate` + `flask db upgrade` 即可。

### 第 4 步：编写 Schema（参数校验）

创建 `flask_server/schema/user_schema.py`：

```python
from marshmallow import Schema, fields


class UserCreateSchema(Schema):
    """创建用户入参"""
    username = fields.String(required=True, metadata={'description': '用户名'})
    password = fields.String(required=True, metadata={'description': '密码'})


class UserResponseSchema(Schema):
    """用户响应数据"""
    uid = fields.String()
    username = fields.String()
    create_time = fields.String()
```

在 `flask_server/schema/__init__.py` 中导出：

```python
from .common import GraceResultSchema, EchoSchema
from .user_schema import UserCreateSchema, UserResponseSchema

__all__ = ['GraceResultSchema', 'EchoSchema', 'UserCreateSchema', 'UserResponseSchema']
```

### 第 5 步：编写 Service（业务逻辑）

创建 `flask_server/service/user_service.py`：

```python
from datetime import datetime
from flask_server.module import sqlalchemy, sqlalchemy_trans
from flask_server.model import UserPO
from flask_server.util import DataEncryptUtil, RandomGenerator, Logger


class UserService:

    @staticmethod
    @sqlalchemy_trans
    def create(username, password):
        uid = RandomGenerator.secrets_token(16)
        user = UserPO()
        user.uid = uid
        user.username = username
        user.passwd = DataEncryptUtil.sha256(password)
        sqlalchemy().session.add(user)
        return uid

    @staticmethod
    def get_by_uid(uid):
        return UserPO.query.filter(UserPO.uid == uid).first()
```

在 `flask_server/service/__init__.py` 中导出：

```python
from .user_service import UserService

__all__ = ['UserService']
```

### 第 6 步：编写 Controller（接口路由）

创建 `flask_server/controller/user_controller.py`：

```python
from flask.views import MethodView
from flask_smorest import Blueprint
from flask_server.util import GraceResult, CommonUtil
from flask_server.schema import GraceResultSchema, UserCreateSchema
from flask_server.service import UserService

blp = Blueprint('user', 'user', url_prefix='/api/v1/users',
                description='用户管理接口')


@blp.route('/')
class UserListCreateView(MethodView):
    @blp.arguments(UserCreateSchema)
    @blp.response(201, GraceResultSchema)
    def post(self, data):
        """创建用户"""
        uid = UserService.create(data['username'], data['password'])
        return GraceResult.ok({'uid': uid}), 201


@blp.route('/<string:uid>')
class UserDetailView(MethodView):
    @blp.response(200, GraceResultSchema)
    def get(self, uid):
        """获取用户详情"""
        user = UserService.get_by_uid(uid)
        if user is None:
            return GraceResult.business_error(4004, '用户不存在'), 404
        return GraceResult.ok(CommonUtil.obj_to_dict(user))
```

### 第 7 步：注册 Controller

编辑 `flask_server/controller/__init__.py`，添加：

```python
from .user_controller import blp as user_blp
api.register_blueprint(user_blp)
```

### 第 8 步：启动并测试

```bash
python server.py
```

测试创建用户：
```bash
curl -X POST http://127.0.0.1:5000/api/v1/users/ \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","password":"123456"}'
```

测试查询用户：
```bash
curl http://127.0.0.1:5000/api/v1/users/<返回的uid>
```

查看 API 文档：浏览器打开 `http://127.0.0.1:5000/docs`，即可看到自动生成的 Swagger UI，可直接在页面上测试接口。

## 📚 核心功能

### 1. 统一响应格式

所有接口返回统一的 JSON 格式：

```json
{
  "code": 0,
  "msg": "成功",
  "data": {}
}
```

状态码定义：
- `0`: 成功
- `1001`: 参数错误
- `-1`: 内部错误

### 2. 请求参数自动解析

框架自动解析多种请求格式：

- **GET 参数**: `request.params`
- **JSON Body**: `request.payload` (Content-Type: application/json)
- **Form Data**: `request.payload` (Content-Type: application/x-www-form-urlencoded)
- **Multipart**: `request.payload` (包含文件)

### 3. 数据库操作

#### 使用 SQLAlchemy ORM

```python
from flask_server.model import UserPO
from flask_server.module import sqlalchemy_trans

class UserService:
    @staticmethod
    @sqlalchemy_trans
    def login(username, password):
        user = UserPO.query.filter(
            UserPO.username == username
        ).first()
        return user
```

#### 直接执行 SQL

```python
from flask_server.module import SQLite

# 查询
rows = SQLite.fetch("SELECT * FROM users WHERE uid = ?", ['123'])

# 插入
row_id = SQLite.execute("INSERT INTO users (uid, name) VALUES (?, ?)", ['123', 'test'])
```

### 4. 缓存使用

```python
from flask_server.module import memory_cache as cache

# 设置缓存（永不过期）
# 注意：waitress 为多线程 WSGI 服务器，memory_cache 已加线程锁保证安全
cache.set('key', 'value')

# 设置缓存并指定 TTL 过期时间（单位：秒）
cache.set('key', 'value', ttl=3600)

# 获取缓存
value = cache.get('key')

# 删除缓存
cache.delete('key')

# 检查是否存在
exists = cache.exists('key')
```

### 5. 异步任务

```python
from flask_server.util import AsyncTaskUtil

# 异步执行函数
AsyncTaskUtil.submit_func_task(some_function, arg1='value1')

# 异步执行命令
AsyncTaskUtil.submit_cmd_task_plain(
    'node --version',
    extra_param='hello',
    on_success=lambda p, r: print(f'成功: {r}'),
    on_error=lambda p, e: print(f'失败: {e}')
)
```

### 6. 日志记录

```python
from flask_server.util import Logger

Logger.info('信息日志')
Logger.warn('警告日志')
Logger.error('错误日志')
```

### 7. 文件上传

```python
from flask_server.module import local_file_storage

# 保存到配置目录下的相对路径
local_file_storage.save('uploads/file.txt', file_obj)

# 读取
data = local_file_storage.load('uploads/file.txt')
```

### 8. 安全加密与随机令牌

```python
from flask_server.util import DataEncryptUtil, RandomGenerator

# SHA-256 哈希
hash_hex = DataEncryptUtil.sha256('text')

# 密码存储（PBKDF2-HMAC-SHA256，自动加盐，返回 "salt$iterations$hash"）
stored = DataEncryptUtil.pbkdf2_hmac('mypassword')
# 校验密码
DataEncryptUtil.verify_pbkdf2('mypassword', stored)  # True/False

# 密码学安全的随机令牌（用于 token/密钥场景）
token = RandomGenerator.secrets_token(32)
```

> `DataEncryptUtil.sha1` 与 `RandomGenerator.random_string` 仍保留但已不推荐用于安全场景。

### 9. 参数校验与 API 文档（flask-smorest）

项目集成 flask-smorest + marshmallow，提供参数校验与 Swagger UI 自动文档。

**创建带校验的接口（Blueprint 风格，推荐）：**

```python
# flask_server/controller/your_controller.py
from flask.views import MethodView
from flask_smorest import Blueprint
from marshmallow import Schema, fields
from flask_server.util import GraceResult
from flask_server.schema import GraceResultSchema

blp = Blueprint('your', 'your', url_prefix='/api/v1', description='你的接口')

class CreateSchema(Schema):
    name = fields.String(required=True, metadata={'description': '名称'})
    age = fields.Integer(required=False, metadata={'description': '年龄'})

@blp.route('/create')
class CreateView(MethodView):
    @blp.arguments(CreateSchema)
    @blp.response(200, GraceResultSchema)
    def post(self, data):
        """创建资源"""  # 此 docstring 会出现在 Swagger 文档中
        return GraceResult.ok(data)
```

在 `controller/__init__.py` 注册：
```python
from .your_controller import blp as your_blp
api.register_blueprint(your_blp)
```

访问 `/docs` 查看 Swagger UI，`/openapi.json` 获取 OpenAPI Schema。

**简单接口**仍可用 `@app.route` + `@json_response`（无校验无文档）。

> **内网/离线环境部署 Swagger**：默认 `/docs` 从 jsdelivr CDN 加载资源，无外网时页面空白。
> 将 swagger-ui-dist 静态资源托管到内网（如 Nginx），并设置环境变量指向：
> ```bash
> SWAGGER_UI_URL=http://internal-docs.example.com/swagger-ui-dist/
> ```
> Nginx 托管示例（`swagger-ui-dist/` 目录放置 `swagger-ui.css`/`swagger-ui-bundle.js` 等）：
> ```nginx
> location /swagger-ui-dist/ { alias /srv/www/swagger-ui-dist/; }
> ```

### 10. 数据库迁移（Flask-Migrate）

配置 `SQLALCHEMY_URI` 后，使用 Flask-Migrate 管理建表迁移：

```bash
export FLASK_APP=flask_server.app:app
flask db init        # 首次初始化迁移目录（生成 migrations/）
flask db migrate -m "create users table"   # 生成迁移脚本
flask db upgrade     # 执行迁移（建表/改表）
```

在 `model/po/` 下声明 Model（推荐声明式，参考 `examples/model/user_declared.py`），迁移脚本会自动检测变更。

> **反射与迁移共存约束**：`DB_REFLECT_ON_START=true` 时启动会反射现有表到 metadata（便于查询已有库表）。反射表与声明式 Model **不能同名**（会报 Table already defined）。建议：已有库用反射，新建表用声明式 + Migrate；或统一声明式并设 `DB_REFLECT_ON_START=false`。

### 11. Redis 缓存

配置 `REDIS_URL` 后自动启用 Redis 缓存（多进程/多实例场景）：

```bash
export REDIS_URL=redis://localhost:6379/0
```

```python
from flask_server.module import redis_cache

redis_cache.set('key', 'value', ttl=3600)   # 自动 JSON 序列化
value = redis_cache.get('key')
```

> 未配置 `REDIS_URL` 时使用 `memory_cache`（内存缓存）。

### 12. 业务错误码

```python
from flask_server.util import GraceResult

# 自定义业务码（非 0/1001/-1）
return GraceResult.business_error(2001, '用户不存在')
```

### 13. 分页与 ETag（flask-smorest 进阶）

**分页**（`Page`）：flask-smorest 内置分页支持，配合 `paginate` 使用：

```python
from flask_smorest import Page

class UserPage(Page):
    """用户分页响应"""
    items = ma.fields.List(ma.fields.Raw())

@blp.route('/users/page')
class UserPageView(MethodView):
    @blp.arguments(UserQuerySchema, location='query')
    @blp.response(200, UserPage)
    def get(self, query):
        """分页查询用户"""
        return UserService.list(page=query['page'], per_page=query['per_page'])
```

分页返回结构：`{items: [...], page: n, per_page: n, total: n}`（对应 SQLAlchemy 的 `paginate()`）。

**ETag 缓存协商**（`@blp.etag`）：GET 响应自动携带 `ETag` 头，客户端带 `If-None-Match` 时返回 304，节省带宽：

```python
@blp.route('/articles/<string:aid>')
class ArticleView(MethodView):
    @blp.etag  # 校验 If-None-Match，命中返回 304
    @blp.response(200, GraceResultSchema)
    def get(self, aid):
        """获取文章"""
        article = ArticleService.get(aid)
        if article is None:
            return GraceResult.business_error(4004, '文章不存在'), 404
        return GraceResult.ok(article)
```

> ETag 与 `@blp.arguments` 同时使用时需把 `@blp.etag` 放在最外层（先校验再验参）。

### 14. 健康检查与探针

框架提供三个健康端点，用途不同（供容器编排/负载均衡/K8s 使用）：

| 端点 | 语义 | 行为 |
|---|---|---|
| `GET /api/v1/healthz` | **存活探针（liveness）** | 仅表示进程存活，不检查依赖，恒返回 200 |
| `GET /api/v1/readyz` | **就绪探针（readiness）** | 检查 DB/Redis 连通性，任一依赖故障返回 **503** + 统一格式 |
| `GET /api/v1/health` | 详情诊断（人工排查用） | 返回 status/version/uptime + 各依赖状态（不改变 HTTP 状态码） |

**推荐用法**：
- 容器 healthcheck / K8s `livenessProbe` 用 `/api/v1/healthz`
- K8s `readinessProbe` / 负载均衡健康检查用 `/api/v1/readyz`（依赖故障时实例被摘除流量）
- 本模板的 docker-compose healthcheck 已默认使用 `/api/v1/readyz`（MySQL/Redis 未就绪时 app 容器不健康）

### 15. 认证模块（可选）

内置注册/登录/Token 骨架，默认关闭、零配置可跑：

```bash
# 启用全局认证保护（/api/ 下除 auth/文档/健康检查外的路径需要 X-AUTH-TOKEN）
export AUTH_ENABLED=true
# 用户存储：memory（进程内，重启丢失）或 sqlalchemy（持久化）
export AUTH_STORE=memory
```

**接口**（`/api/v1/auth/*`，flask-smorest 风格带校验与文档）：

| 接口 | 说明 |
|---|---|
| `POST /api/v1/auth/register` | 注册（username 3-64 / password 6-128，PBKDF2 加密存储） |
| `POST /api/v1/auth/login` | 登录，返回 token（有效期 `AUTH_TOKEN_TTL`） |
| `POST /api/v1/auth/logout` | 登出（使 token 失效） |
| `GET /api/v1/auth/me` | 当前用户信息（需 `X-AUTH-TOKEN` 头） |

**保护业务接口**：

```python
from flask_server.component.auth import login_required

@blp.route('/profile')
class ProfileView(MethodView):
    @blp.response(200, GraceResultSchema)
    @login_required          # 校验 X-AUTH-TOKEN，通过后 request.info['uid'] 可用
    def get(self):
        uid = request.info['uid']
        return GraceResult.ok({'uid': uid})
```

**持久化存储**（`AUTH_STORE=sqlalchemy`）：配置 `SQLALCHEMY_URI` 后执行
`flask db migrate -m "create user table" && flask db upgrade` 建表（模型：`flask_server/model/po/user.py`）。

> 安全提示：响应绝不含密码哈希；生产环境务必修改 SECRET_KEY 并配置 HTTPS。

### 16. Prometheus 指标（可选）

`METRICS_ENABLED=true`（默认开）时暴露 `GET /metrics`（Prometheus 文本格式）：

- `http_requests_total{method,status,route}` — 请求计数（**route 用路由规则聚合，防高基数**）
- `http_request_duration_seconds{method,route}` — 请求延迟直方图

未安装 `prometheus-client` 时 `/metrics` 返回 503 并告警（应用不受影响）。K8s/监控采集示例：

```yaml
# Prometheus 抓取配置
scrape_configs:
  - job_name: flask-server
    metrics_path: /metrics
    static_configs:
      - targets: ["app:5000"]
```

## 🐳 Docker 部署

### 一键启动（app + MySQL + Redis）

```bash
# 复制环境变量文件
cp .env.example .env

# 构建并启动
docker-compose up -d

# 查看状态
docker-compose ps
```

服务将在 `http://localhost:5000` 启动，MySQL 在 `3306`，Redis 在 `6379`。

### 单独构建 app 镜像

```bash
docker build -t flask-server .
docker run -p 5000:5000 --env-file .env flask-server
```

## 🚢 生产部署

提供三种生产入口，按需选择：

| 入口 | 特点 | 适用 |
|---|---|---|
| `python wsgi.py` | waitress，零额外依赖、单进程多线程 | 默认推荐，中小流量 |
| `python wsgi_gunicorn.py` | gunicorn 多 worker 高并发；`SOCKETIO_ENABLED=true` 时自动用 eventlet worker（支持 WebSocket） | 高并发 / 需要 WebSocket 的 Linux 生产 |

```bash
# waitress（默认，零依赖）
python wsgi.py

# gunicorn（Linux 专用，需 pip install gunicorn；WebSocket 场景另需 eventlet）
WORKER_NUM=4 python wsgi_gunicorn.py
```

> ⚠️ gunicorn 不支持 Windows，Windows 生产请使用 wsgi.py（waitress）。
> 生产环境请确保 `APP_ENV=production`、`DEBUG=false`、设置强 `SECRET_KEY`、收紧 `CORS_ORIGINS`、配置 HTTPS。

**容器化生产部署**（waitress 入口 + 不暴露 DB/Redis 端口 + SECRET_KEY 必填校验 + 日志卷）：

```bash
docker-compose -f docker-compose.prod.yml up -d --build
```

## 🛠️ 配置说明

通过环境变量配置（也可直接编辑 `flask_server/config.py` 中的默认值）：

**环境预设档（`APP_ENV`）**：`development`（默认，debug+本地+控制台日志）/ `staging` / `production`，可一次性预设 debug/host/log_level 等，以下变量仍可单独覆盖。

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `APP_ENV` | `development` | 环境预设档：`development` / `staging` / `production` |
| `SERVER_PORT` | `5000` | 服务端口 |
| `SERVER_HOST` | `127.0.0.1` | 监听地址；对外暴露设为 `0.0.0.0` |
| `DEBUG` | _随 APP_ENV_ | 调试模式（`true`/`1`/`yes` 开启） |
| `THREAD_NUM` | `10` | 线程池大小 |
| `SOCKETIO_ENABLED` | `false` | 是否启用 WebSocket（需安装 Flask-SocketIO） |
| `SOCKETIO_ASYNC_MODE` | `threading` | SocketIO 异步模式：`threading`(默认) / `eventlet` |
| `SOCKETIO_MAX_HTTP_BUFFER_SIZE` | `1000000` | WebSocket 单条消息大小上限（字节，默认 1MB） |
| `CORS_ORIGINS` | `*` | CORS 允许来源，`*` 或逗号分隔列表 |
| `SECRET_KEY` | _默认值_ | Flask 密钥，生产环境必须修改 |
| `MAX_CONTENT_LENGTH` | `16777216` | 请求体最大字节数（默认 16MB） |
| `SWAGGER_UI_URL` | _CDN_ | Swagger UI 资源 URL，内网可指向本地 |
| `LOG_FORMAT` | `text` | 日志格式：`text` / `json`（JSON 便于接入 ELK） |
| `LOG_MAX_BYTES` | `10485760` | 日志单文件最大字节数（默认 10MB） |
| `LOG_BACKUP_COUNT` | `5` | 保留的历史日志文件数 |
| `LOG_TO_CONSOLE` | _随 APP_ENV_ | 是否输出日志到控制台 |
| `SQLALCHEMY_URI` | _无_ | SQLAlchemy 数据库 URI |
| `SQLITE_DB_PATH` | _无_ | SQLite 数据库文件路径 |
| `DB_REFLECT_ON_START` | `true` | 启动时是否反射表结构（大数据库设 `false` 改用迁移） |
| `DB_POOL_SIZE` | `10` | 数据库连接池大小 |
| `DB_POOL_RECYCLE` | `3600` | 连接回收时间（秒） |
| `DB_POOL_PRE_PING` | `true` | 连接前 ping 检查 |
| `DB_POOL_TIMEOUT` | `30` | 获取连接超时（秒） |
| `INIT_SQL_PATH` | _无_ | SQL 初始化脚本文件路径 |
| `REDIS_URL` | _无_ | Redis 连接地址，未配置时使用内存缓存 |
| `RATE_LIMIT_ENABLED` | `false` | 是否启用接口限流（按 IP+路径 固定窗口计数） |
| `RATE_LIMIT_PER_MINUTE` | `60` | 每个 IP+路径 每分钟允许的请求数，超出返回 429 |
| `TRUSTED_PROXIES` | `127.0.0.1,::1` | 可信代理 IP 列表；`get_real_ip` 仅信任来自这些地址的 `X-Forwarded-For` |
| `SECURITY_HEADERS_ENABLED` | `true` | 是否注入安全响应头（X-Frame-Options/CSP 等） |
| `ASYNC_TASK_QUEUE_MAX` | `500` | 异步任务排队上限，超限拒绝新任务并告警 |
| `AUTH_ENABLED` | `false` | 是否启用全局认证保护（开启后 /api/ 下未豁免路径需 X-AUTH-TOKEN） |
| `AUTH_TOKEN_TTL` | `604800` | 登录 token 有效期（秒，默认 7 天） |
| `AUTH_STORE` | `memory` | 认证用户存储：`memory`（进程内，默认）/ `sqlalchemy`（需配置 DB 并迁移建表） |
| `METRICS_ENABLED` | `true` | 是否启用 Prometheus 指标（`/metrics`） |
| `WORKER_NUM` | `4` | gunicorn worker 数（仅 wsgi_gunicorn.py 生效） |
| `AUTH_ENABLED` | `false` | 是否启用全局认证保护（开启后 /api/ 下未豁免路径需 X-AUTH-TOKEN） |
| `AUTH_TOKEN_TTL` | `604800` | 登录 token 有效期（秒，默认 7 天） |
| `AUTH_STORE` | `memory` | 认证用户存储：`memory`（进程内，默认）/ `sqlalchemy`（需配置 DB 并迁移建表） |
| `METRICS_ENABLED` | `true` | 是否启用 Prometheus 指标（`/metrics`） |
| `WORKER_NUM` | `4` | gunicorn worker 数（仅 wsgi_gunicorn.py 生效） |

> 日志写入 `server.log`（追加模式，按 `LOG_MAX_BYTES` 轮转，保留 `LOG_BACKUP_COUNT` 个历史文件）；文件存储于 `storage/` 目录。
> `DateTimeUtil` 等时间工具依赖服务器本地时区，跨时区部署请同步调整服务器时区。
> 完整变量示例见 `.env.example`。

## 📖 API 示例

### 创建新的 Controller

**推荐：flask-smorest Blueprint 风格（参数校验 + API 文档）**

```python
# flask_server/controller/your_controller.py
from flask.views import MethodView
from flask_smorest import Blueprint
from marshmallow import Schema, fields
from flask_server.util import GraceResult
from flask_server.schema import GraceResultSchema

blp = Blueprint('your', 'your', url_prefix='/api/v1', description='你的接口')

class CreateSchema(Schema):
    name = fields.String(required=True, metadata={'description': '名称'})

@blp.route('/create')
class CreateView(MethodView):
    @blp.arguments(CreateSchema)
    @blp.response(200, GraceResultSchema)
    def post(self, data):
        """创建资源"""
        return GraceResult.ok(data)
```

在 `controller/__init__.py` 注册：
```python
from .your_controller import blp as your_blp
api.register_blueprint(your_blp)
```

**简单接口：@app.route 风格（无校验无文档）**

```python
# flask_server/controller/your_controller.py
from flask import request
from flask_server.app import app, json_response
from flask_server.util import Logger, GraceResult

@app.route('/api/your/endpoint', methods=['POST'])
@json_response
def your_function():
    data = request.payload
    return GraceResult.ok({'result': 'success'})
```

### 创建新的 Service

```python
# flask_server/service/your_service.py
from flask_server.module import sqlalchemy_trans
from flask_server.model import YourPO

class YourService:
    @staticmethod
    @sqlalchemy_trans
    def some_method(param):
        result = YourPO.query.filter(YourPO.field == param).all()
        return result
```

### 认证拦截器

完整的鉴权拦截器样例可参考 `examples/component/interceptor_example.py`。该样例使用 `GraceResult.business_error()` 返回业务错误码，无需补充额外方法。将其合并到 `component/interceptor.py` 后，配置需要认证的路由：

```python
need_auth_path_list = [
    '/api/user/profile',
    '/api/article/create'
]
```

请求时需要在 Header 中添加：
```
X-AUTH-TOKEN: your_token_here
```

## 🔐 安全建议

1. **生产环境配置**:
   - 保持 `DEBUG=false`（默认即为关闭）
   - 保持 `SERVER_HOST=127.0.0.1` 或置于反向代理之后，避免直接对外暴露
   - 收紧 `CORS_ORIGINS` 为具体域名，勿用 `*`
   - 修改默认端口
   - 使用强密码
   - 配置 HTTPS

2. **敏感信息保护**:
   - 通过环境变量存储密钥与数据库连接串，勿提交到版本控制
   - `.gitignore` 已排除 `.env`、`*.log`、`storage/*.db` 等

3. **数据库安全**:
   - 使用连接池
   - 限制数据库用户权限

## 📄 依赖列表

```
Flask>=3.1.2,<4.0
flask-cors>=6.0.2,<7.0
Flask-SQLAlchemy>=3.1.1,<4.0
flask-migrate>=4.0.0,<5.0
flask-smorest>=0.42.0,<1.0
marshmallow>=3.21.0,<4.0
requests>=2.32.5,<3.0
Werkzeug>=3.1.5,<4.0
PyMySQL>=1.1.2,<2.0
waitress>=3.0.2,<4.0
MarkupSafe>=3.0.3,<4.0
redis>=5.0.0,<6.0
```

> 可选（WebSocket）：`Flask-SocketIO>=5.6.0,<6.0`、`simple-websocket>=1.0.0,<2.0`、`eventlet>=0.36.0,<1.0`

## 🧪 测试

```bash
# 安装开发依赖
pip install -r requirements-dev.txt

# 运行测试（含覆盖率检查，阈值 80%）
pytest tests/ --cov=flask_server --cov-report=term

# 代码风格检查（ruff）
pip install ruff
ruff check flask_server/ tests/ examples/ server.py wsgi.py wsgi_gunicorn.py

# 依赖安全审计（pip-audit）
pip install pip-audit
pip-audit -r requirements.txt
```

测试覆盖核心工具类与模块：`GraceResult`、`CommonUtil`（含 URI 脱敏/循环引用/可信代理 IP）、`SimpleMemoryCache`（TTL/后台清理）、`DateTimeUtil`（含 UTC）、`RandomGenerator`、`DataEncryptUtil`、`KeyGenerator`（雪花 ID 并发/时钟回退）、`LocalFileStorage`（路径穿越防护/无副作用）、`RedisCache`（降级与自动恢复）、`SQLite`（真实 CRUD/LIMIT）、`SQLAlchemy`（事务提交/回滚集成测试，CI 含真实 MySQL）、`BoundedExecutor`（队列上限）、`SubprocessTask`（哨兵停止）、`rate_limit`（限流 429）、认证模块（注册/登录/登出/全局拦截）、Prometheus 指标、gunicorn 配置，以及 HTTP 层集成测试（统一响应/422 校验格式/404/request_id 头/安全响应头/healthz/readyz 探针/docs）。当前共 **175 个用例**，行覆盖率约 85%。

CI 流水线（`.github/workflows/ci.yml`）：Python 3.10/3.12 矩阵测试（覆盖率门槛 80%）+ 真实 MySQL 集成测试 + ruff 代码风格检查 + pip-audit 依赖安全审计。

## ❓ 常见问题

### 端口被占用（`Address already in use`）

修改 `.env` 中的 `SERVER_PORT`，或启动时设置环境变量：
```bash
SERVER_PORT=5001 python server.py
```

### 数据库连不上

1. 确认数据库服务已启动
2. 确认 `.env` 中 `SQLALCHEMY_URI` 或 `SQLITE_DB_PATH` 配置正确
3. MySQL 确认用户名、密码、数据库名无误；SQLite 确认路径可写
4. 使用 Docker 部署时，确认 `docker-compose up -d` 中 mysql 服务健康（`docker-compose ps`）

### Swagger UI 打不开（`/docs` 404）

1. 确认已安装 `flask-smorest`：`pip install flask-smorest`
2. 确认 `flask_server/app.py` 中 `api = Api(app)` 初始化成功
3. 查看启动日志是否有报错

### `ModuleNotFoundError: No module named 'xxx'`

```bash
# 确认在虚拟环境中
pip install -r requirements.txt
```

### Docker 容器启动失败

```bash
# 查看日志
docker-compose logs app

# 常见原因：
# 1. .env 文件不存在或配置有误
# 2. MySQL/Redis 未就绪（检查 docker-compose ps 健康状态）
# 3. 端口冲突（修改 docker-compose.yml 中端口映射）
```

### `flask db` 命令找不到

```bash
# 确认安装了 flask-migrate
pip install flask-migrate

# 确认设置了 FLASK_APP
export FLASK_APP=flask_server.app:app    # Linux/macOS
set FLASK_APP=flask_server.app:app       # Windows
```

### 参数校验返回 422

这是 flask-smorest 的参数校验拦截。检查请求体是否符合 Schema 定义（必填字段、字段类型）。查看 `/docs` 中的接口文档了解所需参数。

框架已将 422 校验错误统一为 GraceResult 格式返回，字段级错误位于 `data`：

```json
{
  "code": 1001,
  "msg": "参数错误",
  "data": {"json": {"message": ["Missing data for required field."]}}
}
```

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📝 许可证

本项目为模板项目，可自由使用和修改。

## 📞 联系方式

如有问题，请提交 Issue。
