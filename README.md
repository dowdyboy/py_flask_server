# Py Flask Server

一个基于 Flask 的轻量级 Python Web 服务模板，采用分层架构设计，提供快速搭建 RESTful API 后端服务的脚手架。

## ✨ 特性

- 🚀 **快速启动**: 开箱即用的 Flask 项目模板，零配置即可运行
- 📐 **分层架构**: 采用 Controller-Service-Model 分层设计，代码结构清晰
- 🔄 **异步支持**: 集成 WebSocket 和异步任务执行能力
- 🗄️ **数据库支持**: 内置 SQLAlchemy ORM，支持 MySQL 和 SQLite
- 💾 **文件存储**: 本地文件存储模块，简化文件上传管理
- 🔒 **安全认证**: 内置 Token 认证和权限校验机制
- 📝 **日志系统**: 统一的日志管理工具
- 🔧 **工具丰富**: 提供加密、缓存、日期处理等常用工具类
- 🌐 **跨域支持**: 内置 CORS 配置
- 📦 **统一响应**: 标准化的 JSON 响应格式

## 📋 环境要求

- Python >= 3.8
- pip

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置数据库

编辑 `flask_server/config.py` 文件：

```python
# MySQL 配置
self.sqlalchemy_uri = 'mysql+pymysql://username:password@host:3306/database_name'

# 或使用 SQLite（默认）
self.db_file_path = os.path.join(self.project_dir, 'storage', 'app.db')
```

### 3. 启动服务

```bash
python server.py
```

服务将在 `http://localhost:5000` 启动。

## 📁 项目结构

```
py_flask_server/
├── flask_server/              # 主应用目录
│   ├── __init__.py           # 模块初始化
│   ├── app.py                # Flask 应用核心配置
│   ├── config.py             # 配置文件
│   │
│   ├── component/            # 组件层（拦截器、中间件等）
│   │   └── interceptor.py   # 请求拦截器示例
│   │
│   ├── controller/           # 控制器层（路由处理）
│   │   ├── user_controller.py      # 用户相关接口
│   │   ├── article_controller.py   # 文章相关接口
│   │   ├── webui_controller.py     # WebUI 静态资源服务
│   │   └── hello_controller.py     # 示例接口
│   │
│   ├── service/              # 服务层（业务逻辑）
│   │   ├── user_service.py         # 用户业务逻辑
│   │   └── article_service.py      # 文章业务逻辑
│   │
│   ├── model/                # 数据模型层
│   │   └── po/               # 持久化对象
│   │       ├── user.py       # 用户模型
│   │       └── article.py    # 文章模型
│   │
│   ├── module/               # 核心模块
│   │   ├── sqlalchemy.py    # SQLAlchemy 数据库模块
│   │   ├── sqlite.py        # SQLite 数据库模块
│   │   ├── simple_memory_cache.py  # 内存缓存模块
│   │   └── local_file_storage.py   # 本地文件存储
│   │
│   └── util/                 # 工具类
│       ├── logger.py        # 日志工具
│       ├── grace_result.py  # 统一响应格式
│       ├── async_task_util.py     # 异步任务工具
│       ├── data_encrypt_util.py   # 数据加密工具
│       ├── date_time_util.py      # 日期时间工具
│       ├── random_generator.py    # 随机数生成器
│       ├── key_generator.py       # 密钥生成器
│       └── common.py        # 通用工具
│
├── webui/                   # 前端静态资源
│   └── index.html          # 前端页面
│
├── storage/                # 文件存储目录
│   └── temp.txt
│
├── requirements.txt         # 依赖列表
├── server.py               # 服务启动入口
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

1. **请求拦截** → `component/interceptor.py`
2. **参数解析** → `app.py` 中的 `before_request` 钩子
3. **路由处理** → `controller/*.py`
4. **业务逻辑** → `service/*.py`
5. **数据操作** → `module/*.py`
6. **响应封装** → `util/grace_result.py`

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
from flask_server.module import sqlite

result = sqlite().execute_sql("SELECT * FROM users WHERE uid = ?", ('123',))
```

### 4. 缓存使用

```python
from flask_server.module import memory_cache as cache

# 设置缓存（默认1小时过期）
cache.set('key', 'value')

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
from flask_server.module import LocalFileStorage

storage = LocalFileStorage()
file_path = storage.save_file(file_obj, 'uploads/')
```

## 🛠️ 配置说明

在 `flask_server/config.py` 中修改配置：

```python
class Config:
    # 服务配置
    port = 5000           # 服务端口
    thread_num = 10       # 线程池大小
    debug = True         # 调试模式

    # 日志配置
    log_filename = 'server.log'
    log_level = logging.DEBUG

    # 数据库配置
    sqlalchemy_uri = 'mysql+pymysql://user:pass@host:3306/db'

    # 文件存储配置
    file_saved_path = 'storage'

    # WebUI 配置
    webui_dir = 'webui'
```

## 📖 API 示例

### 创建新的 Controller

```python
# flask_server/controller/your_controller.py
from flask_server.app import app, json_response
from flask_server.util import Logger, GraceResult

Logger.info("your_controller.py loaded")

@app.route('/api/your/endpoint', methods=['POST'])
@json_response
def your_function():
    data = request.payload
    # 处理业务逻辑
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

在 `component/interceptor.py` 中配置需要认证的路由：

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
   - 关闭 `debug = False`
   - 修改默认端口
   - 使用强密码
   - 配置 HTTPS

2. **敏感信息保护**:
   - 不要将敏感信息提交到版本控制
   - 使用环境变量存储密钥

3. **数据库安全**:
   - 使用连接池
   - 限制数据库用户权限

## 📄 依赖列表

```
Flask==3.0.2
Flask_Cors==4.0.0
Flask_SocketIO==5.3.6
flask_sqlalchemy==3.1.1
requests==2.31.0
Werkzeug==3.0.1
PyMySQL==1.1.1
waitress==3.0.2
```

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📝 许可证

本项目为模板项目，可自由使用和修改。

## 📞 联系方式

如有问题，请提交 Issue。
