# 快速上手与教程

## 从模板创建新项目

```bash
# 一键生成（推荐，自动清理 .git/缓存并替换 LICENSE 署名）
python scripts/scaffold.py my_new_project --author "Your Name"
cd my_new_project

# 或手动复制
# cp -r py_flask_server my_new_project && cd my_new_project
```

```bash
# 创建虚拟环境（推荐）
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt -r requirements-dev.txt

# 配置环境变量（自动加载，无需 export）
cp .env.example .env

# 启动
python server.py        # http://127.0.0.1:5000/docs
```

## 教程：创建第一个完整接口

本教程带你从零创建一个带数据库、参数校验、API 文档的完整用户接口。

### 第 1 步：配置数据库

编辑 `.env`：

```bash
# 使用 SQLite（最简单，无需额外安装）
SQLITE_DB_PATH=storage/app.db

# 或使用 MySQL（建议显式指定 charset）
# SQLALCHEMY_URI=mysql+pymysql://root:password@localhost:3306/mydb?charset=utf8mb4
```

### 第 2 步：声明 Model

脚手架已内置示例模型 `flask_server/model/po/user.py`（认证模块 sqlalchemy 存储模式复用），
已自动导出并纳入迁移检测。**无需新建**，可按需修改字段，或参照它新建其他模型：

```python
from flask_server.module.sqlalchemy import sqlalchemy
from datetime import datetime

db = sqlalchemy()


class UserPO(db.Model):
    __tablename__ = 'user'

    uid = db.Column(db.String(64), primary_key=True)
    username = db.Column(db.String(128), unique=True, nullable=False)
    # PBKDF2 存储格式 salt$iterations$hash（约 168 字符），列宽需 ≥ 256
    passwd = db.Column(db.String(256), nullable=False)
    create_time = db.Column(db.DateTime, default=datetime.now)
```

> 注意：新建模型文件后需在 `flask_server/model/po/__init__.py` 中导出
> （`from .xxx import XxxPO`），Flask-Migrate 才能识别建表。

### 第 3 步：运行数据库迁移

```bash
python scripts/db.py init          # 首次初始化迁移目录
python scripts/db.py migrate "create user table"   # 生成迁移脚本
python scripts/db.py upgrade       # 执行迁移（真正建表）
```

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

**导出注册**：在 `flask_server/schema/__init__.py` 中加入导入（Controller 自动扫描，
但 Schema 需显式导出才能 `from flask_server.schema import ...`）：

```python
from .user_schema import UserCreateSchema, UserResponseSchema
```

### 第 5 步：编写 Service（业务逻辑）

创建 `flask_server/service/user_service.py`：

```python
from flask_server.module import sqlalchemy, sqlalchemy_trans
from flask_server.model import UserPO
from flask_server.util import DataEncryptUtil, RandomGenerator


class UserService:

    @staticmethod
    @sqlalchemy_trans
    def create(username, password):
        uid = RandomGenerator.secrets_token(16)
        user = UserPO()
        user.uid = uid
        user.username = username
        user.passwd = DataEncryptUtil.pbkdf2_hmac(password)   # 加盐哈希，勿用明文/sha256
        sqlalchemy().session.add(user)
        return uid

    @staticmethod
    def get_by_uid(uid):
        return UserPO.query.filter(UserPO.uid == uid).first()
```

**导出注册**：在 `flask_server/service/__init__.py` 中加入导入：

```python
from .user_service import UserService
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

### 第 7 步：完成！

**无需任何注册代码**——框架自动扫描 `controller/` 目录并注册所有 Blueprint。

```bash
python server.py
```

测试创建用户：

```bash
curl -X POST http://127.0.0.1:5000/api/v1/users/ \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","password":"123456"}'
```

查看 API 文档：浏览器打开 `http://127.0.0.1:5000/docs`。

## 两种 Controller 写法对比

| | `@app.route` 风格 | flask-smorest `blp` 风格 |
|---|---|---|
| 参数校验 | 手动从 `request.payload` 取值 | `@blp.arguments(Schema)` 自动校验 |
| API 文档 | 无 | 自动生成 Swagger UI |
| 推荐度 | 简单接口可用 | **推荐**，生产项目首选 |

```python
# @app.route 风格（最简示例）
from flask_server.app import app, json_response
from flask_server.util import GraceResult

@app.route('/hello', methods=['GET'])
@json_response
def hello():
    return GraceResult.ok({'message': 'Hello, World!'})
```

## 认证拦截器

开箱认证见 [API 约定](api-conventions.md#认证模块)；自定义鉴权拦截器参考
`examples/component/interceptor_example.py`，合并到 `flask_server/component/interceptor.py` 后配置：

```python
need_auth_path_list = ['/api/user/profile']
```

请求时需携带请求头 `X-AUTH-TOKEN: your_token_here`。

## 测试接口

启动服务后可直接使用 `examples/api.http`（VS Code REST Client）一键请求全部端点，
或用 curl / Swagger UI（`/docs`）手动测试。
