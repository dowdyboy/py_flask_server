# API 约定

## 统一响应格式

所有接口返回统一 JSON：

```json
{
  "code": 0,
  "msg": "成功",
  "data": {}
}
```

## 错误码表

### 框架内置

| code | 含义 | HTTP 状态 |
|---|---|---|
| `0` | 成功 | 200 |
| `1001` | 参数错误 | 400 |
| `-1` | 内部错误 | 500 |
| `4001` | 认证错误（用户名已存在 / 用户名或密码错误） | 400 / 401 |
| `4002` | 未登录 / Token / refresh_token 无效或已过期 | 401 |
| `4003` | 登录失败次数过多，已临时锁定 | 429 |
| `5030` | 依赖未就绪（DB/Redis 故障，就绪探针） | 503 |

### HTTP 状态码约定

| 状态码 | 场景 | 响应格式 |
|---|---|---|
| 422 | 参数校验失败（flask-smorest） | 统一格式，字段错误在 `data` |
| 404 | 资源不存在 | 统一格式（code=-1） |
| 429 | 触发限流或登录锁定 | 统一格式（限流 code=-1，锁定 code=4003） |
| 401 | 未登录 / Token 无效或已过期 | 统一格式（code=4001/4002） |

业务自定义错误码：`GraceResult.business_error(2001, '用户不存在')`。

### 422 校验错误结构

字段级错误位于 `data`：

```json
{
  "code": 1001,
  "msg": "参数错误",
  "data": {"json": {"message": ["Missing data for required field."]}}
}
```

## 请求参数解析

框架自动解析多种格式（`before_request` 钩子）：

- **GET 参数** → `request.params`
- **JSON Body** → `request.payload`（Content-Type: application/json）
- **Form Data** → `request.payload`（application/x-www-form-urlencoded）
- **Multipart** → `request.payload`（包含文件，表单字段与文件合并）

## 认证模块

接口：`POST /api/v1/auth/register`、`/login`、`/refresh`、`/logout`、`GET /me`
（见 [快速上手](getting-started.md) 与配置 `AUTH_*` 变量）。

**双令牌机制**：
- 登录返回 `{token, refresh_token}`：`token` 用于请求头 `X-AUTH-TOKEN`（有效期 `AUTH_TOKEN_TTL`），
  `refresh_token` 用于续期
- `POST /api/v1/auth/refresh`（body `{"refresh_token": "..."}`）换取新令牌——**旧 refresh 作废（单次使用）**

**防爆破**：同一用户名连续登录失败 `AUTH_LOGIN_MAX_FAILS`（默认 5）次后锁定
`AUTH_LOGIN_LOCK_SECONDS`（默认 300）秒，锁定期间正确密码也拒绝（code 4003，HTTP 429）。

**令牌与计数存储（自动降级）**：
- 未配置 `REDIS_URL`：access/refresh token 与防爆破计数存进程内内存（单实例可用，重启失效）
- 配置 `REDIS_URL`：自动改用 Redis（多 worker 共享，任意 worker 签发的 token 都能校验）；
  **Redis 不可达时自动回退内存缓存**（单实例下登录/防爆破仍可用，恢复后自动回 Redis）
- `AUTH_STORE=sqlalchemy` 只解决用户数据持久化（UserPO 建表），**token 共享仍依赖 Redis**；
  多进程部署建议配置 `REDIS_URL`（启动 banner 会告警）

**保护单个接口**（推荐）：

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

**全局保护**：`AUTH_ENABLED=true` 时，`/api/` 下除 auth/文档/健康检查外的路径
都需要 `X-AUTH-TOKEN` 请求头（CORS 预检 `OPTIONS` 请求自动放行，不影响跨域前端）。

> 安全提示：响应绝不含密码哈希；生产环境务必修改 SECRET_KEY 并配置 HTTPS。

> ⚠️ `AUTH_STORE=sqlalchemy` 时，数据库故障会导致全部受保护 `/api/` 请求返回 500
> （用户数据源不可用时无法降级为 401）；数据库可用性由 `readyz` 探针监测。

## 分页与 ETag

**分页**（flask-smorest `Page`）：

```python
from flask_smorest import Page
import marshmallow as ma

class UserPage(Page):
    items = ma.fields.List(ma.fields.Raw())

@blp.route('/users/page')
class UserPageView(MethodView):
    @blp.arguments(UserQuerySchema, location='query')
    @blp.response(200, UserPage)
    def get(self, query):
        return UserService.list(page=query['page'], per_page=query['per_page'])
```

返回结构：`{items: [...], page: n, per_page: n, total: n}`（对应 SQLAlchemy `paginate()`）。

**ETag 缓存协商**：

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

`RATE_LIMIT_ENABLED=true` 时启用，固定窗口计数（`RATE_LIMIT_PER_MINUTE`，默认 60 次/分钟），
超限返回 429（code=-1）。

**双层配额**（原子 INCR 计数，防并发竞态绕过）：

- `rate:{ip}:{path}` 路径级配额：同一 IP 对同一路径的请求上限
- `rate:{ip}` IP 级总配额：同一 IP 的**全部**请求上限（兜底，防止攻击者用随机路径
  绕过路径级配额——webui catch-all 使任意唯一路径各有独立计数键）

**豁免端点**（不计数，防编排系统误伤）：`/metrics`、`/api/v1/healthz`、`/api/v1/readyz`、
`/api/v1/health`（探针被 429 会导致实例被摘除 → 流量集中 → 更 429 的雪崩循环）。

**存储与降级**：

- `RATE_LIMIT_STORE=memory`（默认，进程内）/ `redis`（多实例准确，需配置 `REDIS_URL`）
- Redis 主存储**运行中不可达**时自动回退内存计数（单实例下限流仍生效，恢复后自动回 Redis）；
  `RATE_LIMIT_STORE=redis` 但未配置 `REDIS_URL` 时同样回退内存存储

**客户端 IP 解析**：`get_real_ip` 仅在来源位于 `TRUSTED_PROXIES`（支持 CIDR）时信任
`X-Forwarded-For`（取第一个非空条目），否则使用 `remote_addr`。

## Prometheus 指标

`GET /metrics`（`METRICS_ENABLED=true` 默认开）：

- `http_requests_total{method,status,route}` — 请求计数（route 用路由规则聚合，防高基数）
- `http_request_duration_seconds{method,route}` — 请求延迟直方图

## 日志与链路追踪

- `Logger.info/warn/error`（`flask_server.util.Logger`）
- 每次请求自动生成/透传 `X-Request-Id`（请求头传入则透传，否则自动生成），
  响应回写 `X-Request-Id`，日志以 `[rid:xxx]` 标记
- `LOG_FORMAT=json` 时日志含 `request_id` 字段（ELK/Loki 聚合）
