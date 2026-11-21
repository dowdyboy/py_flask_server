# API 约定

## 统一响应格式

业务接口（`/api/` 下的 controller）返回统一 JSON：

```json
{
  "code": 0,
  "msg": "成功",
  "data": {}
}
```

> 例外：`/metrics` 返回 Prometheus 文本（未启用或依赖缺失时为 503），`/docs` 返回 Swagger UI 的 HTML，
> `/<path:filename>`（webui catch-all）返回静态文件——它们不走统一 JSON 格式。

## 错误码表

错误码的单一事实来源是 [`flask_server/util/error_code.py`](../flask_server/util/error_code.py)
的 `ErrorCode` 枚举；controller 用 `GraceResult.business_error(ErrorCode.XXX, ...)` 引用，
新增错误码时同步本表。

### 框架内置

| code | 含义 | HTTP 状态 |
|---|---|---|
| `0` | 成功 | 200 |
| `1001` | 参数错误 | 400 / 422 |
| `-1` | 内部错误 | 500 |
| `4001` | 认证错误（用户名已存在 / 用户名或密码错误） | 400 / 401 |
| `4002` | 未登录 / Token / refresh_token 无效或已过期；登录成功但会话不可读 | 401 / 503 |
| `4003` | 登录失败次数过多，已临时锁定 | 429 |
| `5030` | 依赖未就绪（DB/Redis 故障，就绪探针） | 503 |

### HTTP 状态码约定

| 状态码 | 场景 | 响应格式 |
|---|---|---|
| 422 | 参数校验失败（flask-smorest） | 统一格式，字段错误在 `data` |
| 404 | 资源不存在 | 统一格式（code=-1） |
| 429 | 触发限流或登录锁定 | 统一格式（限流 code=-1，锁定 code=4003）；响应头带 `Retry-After`（剩余秒数） |
| 401 | 未登录 / Token 无效或已过期 | 统一格式（code=4001/4002） |
| 503 | 依赖未就绪（DB/Redis 故障）；登录成功但会话不可读 | 统一格式（code=5030 / 4002） |

> **`KeyError` 不映射为 400**：未捕获的 KeyError 几乎总是服务端缺陷（字段名写错/数据缺失），
> 现在按 500 处理并记录 traceback。若业务想对"缺字段"返回 400，请显式校验
> （`@blp.arguments` schema，或 `request.payload.get(...)` + `GraceResult.param_error()`）。

业务自定义错误码：`GraceResult.business_error(2001, '用户不存在')`。

> 说明：controller 除了 `@blp.response(200, ...)` 之外，用 `@blp.alt_response(...)` 把实际
> 会返回的错误码（400/401/429/503）逐个写进了 `/openapi.json`，并统一引用 `GraceResult`
> schema。因此用这份 spec 生成的 SDK 能识别错误响应，不必只依赖本页的错误码表。

### 422 校验错误结构

字段级错误位于 `data`：

```json
{
  "code": 1001,
  "msg": "参数错误",
  "data": {"json": {"message": ["Missing data for required field."]}}
}
```

**非法 JSON Body**：`before_request` 的 JSON 解析使用 `get_json(silent=True)`，不会抢先抛
裸 400；请求继续交给视图层——带 `@blp.arguments` 的接口由 webargs 产出
**400 + `code=1001` + 字段级 `data`**（与 422 同形状），body 可选的接口（如 `/logout`）
则按 `payload={}` 继续执行。

**其余 4xx**：不带 `messages` 的 4xx（如 Werkzeug 直接抛出的 404/405、未匹配路由）仍是 `code=-1`。

## 请求参数解析

框架自动解析多种格式（`before_request` 钩子）：

- **GET 参数** → `request.params`
- **JSON Body** → `request.payload`（Content-Type: application/json）
- **Form Data** → `request.payload`（application/x-www-form-urlencoded）
- **Multipart** → `request.payload`（包含文件，表单字段与文件合并）

## 认证模块

接口：`POST /api/v1/auth/register`、`/login`、`/refresh`、`/logout`、`/logout_all`、
`/password`、`GET /me`（见 [快速上手](getting-started.md) 与配置 `AUTH_*` 变量）。

**双令牌机制**：
- 登录返回 `{token, refresh_token}`：`token` 用于请求头 `X-AUTH-TOKEN`（有效期 `AUTH_TOKEN_TTL`），
  `refresh_token` 用于续期
- `POST /api/v1/auth/refresh`（body `{"refresh_token": "..."}`）换取新令牌——**旧 refresh 作废（单次使用）**
- **refresh 重用检测**：已使用的 refresh_token 在 5 分钟内再次出现（被窃取/重放）时，判定为泄露，
  吊销该用户**全部** access/refresh token（客户端需重新登录）。正常轮换不受影响
- `POST /api/v1/auth/logout` 使当前 access token 失效；body 可选携带 `{"refresh_token": "..."}`
  一并吊销 refresh（彻底结束会话），不携带则 refresh 到期自然失效（向后兼容）
- `POST /api/v1/auth/logout_all`（需登录）吊销该用户全部会话（登出所有设备）
- `POST /api/v1/auth/password`（需登录，body `{"old_password","new_password"}`）修改密码；
  成功后该用户全部旧会话立即失效，需用新密码重新登录。旧密码错误统一返回 401（不区分账号是否存在）

**防爆破**：同一 **(用户名, 来源 IP)** 连续登录失败 `AUTH_LOGIN_MAX_FAILS`（默认 5）次后锁定
`AUTH_LOGIN_LOCK_SECONDS`（默认 300）秒，锁定期间正确密码也拒绝（code 4003，HTTP 429，
响应头 `Retry-After` 为**锁定剩余秒数**）。

> 锁定键按 (用户名, IP) 隔离，而不是只按用户名：否则攻击者遍历已知用户名、各打几次错误密码，
> 就能把全站账号逐个锁死（锁定型 DoS）。代价是分布式撞库可绕过单 IP 阈值——跨 IP 失败累计
> 达 50 次会打一条告警日志，真正的拦截请交给 `RATE_LIMIT_ENABLED` / WAF。
>
> `refresh_token` 换取新令牌时会校验用户是否仍存在（用户被删除 → 401，而非 500）。

**令牌与计数存储（自动降级）**：
- 未配置 `REDIS_URL`：access/refresh token 与防爆破计数存进程内内存（单实例可用，重启失效）
- 配置 `REDIS_URL`：自动改用 Redis（多 worker 共享，任意 worker 签发的 token 都能校验）；
  **Redis 不可达时自动回退内存缓存**（单实例下登录/防爆破仍可用，恢复后自动回 Redis）
- `AUTH_STORE=sqlalchemy` 只解决用户数据持久化（UserPO 建表），**token 共享仍依赖 Redis**；
  多进程部署建议配置 `REDIS_URL`（启动 banner 会告警）
- 已签发 token 会按用户维护索引（每用户最多保留 20 个），用于改密与"登出所有设备"时批量吊销；
  索引与 token 存在同一缓存（内存/Redis），多实例下吊销是**尽力而为**：跨 worker 的场景请确保
  配置 `REDIS_URL`，否则每个进程只能看到自己签发的会话

**保护单个接口**（推荐）：

```python
from flask_server.component.auth import login_required

@blp.route('/profile')
class ProfileView(MethodView):
    @blp.response(200, GraceResultSchema)
    @blp.doc(security=[{'XAuthToken': []}])   # 写进 OpenAPI：该接口需要 X-AUTH-TOKEN
    @login_required          # 校验 X-AUTH-TOKEN，通过后 request.info['uid'] 可用
    def get(self):
        uid = request.info['uid']
        return GraceResult.ok({'uid': uid})
```

> `/openapi.json` 的 `components.securitySchemes.XAuthToken` 已声明 `X-AUTH-TOKEN` 请求头；
> 受保护接口同时用 `@blp.doc(security=[{'XAuthToken': []}])` 标注，生成的客户端可以直接
> 带上认证头。`/api/v1/auth/me` 已作为示例标注。

**全局保护**：`AUTH_ENABLED=true` 时，`/api/` 下除 auth/文档/健康检查外的路径
都需要 `X-AUTH-TOKEN` 请求头（CORS 预检 `OPTIONS` 请求自动放行，不影响跨域前端）。

> 安全提示：响应绝不含密码哈希；生产环境务必修改 SECRET_KEY 并配置 HTTPS。

> ⚠️ `AUTH_STORE=sqlalchemy` 时，数据库故障会导致全部受保护 `/api/` 请求返回 500
> （用户数据源不可用时无法降级为 401）；数据库可用性由 `readyz` 探针监测。

## 分页与 ETag

**脚手架内置分页工具**（推荐，双后端/双数据源语义一致）：

```python
from flask_smorest import Blueprint
from flask_server.schema import PageSchema, PaginationQuerySchema
from flask_server.util import paginate

@blp.route('/items')
class ItemListView(MethodView):
    @blp.arguments(PaginationQuerySchema, location='query')
    @blp.response(200, PageSchema)
    def get(self, args):
        return paginate(ItemPO.query, page=args['page'],
                        per_page=args['per_page']).to_dict()
```

`paginate()` 对 Flask-SQLAlchemy Query 走原生 `paginate()`（不全量加载），对普通列表按切片；
响应体固定为 `{items, page, per_page, total, pages}`。`page`/`per_page` 非法值会钳到
`1`/`[1,100]`，不会 500。

**flask-smorest 自带分页**（`@blp.paginate()`；注意 `Page` 不是 Schema，而是分页器类，
其元数据走响应头 `X-Pagination`）：

```python
from flask.views import MethodView
from flask_smorest import Blueprint

blp = Blueprint('user', 'user', url_prefix='/api/v1/users')


@blp.route('/page')
class UserPageView(MethodView):
    @blp.response(200, UserSchema(many=True))
    @blp.paginate()          # 解析 page/page_size，并向视图注入 pagination_parameters
    def get(self, pagination_parameters):
        users, total = UserService.list(
            page=pagination_parameters.page,
            per_page=pagination_parameters.page_size,
        )
        pagination_parameters.item_count = total   # 不设置则不会返回 X-Pagination
        return users
```

`@blp.paginate()` 从 query 解析 `page`（默认 1）/ `page_size`（默认 10，上限 100），
`pagination_parameters` 是 `PaginationParameters`（`.page` / `.page_size` / `.item_count`）。
**响应体只是数据列表**，分页元数据放在响应头 `X-Pagination`（JSON：`total` / `total_pages` /
`first_page` / `last_page` / `page` / `previous_page` / `next_page`）。

如果直接拿到的是 Flask-SQLAlchemy `paginate()` 对象，它提供 `.items` / `.page` / `.per_page` /
`.total`：把 `.items` 作为视图返回值、用 `.total` 回填 `pagination_parameters.item_count` 即可。

**ETag 缓存协商**：框架默认对 `GET/HEAD` 的 JSON 成功响应自动生成 `ETag`
（`ETAG_ENABLED=true`，默认开启；静态文件/流式响应不参与）。客户端带
`If-None-Match` 命中时自动返回 304（空 body），无需业务代码配合。
需要按业务语义自定义时，也可以只用 flask-smorest 的装饰器：

```python
@blp.route('/articles/<string:aid>')
class ArticleView(MethodView):
    @blp.etag  # 校验 If-None-Match，命中返回 304
    @blp.response(200, GraceResultSchema)
    def get(self, aid):
        article = ArticleService.get(aid)
        if article is None:
            return GraceResult.business_error(4004, '文章不存在'), 404
        return GraceResult.ok(article)
```

> ETag 与 `@blp.arguments` 同时使用时需把 `@blp.etag` 放在最外层。

## 限流

`RATE_LIMIT_ENABLED=true` 时启用，顺延窗口计数（`RATE_LIMIT_PER_MINUTE`，默认 60 次/分钟；
每次请求刷新窗口 TTL，活动期内计数不会按整分钟重置），
超限返回 429（code=-1），响应头 `Retry-After: 60`（= 窗口长度）供客户端退避。
所有响应（含 429）都带 `X-RateLimit-Limit` / `X-RateLimit-Remaining` / `X-RateLimit-Reset`
（Reset 为最长等待秒数，窗口是顺延式而非固定时钟窗口）。

**双层配额**（原子 INCR 计数，防并发竞态绕过）：

- `rate:{ip}:{path}` 路径级配额：同一 IP 对同一路径的请求上限
- `rate:{ip}` IP 级总配额：同一 IP 的**全部**请求上限（兜底，防止攻击者用随机路径
  绕过路径级配额——webui catch-all 使任意唯一路径各有独立计数键）

**豁免端点**（不计数，防编排系统误伤）：`/metrics`、`/api/v1/healthz`、`/api/v1/readyz`、
`/api/v1/health`（探针被 429 会导致实例被摘除 → 流量集中 → 更 429 的雪崩循环）。
豁免判定对尾斜杠归一（`/metrics/` 与 `/metrics` 同豁免），防采集端尾斜杠 URL 被误限流；
超长路径（>512 字符）的计数键自动哈希截断，防超长 URL 撑爆缓存键内存。

**存储与降级**：

- `RATE_LIMIT_STORE=memory`（默认，进程内）/ `redis`（多实例准确，需配置 `REDIS_URL`）
- Redis 主存储**运行中不可达**时自动回退内存计数（单实例下限流仍生效，恢复后自动回 Redis）；
  `RATE_LIMIT_STORE=redis` 但未配置 `REDIS_URL` 时同样回退内存存储

**客户端 IP 解析**：`get_real_ip` 仅在来源位于 `TRUSTED_PROXIES`（支持 CIDR）时信任
`X-Forwarded-For`；解析规则是**从右向左取第一个"非可信代理且为合法 IP"的条目**，否则使用
`remote_addr`。

> 为什么不是取最左项：反向代理普遍采用**追加**语义（nginx `$proxy_add_x_forwarded_for`、
> Traefik 等），头形如 `客户端伪造值, ..., 代理追加的真实地址`。最左项由客户端提供、可任意伪造，
> 取它等于让攻击者每次换个假 IP 就绕过 IP 限流并污染审计日志。仅当整条链路都是可信代理时
> 才回退最左项。

## Prometheus 指标

`GET /metrics`（`METRICS_ENABLED=true` 默认开；关闭或未安装 `prometheus-client` 时返回 503）：

| 指标 | 标签 | 含义 |
|---|---|---|
| `http_requests_total` | `method` / `status` / `route` | 请求计数（`route` 用路由规则聚合，未匹配路由固定为 `__unmatched__`，防高基数） |
| `http_request_duration_seconds` | `method` / `route` | 请求延迟直方图（`_bucket` / `_sum` / `_count`） |
| `http_rate_limited_total` | `scope` | 被限流拒绝（429）的请求数；`scope="path"` 为路径级配额命中，`scope="ip"` 为 IP 级总配额命中 |
| `auth_login_locked_total` | _无_ | 因登录锁定被拒绝（429 / code 4003）的登录次数；不按用户名/IP 打标签（避免高基数与账号信息泄漏） |
| `task_processed_total` | `result` | 任务执行结果：`succeeded` / `retried` / `dead`（固定低基数） |
| `task_rejected_total` | `reason` | 任务**未执行**即被拒绝/进入死信的数量：`reason="unavailable"`（队列后端不可用，`reserve` 失败）、`reason="unregistered"`（任务名未注册）、`reason="max_retries"`（handler 重试耗尽） |

> 标签值全部来自固定集合（不取请求路径、用户名等用户输入），保证低基数、避免指标基数爆炸。
> 业务代码通过 `flask_server.component.metrics._inc(counter, **labels)` 自增：`METRICS_ENABLED=false`、
> 依赖缺失或标签非法时静默跳过，绝不影响主流程。

## 日志与链路追踪

- `Logger.debug/info/warn/error`（`flask_server.util.Logger`）；`Logger.warning` 是 `warn` 的等价别名，
  `Logger.exception(txt)` 以 ERROR 级别记录并附带堆栈（**只能在 `except` 块内调用**）
- 每次请求自动生成/透传 `X-Request-Id`（请求头传入则透传，否则自动生成），
  响应回写 `X-Request-Id`，日志以 `[rid:xxx]` 标记
- `LOG_FORMAT=json` 时日志含 `request_id` 字段（ELK/Loki 聚合）
