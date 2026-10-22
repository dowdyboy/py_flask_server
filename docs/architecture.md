# 架构说明

本文说明这个脚手架的**分层、启动流程、请求生命周期、配置体系与进程模型**，
用于回答"我要加一个功能，该动哪个文件"这类问题。

---

## 1. 设计目标

| 目标 | 落地方式 |
|---|---|
| 复制即用、建文件即出接口 | `controller/`、`handler/` 目录扫描自动装配 |
| 分层清晰、依赖单向 | controller → service → model；module/util 下沉 |
| 生产可用 | 安全基线、可观测性、限流、优雅关闭、多进程注意事项 |
| 可测试 | `create_app()` 工厂 + 配置注入，不依赖导入顺序 |

---

## 2. 分层与依赖方向

```
              HTTP 请求                        TCP / UDP 消息
                 │                                  │
        ┌────────▼────────┐              ┌──────────▼──────────┐
        │   controller/   │              │      handler/       │
        │ 路由 / 参数校验  │              │  消息处理（协议层）  │
        └────────┬────────┘              └──────────┬──────────┘
                 │                                  │
        ┌────────▼────────┐                         │
        │    service/     │  业务编排（简单业务可省略）│
        └────────┬────────┘                         │
                 │                                  │
        ┌────────▼────────┐                         │
        │   model/po/     │  ORM 模型               │
        └────────┬────────┘                         │
                 │                                  │
        ┌────────▼──────────────────────────────────▼────────┐
        │  module/  基础设施：DB / 缓存 / 文件存储 / 协议服务器 │
        └────────┬───────────────────────────────────────────┘
                 │
        ┌────────▼────────────────────────────────┐
        │  component/  横切：限流 / 认证 / 指标     │  ← 装配到 app 上
        └────────┬────────────────────────────────┘
                 │
        ┌────────▼────────────────────────────────┐
        │  util/   日志 / 结果 / ID / 加密 / 异步   │  ← 不依赖上层
        └─────────────────────────────────────────┘
```

**依赖规则（从上往下，不可反向）**

- `util/` 只依赖标准库与 `config`，不 import 任何其它业务层
- `module/` 可依赖 `util/`，不依赖 `component/`、`controller/`
- `component/` 可依赖 `module/`、`util/`，不依赖 `controller/`
- `controller/` 可依赖 `service/`、`model/`、`util/`、`schema/`
- `app.py` 是最上层装配者，可 import 所有层（且只在函数内部 import，避免循环依赖）

---

## 3. 目录职责

| 目录 | 职责 | 新文件是否自动生效 |
|---|---|---|
| `flask_server/app.py` | `create_app()` 工厂、请求钩子、异常处理、安全头 | —（需改装配逻辑） |
| `flask_server/config.py` | 配置解析、`Config`、`ConfigProxy` | 改配置项需新增字段 |
| `flask_server/controller/` | 路由与入参校验 | ✅ 自动注册 Blueprint / `register(app)` |
| `flask_server/handler/` | TCP/UDP 消息处理器 | ✅ 自动导入并注册装饰器 |
| `flask_server/task/` | 后台任务（`@task` 声明） | ✅ worker 启动时自动导入（`auto_import_tasks`） |
| `flask_server/service/` | 业务编排（可选层） | ❌ 需被 controller 调用 |
| `flask_server/model/po/` | 声明式 ORM 模型 | ✅ 被 `model` 包导入后进入迁移检测 |
| `flask_server/module/` | 基础设施单例（DB / 缓存 / 存储 / 协议服务器 / 任务队列） | 需在 `module/__init__.py` 导出 |
| `flask_server/component/` | 横切组件（限流/认证/指标） | 需在 `init_components()` 里装配 |
| `flask_server/util/` | 无状态工具 | 需在 `util/__init__.py` 导出 |
| `flask_server/schema/` | marshmallow Schema | ❌ 需被 controller 引用 |

---

## 4. 启动流程

三个入口都先 `import flask_server`，此时模块底部的 `app = create_app()` 完成装配。

```
server.py（开发）        wsgi.py（waitress 生产）        wsgi_gunicorn.py（多进程生产）
      │                        │                              │
 打印 banner              打印 banner                    打印 banner（含 worker 数）
      │                        │                              │
 启协议服务器*          启协议服务器                     gunicorn 启动
      │                        │                              │
 启内嵌任务 worker†     启内嵌任务 worker†              post_worker_init：
      │                        │                          workers == 1 才启协议服务器
 app.run(debug)      create_server + 信号处理器               │
      │                        │                          （内嵌 worker 见 †）
      │                   run() → 收到信号 → 停内嵌 worker →
      │                   停协议服务器 → 释放 DB/Redis → 退出
      └────────────────────────┴──────────────────────────────┘

独立部署（推荐）：python scripts/worker.py --concurrency 4
```

\* `server.py` 在 debug + reloader 模式下只由**子进程**启动协议服务器，避免父子抢端口。
† 仅 `TASK_WORKER_EMBEDDED=true` 且已注册任务时启动；内存后端下任务只在入队的进程内可见，
  多进程部署请改用独立 `scripts/worker.py`（消费 Redis 队列）。

### create_app() 装配顺序

| 步骤 | 内容 | 说明 |
|---|---|---|
| 1 | `Flask(__name__)`，写入 `app.extensions['flask_server.config']` | 必须最先，后续都从这里解析配置 |
| 2 | CORS、`SECRET_KEY`、`MAX_CONTENT_LENGTH`、默认密钥告警 | 创建期读注入的配置 |
| 3 | flask-smorest：OpenAPI/文档配置 + `Api(app)` | `/docs`、`/openapi.json` |
| 4 | SocketIO（按配置，默认关闭） | 进程级单例 |
| 5 | `init_SQLAlchemy` / `init_Migrate` | 进程级单例，二次创建直接复用 |
| 6 | `register_request_hooks` | 参数解析、request_id、安全头、teardown |
| 7 | `register_error_handlers` | 4 类异常 → 统一 `{code,msg,data}` |
| 8 | `init_components` | 限流 → 认证 → 指标（顺序决定执行序） |
| 9 | `register_controllers` | Blueprint + 模块级 `register(app)` |
| 10 | 挂 `app.api` / `app.socketio` / `app.framework_config` | 便于调用方取用 |

---

## 5. 请求生命周期

### before_request（按注册顺序执行）

| 序 | 钩子 | 作用 |
|---|---|---|
| 1 | `init_request_info` | 生成/清洗 `X-Request-Id`，写入 `g` 与日志上下文，初始化 `request.payload` |
| 2 | `parse_request_param` | `request.params = request.args` |
| 3 | `parse_request_json` | JSON body → `request.payload`（非 dict 归一为 `{}`） |
| 4 | `parse_request_form_data` | 表单/多部分 → `request.payload` |
| 5 | `rate_limit_check` | 限流（IP+路径 与 IP 总配额） |
| 6 | `auth_interceptor` | **仅 `AUTH_ENABLED=true` 时存在**，校验 `X-AUTH-TOKEN` |
| 7 | `metrics_start_timer` | 记录开始时间（`METRICS_ENABLED=false` 时零开销） |

### after_request（注册顺序的反向执行）

| 执行序 | 钩子 | 作用 |
|---|---|---|
| 1 | `metrics_record` | 计数 + 延迟直方图 |
| 2 | `set_security_headers` | CSP / HSTS / nosniff 等 |
| 3 | `set_request_id_header` | 回写 `X-Request-Id` |
| 4 | `cors_after_request` | flask-cors 注入 CORS 头 |

### 异常处理

| 异常 | HTTP | code | 说明 |
|---|---|---|---|
| `HTTPException` 4xx | 原状态码 | -1 | 404 统一文案；其余回显 description |
| `HTTPException` 5xx | 原状态码 | -1 | 生产环境脱敏为"服务器内部错误" |
| `UnprocessableEntity`（422） | 422 | 1001 | 字段级错误放 `data` |
| `KeyError` | 400 | 1001 | `payload['x']` 缺字段 |
| 其它 `Exception` | 500 | -1 | 记录 traceback，不回显细节 |

### 路由匹配

- 静态规则优先于 `<converter>`：`/docs`、`/metrics`、`/openapi.json` 不会被静态站点 catch-all 吞掉
- `/<path:filename>`（webui catch-all）对 `api` / `api/...` 前缀直接 404，不走 SPA 回退
- 带静态资源扩展名（`.css/.js/.png` 等）且文件不存在 → 404，而非回退 `index.html`

---

## 6. 配置体系

```
环境变量  ─┐
项目根 .env ─┼─→ Config(...) ─→ default_config（进程默认）
代码默认值  ─┘                      │
                                    │  create_app(framework_config=Config({...}))
                                    ▼
                    app.extensions['flask_server.config']
                                    │
                    config（ConfigProxy）按上下文解析
                                    │
              ┌─────────────────────┴─────────────────────┐
        有 app 上下文                               无 app 上下文
    → 该 app 注入的配置                      → default_config
```

**两条使用规则**

| 场景 | 怎么读配置 | 原因 |
|---|---|---|
| 创建期（`create_app` 内、`init_*` 注册函数） | 用传入的 `cfg` 局部变量 | 此时还没有请求上下文，代理会回退默认配置 |
| 请求期（钩子、视图、组件） | 直接用 `config.xxx` | 代理自动解析到当前 app 的配置 |

**优先级**：`Config(overrides)` > 环境变量 > `.env` > 代码默认值。

**兼容性**：`from flask_server import config` 拿到的仍是同一个对象（代理），
`config.xxx` 读写与改造前完全一致；`monkeypatch.setattr(config, 'x', y)` 在无
app 上下文时作用于进程默认配置，既有测试无需修改。

---

## 7. 进程模型与单例边界

| 组件 | 作用域 | 多 worker / 多副本时的行为 |
|---|---|---|
| `db` / Flask-Migrate | 进程内（fork 前创建，worker 继承） | 各 worker 独立连接池；**不同 URI 需多进程部署** |
| SocketIO | 进程内 | eventlet monkey patch 是**导入期**决定的 |
| Prometheus 指标 | 进程内 | 需 `PROMETHEUS_MULTIPROC_DIR` 才能聚合 |
| 内存缓存 / 限流计数 / 认证 token | 进程内 | 需 `REDIS_URL` 才能跨 worker 一致 |
| 任务队列 | 内存（默认）/ Redis | 多 worker 必须配 `REDIS_URL`，否则各消费各的；生产用独立 `scripts/worker.py` |
| 日志 handler | 进程内 | 多 worker 写同一文件有轮转竞态，建议容器日志 |
| TCP/UDP 协议服务器 | **每进程绑定同一端口** | 由 `post_worker_init` 在 `workers > 1` 时禁启 |
| 雪花 ID worker | 进程内 | 按 `crc32(hostname)+pid` 派生；严格唯一请显式配置或改用 UUIDv7 |

> 结论：**同进程多实例只适合"共享同一份外部资源、配置不同"的场景**；
> 需要完全隔离（不同 DB/不同缓存）时应多进程/多容器部署。

---

## 8. 扩展点

### 新增一个 HTTP 接口

```python
# flask_server/controller/user_controller.py
from flask.views import MethodView
from flask_smorest import Blueprint
from ..util import GraceResult
from ..schema import GraceResultSchema

blp = Blueprint('user', 'user', url_prefix='/api/v1/users')

@blp.route('/<string:uid>')
class UserView(MethodView):
    @blp.response(200, GraceResultSchema)
    def get(self, uid):
        return GraceResult.ok({'uid': uid})
```

保存即生效（自动注册 Blueprint）。需要 `@app.route` 风格时用模块级 `def register(app)`。

### 新增一个 TCP/UDP 消息处理器

```python
# flask_server/handler/my_handler.py
from flask_server.module import tcp_server

@tcp_server.on_message
def on_message(conn, data, addr):
    conn.sendall(b'ack')
```

保存即生效；服务器由入口的 `start_protocol_servers()` 启动（`TCP_ENABLED=true`）。

### 新增一个横切组件（中间件）

1. `component/my_component.py` 提供 `def init_my_component(app, cfg=None)`
2. 在 `component/__init__.py::init_components()` 里按顺序调用

### 新增基础设施

`module/xxx.py` 暴露单例 → 在 `module/__init__.py` 导出（注意装配顺序）。

---

## 9. 改动决策表

| 想做的事 | 该动哪里 |
|---|---|
| 加接口 / 改入参校验 | `controller/` + `schema/` |
| 加业务逻辑 | `service/`（或先写在 controller，后续再抽） |
| 加表 / 改字段 | `model/po/` + `flask db migrate` |
| 加中间件（鉴权/审计/灰度） | `component/` + `init_components()` |
| 加缓存/队列/对象存储 | `module/` + `module/__init__.py` |
| 加配置项 | `config.py::Config` + `.env.example` + `docs/configuration.md` |
| 加协议接入 | `handler/` + 配置 `TCP_*/UDP_*` |
| 改统一响应/错误码 | `util/grace_result.py` + `app.py` 的异常处理 |
| 改启动/关闭行为 | `server.py` / `wsgi.py` / `wsgi_gunicorn.py` |

---

## 10. 已知约束与取舍

1. **同进程多实例共享** `db` / SocketIO / Prometheus 指标 / 日志 handler —— 见第 7 节
2. **eventlet monkey patch 在导入期决定**：`SOCKETIO_ENABLED` / `SOCKETIO_ASYNC_MODE`
   必须在 import 前由环境变量确定，无法按 app 注入
3. **日志配置是进程级的**：`LOG_LEVEL` 等不随 app 变化
4. **DB 反射在首次绑定时执行**：大库建议 `DB_REFLECT_ON_START=false`
5. **静态站点 catch-all 会拦截任意路径**：新增 `@app.route` 时不必担心顺序，
   Werkzeug 按规则权重匹配；但纯路径型规则（`/<x>`）与 catch-all 并存时要注意优先级
