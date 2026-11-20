# 配置说明

通过环境变量配置（框架启动时自动加载项目根目录 `.env`，无需手动 export）。
也可直接编辑 `flask_server/config.py` 中的默认值。

## 环境预设档（APP_ENV）

| 预设 | debug | host | 控制台日志 |
|---|---|---|---|
| `development`（默认） | true | 127.0.0.1 | 是 |
| `staging` | false | 0.0.0.0 | 否 |
| `production` | false | 0.0.0.0 | 否 |

`APP_ENV` 一次性预设 debug / host / 控制台日志，以下变量仍可单独覆盖。

> **日志级别不是预设项**：`log_level = DEBUG if debug else INFO`（只由 `DEBUG` 派生），
> 因此 `APP_ENV=production` + `DEBUG=true` 时日志级别仍是 DEBUG。

## 环境变量表

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `APP_ENV` | `development` | 环境预设档：`development` / `staging` / `production` |
| `SERVER_PORT` | `5000` | 服务端口 |
| `SERVER_HOST` | _随 APP_ENV_ | 监听地址：`development` 默认 `127.0.0.1`，`staging`/`production` 默认 `0.0.0.0`；对外暴露设为 `0.0.0.0` |
| `DEBUG` | _随 APP_ENV_ | 调试模式（`true`/`1`/`yes` 开启）；日志级别也由它派生 |
| `THREAD_NUM` | `10` | 线程池大小（须 > 0，否则告警回退 10） |
| `ASYNC_TASK_QUEUE_MAX` | `500` | 异步任务排队上限，超限拒绝新任务并告警（须 ≥ 0，负数告警回退 500） |
| `SOCKETIO_ENABLED` | `false` | 是否启用 WebSocket（需安装 Flask-SocketIO） |
| `SOCKETIO_ASYNC_MODE` | `threading` | SocketIO 异步模式：`threading`(默认) / `eventlet`；非法值告警回退 `threading` |
| `SOCKETIO_MAX_HTTP_BUFFER_SIZE` | `1000000` | WebSocket 单条消息大小上限（字节，默认 1MB） |
| `TCP_ENABLED` | `false` | 是否启用 TCP 协议服务器（处理器见 `flask_server/handler/`，详见 [protocol-servers.md](protocol-servers.md)） |
| `TCP_HOST` | `0.0.0.0` | TCP 监听地址 |
| `TCP_PORT` | `9000` | TCP 监听端口 |
| `TCP_FRAMING` | `line` | TCP 消息定界：`line`（分隔符切分，默认）/ `fixed`（固定长度）/ `head_tail`（帧头帧尾）/ `raw`（原始 recv 流，自行拆包）；非法值回退 `line` |
| `TCP_FRAME_SEPARATOR` | `\n` | TCP 行帧分隔符（支持转义写法，如 `\r\n`） |
| `TCP_FRAME_LENGTH` | `1024` | TCP 固定长度帧长（`fixed` 模式，需 > 0，否则回退 `line`） |
| `TCP_FRAME_HEAD` | _空_ | TCP 帧头字节串（`head_tail` 模式，支持二进制转义如 `\xaa\x55`；缺任一帧头/帧尾回退 `line`） |
| `TCP_FRAME_TAIL` | _空_ | TCP 帧尾字节串（`head_tail` 模式） |
| `TCP_MAX_MESSAGE_LENGTH` | `65536` | TCP 单条消息上限（字节，超限视为协议错误断开连接，防内存 DoS） |
| `TCP_MAX_CONNECTIONS` | `256` | TCP 并发连接上限（每连接一线程，超限拒绝新连接，防线程耗尽 DoS；`≤0` 表示不限制） |
| `TCP_IDLE_TIMEOUT` | `300` | TCP 空闲连接超时（秒）：连接在此时间内没有任何数据到达即关闭。防"只连不发"占满并发槽位（慢连接耗尽 DoS）；`≤0` 表示不设超时（长连接心跳场景） |
| `UDP_ENABLED` | `false` | 是否启用 UDP 协议服务器（处理器返回 bytes 自动回发） |
| `UDP_HOST` | `0.0.0.0` | UDP 监听地址 |
| `UDP_PORT` | `9001` | UDP 监听端口 |
| `UDP_MAX_MESSAGE_LENGTH` | `65536` | UDP 单数据报大小上限（字节）。超过此大小：**Linux** 上被 recvfrom 缓冲截断后仍交给 handler；**Windows** 上因 `WSAEMSGSIZE` 被丢弃，handler 收不到回调。请按协议最大报文设置 |
| `UDP_MAX_CONCURRENCY` | `256` | UDP 并发处理数据报上限（每数据报一线程，超限丢弃数据报，防洪泛线程爆炸 DoS；`≤0` 表示不限制） |
| `CORS_ORIGINS` | `*` | CORS 允许来源，`*` 或逗号分隔列表 |
| `API_TITLE` | `Flask Server API` | Swagger 文档标题（`/docs`、`/openapi.json`） |
| `API_VERSION` | `v1` | 接口版本（同时用于 `/api/v1/health` 返回的 version 字段） |
| `API_SPEC_URL` | `/openapi.json` | OpenAPI JSON 路径 |
| `API_DOCS_URL` | `/docs` | Swagger UI 路径（CSP 会针对该路径放宽） |
| `SECRET_KEY` | _默认值_ | Flask 密钥，生产环境必须修改 |
| `MAX_CONTENT_LENGTH` | `16777216` | 请求体最大字节数（默认 16MB；须 > 0——0 会让所有带 body 的请求直接 413，非法值告警回退默认） |
| `SWAGGER_UI_URL` | _CDN_ | Swagger UI 资源 URL（CSS/JS 都从这里取）。`/docs` 的 CSP 会**自动放行该 URL 的 scheme://host**，因此内网镜像/私有 CDN 可直接使用 |
| `HSTS_ENABLED` | `false` | 是否注入 `Strict-Transport-Security`（仅在 HTTPS 反代之后有意义；HTTP 下浏览器会忽略该头） |
| `HSTS_MAX_AGE` | `31536000` | HSTS max-age（秒，默认 1 年）；负数告警回退默认 |
| `LOG_FORMAT` | `text` | 日志格式：`text` / `json`（JSON 便于接入 ELK）；非法值告警回退 `text` |
| `LOG_MAX_BYTES` | `10485760` | 日志单文件最大字节数（默认 10MB） |
| `LOG_BACKUP_COUNT` | `5` | 保留的历史日志文件数 |
| `LOG_TO_CONSOLE` | _随 APP_ENV_ | 是否输出日志到控制台（`development` 预设是，`staging`/`production` 否）。文件与控制台**都**关闭时，`Logger.init` 会兜底启用控制台并打一条 WARNING |
| `LOG_FILE_PATH` | _未设置_ | 日志文件路径。**未设置（`.env.example` 中默认注释）或设为空字符串 = 不写文件日志**；是否输出控制台由 `LOG_TO_CONSOLE` 单独决定，两者互不替代。设为非空路径则写文件（如项目根 `server.log`，按 `LOG_MAX_BYTES` 轮转），目录不存在时自动创建，创建失败（权限不足等）降级为控制台日志并告警，不影响启动。**若文件日志与控制台日志都关闭，`Logger.init` 会兜底启用控制台并告警，INFO 不会被静默丢弃**。测试场景建议保持未设置/置空，避免测试运行污染日志 |
| `DEBUG_SQL` | `false` | 是否打印 SQL 语句（开发调试用） |
| `SQLALCHEMY_URI` | _无_ | SQLAlchemy 数据库 URI（ORM 与 Flask-Migrate 的唯一入口；SQLite 相对路径基于 `instance/`，如 `sqlite:///storage/app.db`） |
| `SQLITE_DB_PATH` | _无_ | SQLite 数据库文件路径（**仅** `flask_server.module.sqlite` 的裸 sqlite3 封装使用；不参与 ORM/迁移，ORM 请配 `SQLALCHEMY_URI`） |
| `DB_REFLECT_ON_START` | `true` | 启动时是否反射表结构（大数据库设 `false` 改用迁移） |
| `DB_POOL_SIZE` | `10` | 数据库连接池大小（须 > 0，否则告警回退 10；仅对 MySQL 等 QueuePool 后端生效） |
| `DB_POOL_RECYCLE` | `3600` | 连接回收时间（秒） |
| `DB_POOL_PRE_PING` | `true` | 连接前 ping 检查 |
| `DB_POOL_TIMEOUT` | `30` | 获取连接超时（秒；须 > 0，否则告警回退 30；仅对 QueuePool 后端生效） |
| `INIT_SQL_PATH` | _无_ | SQL 初始化脚本文件路径（整体以 `executescript` 执行，兼容存储过程/注释内的分号） |
| `REDIS_URL` | _无_ | Redis 连接地址，未配置时使用内存缓存 |
| `RATE_LIMIT_ENABLED` | `false` | 是否启用接口限流（按 IP+路径 顺延窗口计数） |
| `RATE_LIMIT_PER_MINUTE` | `60` | 每个 IP+路径 每分钟允许的请求数，超出返回 429。探针/监控端点（/metrics、healthz、readyz、health，含尾斜杠写法）豁免限流；超长路径的计数键自动哈希截断 |
| `TRUSTED_PROXIES` | `127.0.0.1,::1` | 可信代理 IP 列表（支持精确 IP 与 CIDR 前缀，如 `172.16.0.0/12` 覆盖 Docker 网关网段）；`get_real_ip` 仅信任来自这些地址的 `X-Forwarded-For`。⚠️ CIDR 内任何主机都可伪造 `X-Forwarded-For`（绕过限流/污染日志），请确保网段内仅含可信服务 |
| `SECURITY_HEADERS_ENABLED` | `true` | 是否注入安全响应头（X-Frame-Options/CSP 等） |
| `AUTH_ENABLED` | `false` | 是否启用全局认证保护（开启后 /api/ 下未豁免路径需 X-AUTH-TOKEN） |
| `AUTH_TOKEN_TTL` | `604800` | access token 有效期（秒，默认 7 天） |
| `AUTH_REFRESH_TOKEN_TTL` | `2592000` | refresh token 有效期（秒，默认 30 天） |
| `AUTH_STORE` | `memory` | 认证用户存储：`memory`（进程内，默认）/ `sqlalchemy`（需配置 DB 并迁移建表）；拼错告警回退 `memory`（不再静默退化） |
| `AUTH_LOGIN_MAX_FAILS` | `5` | 登录防爆破：同一 (用户名, 来源 IP) 连续失败次数阈值 |
| `AUTH_LOGIN_LOCK_SECONDS` | `300` | 登录防爆破：达阈值后的锁定秒数（锁定键按 (用户名, IP) 隔离，避免他人故意打错密码锁死账号） |
| `AUTH_PBKDF2_ITERATIONS` | `600000` | 密码哈希 PBKDF2-HMAC-SHA256 迭代次数（新哈希写入用；校验以哈希串内记录的迭代次数为准，可平滑升级）。`<10000` 告警回退默认；**`>1_000_000` 同样告警回退**（`config.PBKDF2_MAX_ITERATIONS` 与校验侧 `DataEncryptUtil.verify_pbkdf2` 共用同一上限——超过它的新哈希永远无法通过校验，会导致所有登录 401） |
| `METRICS_ENABLED` | `true` | 是否启用 Prometheus 指标（`/metrics`） |
| `PROMETHEUS_MULTIPROC_DIR` | _无_ | 多进程指标目录。gunicorn 多 worker 时**必须设置**（指向一个目录），否则 `/metrics` 只返回被刮到的那个 worker 的数据；目录不存在会自动创建 |
| `WORKER_NUM` | `4` | gunicorn worker 数（仅 `wsgi_gunicorn.py` 生效）。`wsgi.py` / `server.py` 会显式给启动 banner 传 `worker_num=1`，单进程运行不再读此值而误报多 worker 告警 |
| `RATE_LIMIT_STORE` | `memory` | 限流计数存储：`memory`（进程内）/ `redis`（多实例准确，需 REDIS_URL）；拼错告警回退 `memory` |
| `SNOWFLAKE_WORKER_ID` | _自动_ | 雪花 ID 机器标识（0-31，仅 **32** 个槽位）。未配置时按 `(crc32(hostname) + pid) % 32` 派生——**只是尽力分散，不是唯一性保证**：按生日问题估算，至少两个副本撞到同一槽位的概率为 2 副本 **≈3%**、4 副本 **≈18%**、8 副本 **≈61%**；一旦撞号，两个副本在同一毫秒生成的 64 位 ID 会完全相同。因此**≥2 副本应显式配置**（每个副本不同值），**≥4 副本实际上必须配置**（如 K8s StatefulSet 按序号注入 / 每副本独立环境变量）；对唯一性零容忍或不想管理机器号时，直接用 `KeyGenerator.generate_uuid7()`（时间有序 UUID，零配置） |
| `SNOWFLAKE_DATACENTER_ID` | `1` | 雪花 ID 数据中心标识（0-31）。多机房/多集群各配不同值，越界回退 1 并告警 |
| `TASK_QUEUE_STREAM` | `tasks:default` | 任务队列名（Redis Streams）。详见 [任务队列](task-queue.md) |
| `TASK_QUEUE_GROUP` | `default` | 任务队列 consumer group 名 |
| `TASK_WORKER_CONCURRENCY` | `4` | 任务消费线程数（须 > 0，否则回退默认） |
| `TASK_MAX_RETRIES` | `3` | 任务最大重试次数（`0` = 失败直接进死信） |
| `TASK_RETRY_BACKOFF` | `5` | 重试退避基数（秒）：第 n 次重试延迟 = `backoff * 2**(n-1)` |
| `TASK_VISIBILITY_TIMEOUT` | `300` | 可见性超时（秒）：worker 崩溃后超过该时长的 pending 消息会被重新投递 |
| `TASK_STATUS_TTL` | `86400` | 任务状态保留时长（秒，Redis 后端为 key TTL） |
| `TASK_DEAD_LETTER_MAX` | `1000` | 死信队列上限（超出丢弃最旧的） |
| `TASK_STATUS_MAX` | `10000` | 内存后端状态表上限（防内存无界增长） |
| `TASK_RESERVE_TIMEOUT` | `2` | 单次出队阻塞时长（秒）：越小空转越频繁，越大停止响应越慢 |
| `TASK_WORKER_EMBEDDED` | `false` | 是否在 Web 进程内嵌任务消费者（开发便利；生产用 `scripts/worker.py`） |

## 数据库

```bash
# MySQL（建议显式指定 charset=utf8mb4 保证中文正确）
export SQLALCHEMY_URI='mysql+pymysql://username:password@host:3306/database_name?charset=utf8mb4'

# 或 SQLite（ORM + 迁移；Flask-SQLAlchemy 3 的相对路径基于 instance/，
# 实际文件为 instance/storage/app.db；固定位置请用绝对路径 sqlite:////data/app.db）
export SQLALCHEMY_URI='sqlite:///storage/app.db'

# 注意：SQLITE_DB_PATH 只用于轻量封装 flask_server.module.sqlite（裸 sqlite3），
# 不参与 SQLAlchemy ORM / Flask-Migrate
# export SQLITE_DB_PATH='storage/app.db'
```

### 反射与迁移共存约束

`DB_REFLECT_ON_START=true`（默认）时启动会将库中**未被声明式模型占用**的表反射到 metadata
（便于查询已有库表）；声明式模型（如认证的 `user` 表）先导入占据表名，同名表不会被重复
反射——"迁移建表后重启"不会因 `Table already defined` 崩溃。

建议：已有库用反射；新建表用声明式 + Migrate。超大库（数千表）反射会拖慢启动，
建议设 `DB_REFLECT_ON_START=false`。

## 缓存

- 配置 `REDIS_URL` 后自动使用 Redis 缓存（多进程/多实例场景）
- 未配置时使用内存缓存 `memory_cache`（线程安全，TTL 后台自动清理）。内存缓存**有容量上限**
  （默认 `10_000` 条，可用 `SimpleMemoryCache(max_entries=...)` 为单实例覆盖）：写入超过上限时
  按写入顺序淘汰最久未写入的键（刚写入的键不会被淘汰），并按"每累计 1000 次淘汰"限频打一条
  `WARN`，便于发现缓存长期欠配。TTL 键仍由后台线程定期清理；容量淘汰与 TTL 过期互不替代
- Redis 不可达时自动降级并冷却重试，不影响业务
- **认证 token / 防爆破计数同此策略**：配置 `REDIS_URL` 后自动落 Redis（多 worker 共享），
  不可达时回退内存缓存（单实例仍可用），恢复后自动回 Redis

## 依赖列表

```
Flask, flask-cors, Flask-SQLAlchemy, flask-migrate, flask-smorest,
marshmallow, Werkzeug, PyMySQL, waitress, MarkupSafe,
redis, python-dotenv, prometheus-client
```

可选：`Flask-SocketIO` + `simple-websocket`（WebSocket）、`eventlet`（eventlet 模式）、
`gunicorn`（Linux 生产多进程）。

> `requests` 仅 `scripts/benchmark.py` 使用，已移至 `requirements-dev.txt`，
> 不进入生产镜像。

> 配置 `LOG_FILE_PATH`（非空）时日志写入对应文件（按 `LOG_MAX_BYTES` 轮转）；
> 未配置时不写文件，控制台输出由 `LOG_TO_CONSOLE` 决定——两者都关闭时 `Logger.init`
> 会兜底启用控制台并告警（容器场景可直接用 Docker logs 采集）。
> **`storage/` 是上传文件根目录（`config.file_saved_path`），不是日志目录。**
