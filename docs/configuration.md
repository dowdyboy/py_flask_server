# 配置说明

通过环境变量配置（框架启动时自动加载项目根目录 `.env`，无需手动 export）。
也可直接编辑 `flask_server/config.py` 中的默认值。

## 环境预设档（APP_ENV）

| 预设 | debug | host | 日志级别 | 控制台日志 |
|---|---|---|---|---|
| `development`（默认） | true | 127.0.0.1 | DEBUG | 是 |
| `staging` | false | 0.0.0.0 | INFO | 否 |
| `production` | false | 0.0.0.0 | INFO | 否 |

`APP_ENV` 一次性预设 debug/host/log_level 等，以下变量仍可单独覆盖。

## 环境变量表

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `APP_ENV` | `development` | 环境预设档：`development` / `staging` / `production` |
| `SERVER_PORT` | `5000` | 服务端口 |
| `SERVER_HOST` | `127.0.0.1` | 监听地址；对外暴露设为 `0.0.0.0` |
| `DEBUG` | _随 APP_ENV_ | 调试模式（`true`/`1`/`yes` 开启） |
| `THREAD_NUM` | `10` | 线程池大小 |
| `ASYNC_TASK_QUEUE_MAX` | `500` | 异步任务排队上限，超限拒绝新任务并告警 |
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
| `LOG_FILE_PATH` | `server.log` | 日志文件路径（默认项目根 `server.log`）；设为空字符串则禁用文件日志（仅控制台）。测试场景建议设空，避免测试运行污染日志 |
| `DEBUG_SQL` | `false` | 是否打印 SQL 语句（开发调试用） |
| `SQLALCHEMY_URI` | _无_ | SQLAlchemy 数据库 URI |
| `SQLITE_DB_PATH` | _无_ | SQLite 数据库文件路径 |
| `DB_REFLECT_ON_START` | `true` | 启动时是否反射表结构（大数据库设 `false` 改用迁移） |
| `DB_POOL_SIZE` | `10` | 数据库连接池大小 |
| `DB_POOL_RECYCLE` | `3600` | 连接回收时间（秒） |
| `DB_POOL_PRE_PING` | `true` | 连接前 ping 检查 |
| `DB_POOL_TIMEOUT` | `30` | 获取连接超时（秒） |
| `INIT_SQL_PATH` | _无_ | SQL 初始化脚本文件路径（整体以 `executescript` 执行，兼容存储过程/注释内的分号） |
| `REDIS_URL` | _无_ | Redis 连接地址，未配置时使用内存缓存 |
| `RATE_LIMIT_ENABLED` | `false` | 是否启用接口限流（按 IP+路径 固定窗口计数） |
| `RATE_LIMIT_PER_MINUTE` | `60` | 每个 IP+路径 每分钟允许的请求数，超出返回 429 |
| `TRUSTED_PROXIES` | `127.0.0.1,::1` | 可信代理 IP 列表（支持精确 IP 与 CIDR 前缀，如 `172.16.0.0/12` 覆盖 Docker 网关网段）；`get_real_ip` 仅信任来自这些地址的 `X-Forwarded-For`。⚠️ CIDR 内任何主机都可伪造 `X-Forwarded-For`（绕过限流/污染日志），请确保网段内仅含可信服务 |
| `SECURITY_HEADERS_ENABLED` | `true` | 是否注入安全响应头（X-Frame-Options/CSP 等） |
| `AUTH_ENABLED` | `false` | 是否启用全局认证保护（开启后 /api/ 下未豁免路径需 X-AUTH-TOKEN） |
| `AUTH_TOKEN_TTL` | `604800` | access token 有效期（秒，默认 7 天） |
| `AUTH_REFRESH_TOKEN_TTL` | `2592000` | refresh token 有效期（秒，默认 30 天） |
| `AUTH_STORE` | `memory` | 认证用户存储：`memory`（进程内，默认）/ `sqlalchemy`（需配置 DB 并迁移建表） |
| `AUTH_LOGIN_MAX_FAILS` | `5` | 登录防爆破：连续失败次数阈值 |
| `AUTH_LOGIN_LOCK_SECONDS` | `300` | 登录防爆破：达阈值后的锁定秒数 |
| `METRICS_ENABLED` | `true` | 是否启用 Prometheus 指标（`/metrics`） |
| `WORKER_NUM` | `4` | gunicorn worker 数（仅 wsgi_gunicorn.py 生效） |
| `RATE_LIMIT_STORE` | `memory` | 限流计数存储：`memory`（进程内）/ `redis`（多实例准确，需 REDIS_URL） |
| `SNOWFLAKE_WORKER_ID` | _自动_ | 雪花 ID 机器标识（0-31）；多进程部署建议每进程显式配置，未配置时按 PID 自动派生 |

## 数据库

```bash
# MySQL（建议显式指定 charset=utf8mb4 保证中文正确）
export SQLALCHEMY_URI='mysql+pymysql://username:password@host:3306/database_name?charset=utf8mb4'

# 或 SQLite（自动创建数据库文件）
export SQLITE_DB_PATH='storage/app.db'
```

### 反射与迁移共存约束

`DB_REFLECT_ON_START=true` 时启动会反射现有表到 metadata（便于查询已有库表）。
反射表与声明式 Model **不能同名**（会报 Table already defined）。
建议：已有库用反射，新建表用声明式 + Migrate；或统一声明式并设 `DB_REFLECT_ON_START=false`。

## 缓存

- 配置 `REDIS_URL` 后自动使用 Redis 缓存（多进程/多实例场景）
- 未配置时使用内存缓存 `memory_cache`（线程安全，TTL 后台自动清理）
- Redis 不可达时自动降级并冷却重试，不影响业务
- **认证 token / 防爆破计数同此策略**：配置 `REDIS_URL` 后自动落 Redis（多 worker 共享），
  不可达时回退内存缓存（单实例仍可用），恢复后自动回 Redis

## 依赖列表

```
Flask, flask-cors, Flask-SQLAlchemy, flask-migrate, flask-smorest,
marshmallow, requests, Werkzeug, PyMySQL, waitress, MarkupSafe,
redis, python-dotenv, prometheus-client
```

可选：`Flask-SocketIO` + `simple-websocket`（WebSocket）、`eventlet`（eventlet 模式）、
`gunicorn`（Linux 生产多进程）。

> 日志写入 `server.log`（按 `LOG_MAX_BYTES` 轮转）；文件存储于 `storage/` 目录。
