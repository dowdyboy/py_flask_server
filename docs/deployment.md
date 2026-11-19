# 部署指南

## 生产入口选型

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

### 多进程部署注意事项（gunicorn）

1. **雪花 ID**：worker_id 默认按 `(crc32(hostname) + pid) % 32` 派生（容器 hostname 可区分副本，
   但仍非严格保证）。多副本部署请**显式配置** `SNOWFLAKE_WORKER_ID` / `SNOWFLAKE_DATACENTER_ID`；
   对唯一性零容忍的场景直接用 `KeyGenerator.generate_uuid7()`（时间有序 UUID，无需分配机器号）
2. **缓存/限流**：memory_cache 与限流计数为进程内，多 worker 下互相独立——
   必须配置 `REDIS_URL` 保持一致（启动 banner 会告警）
3. **认证**：access/refresh token 与登录防爆破计数默认存进程内缓存；配置 `REDIS_URL`
   后自动改用 Redis（多 worker 共享，任意 worker 签发的 token 都能校验）。
   `AUTH_STORE=sqlalchemy` 只解决用户数据持久化，**token 共享仍依赖 Redis**
4. **登录锁定**：防爆破计数按 (用户名, IP) 隔离并缓存在上述存储，多 worker 下同样需 Redis 才能全局生效
5. **定时任务**：若使用 APScheduler，多 worker 下任务会重复执行——建议单 worker 运行
   或加分布式锁（参考 `examples/scheduler_demo.py`）
6. **日志**：`LOG_FILE_PATH` 未设置/为空时**不写任何日志文件**（不存在"默认写 `server.log`"）。
   控制台输出由独立的 `LOG_TO_CONSOLE` 决定（`staging`/`production` 预设为 false）；
   若文件与控制台都关闭，`Logger.init` 会兜底启用控制台并打一条 WARNING，避免 INFO 被静默丢弃。
   显式设置 `LOG_FILE_PATH` 时每个 worker 都会写同一文件，多进程并发轮转存在竞态（可能丢行）。
   生产建议：保持 `LOG_FILE_PATH` 为空、交给容器日志采集（Docker logs 同时收 stdout/stderr）；
   或让每个 worker 写不同文件后由外部工具统一轮转，而不是多个进程轮转同一文件
7. **Prometheus 指标**：多 worker 下必须设置 `PROMETHEUS_MULTIPROC_DIR`（指向一个目录，
   不存在会自动创建），框架会自动用多进程聚合模式返回全局指标，并在 worker 退出时
   清理其指标文件；未设置时 `/metrics` 只含被刮到的那个 worker 的计数
8. **TCP/UDP 协议服务器**：为每进程独立实例，多 worker 会重复绑定同一端口。
   **框架已在代码层拦截**：`WORKER_NUM>1` 时 `post_worker_init` 跳过启动（只打一条告警），
   不会出现"端口冲突 → worker 崩溃 → master 重拉"的崩溃循环。需要协议服务器时请
   `WORKER_NUM=1` 或将协议服务器独立进程部署；同时在防火墙放行 TCP/UDP 端口
   （默认 9000 / 9001）。详见 [protocol-servers.md](protocol-servers.md)
9. **后台任务**：任务消费者建议**独立进程/独立容器**部署（`python scripts/worker.py`），
   与 Web 进程分开扩容重启。必须配置 `REDIS_URL`（内存队列只在入队的进程内可见）；
   `TASK_WORKER_EMBEDDED=true` 只适合单进程开发。详见 [task-queue.md](task-queue.md)
   > `docker-compose.prod.yml` 已内置 `worker` 服务（默认不启动，避免没定义任务时反复重启）：
   > `docker compose -f docker-compose.prod.yml --profile worker up -d --build`

### 优雅退出

| 入口 | 行为 |
|---|---|
| `python wsgi.py` | 收到 SIGTERM/SIGINT 后：**停止 accept 新连接** → 等待在途请求处理完（最长 `GRACEFUL_TIMEOUT`=15s）→ 依次停止内嵌任务 worker、协议服务器、DB 连接池/Redis/线程池 → 退出。空闲 keep-alive 连接由看门狗超时兜底；**二次信号立即退出** |
| `python wsgi_gunicorn.py` | 由 gunicorn 自身管理 `graceful_timeout`（15s）与 worker 优雅退出（任务消费者是独立进程，不在此列） |
| `python scripts/worker.py` | 收到 SIGTERM/SIGINT 后**不再取新任务**，等在途任务执行完再退出；容器请把 `stop_grace_period` 设得比最长任务长 |

## Docker 部署

### 一键启动（app + MySQL + Redis）

```bash
cp .env.example .env
docker-compose up -d
docker-compose ps
```

服务：app 在 `http://localhost:5000`，MySQL `3306`，Redis `6379`（均带 healthcheck，
app 依赖 MySQL/Redis 就绪后启动）。

> compose 中的 app 服务（以及 prod 的 worker 服务）均启用 `init: true`
> （tini 作为 PID 1 回收僵尸进程）。开发编排的 `stop_grace_period` 为 10s
> （入口是 `server.py` 开发服务器）；生产入口
> `wsgi.py` 的 `GRACEFUL_TIMEOUT` 是 15s，prod 编排已把 app 的 `stop_grace_period`
> 设为 **25s**（必须大于 15s，否则 Docker 会在 drain 完成前 SIGKILL；worker 为 60s）。

### 容器化生产部署

```bash
docker-compose -f docker-compose.prod.yml up -d --build
```

prod 编排与开发版的差异：
- app 使用 waitress 入口（`python wsgi.py`）
- MySQL/Redis 不暴露宿主端口（仅内网互通）
- `SECRET_KEY` / `MYSQL_ROOT_PASSWORD` 必填校验（缺失直接拒绝启动）
- `LOG_FILE_PATH=/app/server.log`，并把 `./server.log` 挂载到宿主便于日志采集
  （若容器未设置 `LOG_FILE_PATH`，该挂载不会有内容）
- app 与 worker 启用 `init: true`；app 的 `stop_grace_period: 25s` >
  `wsgi.py` 的 `GRACEFUL_TIMEOUT=15s`，worker 为 60s

> ⚠️ 镜像内以非 root 用户（`appuser`，uid 1000）运行，compose 将宿主目录
> `./storage`（文件上传落盘）与 `./server.log` 以 bind mount 挂载进容器。
> `./server.log` 只有在容器设置了 `LOG_FILE_PATH`（prod compose 已设为 `/app/server.log`）时
> 才会有日志写入；未设置时该挂载只是空文件。**Linux 宿主上若这些目录/文件属 root 且权限不足
> （如 755），appuser 无法写入**，文件上传/日志写入会报错。请确保宿主目录可写：
> `chmod -R a+w storage`（或改属主 `chown 1000:1000`），或改用 named volume。

### 单独构建镜像

```bash
docker build -t flask-server .
docker run -p 5000:5000 \
  -e APP_ENV=production \
  -e SERVER_HOST=0.0.0.0 \
  -e SECRET_KEY='请替换为强随机值' \
  -e SQLALCHEMY_URI='mysql+pymysql://user:pass@host:3306/db?charset=utf8mb4' \
  flask-server
```

> ⚠️ 不要直接照搬 `--env-file .env`：模板 `.env.example` 里是 `SERVER_HOST=127.0.0.1`，
> 容器只会监听回环地址，宿主 `-p 5000:5000` 映射不可达；而且 `APP_ENV=development` +
> `DEBUG=true` 会覆盖镜像内置的 `APP_ENV=production`。
> 若坚持用 `.env`，先改成 `SERVER_HOST=0.0.0.0`、`APP_ENV=production`、`DEBUG=false`，
> 并确认 `SECRET_KEY` / `SQLALCHEMY_URI` 不是模板默认值。需要文件日志时再加
> `-e LOG_FILE_PATH=/app/server.log`。

镜像采用多阶段构建（builder 安装依赖 → 最终层仅运行时文件），体积更小。
镜像默认 `CMD ["python", "wsgi.py"]`（waitress）；WebSocket 场景改用 `server.py`，
Linux 多进程用 `wsgi_gunicorn.py`。

## 健康检查与探针

| 端点 | 语义 | 行为 |
|---|---|---|
| `GET /api/v1/healthz` | **存活探针（liveness）** | 仅表示进程存活，不检查依赖，恒返回 200 |
| `GET /api/v1/readyz` | **就绪探针（readiness）** | 检查 DB/Redis 连通性，任一依赖故障返回 **503** + 统一格式 |
| `GET /api/v1/health` | 详情诊断（人工排查用） | 返回 status/version/uptime + 各依赖状态（不改变 HTTP 状态码） |

**推荐用法**：
- 容器 healthcheck / K8s `livenessProbe` 用 `/api/v1/healthz`
- K8s `readinessProbe` / 负载均衡健康检查用 `/api/v1/readyz`（依赖故障时实例被摘除流量）
- 模板的 docker-compose healthcheck 已默认使用 `/api/v1/readyz`

## 安全建议

1. **生产配置**：`APP_ENV=production`、`DEBUG=false`、`SERVER_HOST` 置于反向代理之后、
   收紧 `CORS_ORIGINS` 为具体域名、设置强 `SECRET_KEY`、配置 HTTPS。
   启动时 banner 会自动检查上述项并列出警告。
2. **敏感信息**：密钥与数据库连接串通过环境变量/`.env` 存储，勿提交到版本控制
   （`.gitignore` 已排除 `.env`、`*.log`、`storage/*.db`）。
3. **数据库**：使用连接池、限制数据库用户权限。
4. **安全响应头**：默认注入 X-Content-Type-Options / X-Frame-Options / Referrer-Policy / CSP
   （`SECURITY_HEADERS_ENABLED=false` 可关闭）。CSP 对 `/docs` 会自动放行 `SWAGGER_UI_URL`
   的 `scheme://host`（CSS/JS 都从该地址取），其余路径收紧为 `script-src 'self'`。
   置于 HTTPS 反代之后建议同时开启 `HSTS_ENABLED=true`。
5. **限流**：`RATE_LIMIT_ENABLED=true` 按 IP+路径 顺延窗口限流（另有 IP 级总配额兜底，
   防随机路径绕过），超限返回 429；`/metrics` 与健康探针端点豁免限流。
6. **可信代理**：`TRUSTED_PROXIES` 内的来源会被信任其 `X-Forwarded-For` 头，解析规则是从右
   向左取第一个非可信 IP。⚠️ 使用 CIDR（如 Docker 的 `172.16.0.0/12`）时，该网段内**任何**
   主机（含同网络下其他容器）都能伪造 `X-Forwarded-For`（绕过限流、污染日志 IP）。请确保
   网段内仅含可信服务；如需严格隔离，应改为信任具体网关 IP。
   > **waitress 特有坑（框架已处理）**：waitress 3 的 `clear_untrusted_proxy_headers`
   > 默认为 True，未配置 `trusted_proxy` 时会**直接删除** `X-Forwarded-*`。若被删除，
   > 代理后所有请求的 `remote_addr` 都是代理 IP——限流会退化成一个全站共享的桶、
   > 日志丢失真实来源。`wsgi.py` 已显式关闭该清理，信任决策统一交给
   > `TRUSTED_PROXIES` + `get_real_ip()`。
7. **认证与监控**：`AUTH_ENABLED=true` 时全局认证仅拦截 `/api/` 前缀路径——
   `/metrics` 不在 `/api/` 下，**不需要 `X-AUTH-TOKEN`**（Prometheus 可直接抓取）。
   如需保护指标端点，请将服务仅绑定内网（防火墙/编排网络策略），或在
   `flask_server/component/interceptor.py` 自行添加鉴权。

## Swagger UI 内网/离线部署

默认 `/docs` 从 jsdelivr CDN 加载资源，无外网时页面空白。
将 swagger-ui-dist 静态资源托管到内网（如 Nginx），并设置环境变量指向：

```bash
SWAGGER_UI_URL=http://internal-docs.example.com/swagger-ui-dist/
```

> `/docs` 的 CSP 会读取 `SWAGGER_UI_URL` 并自动放行其来源（`scheme://host`），
> 无需手工改代码；自定义地址同样生效（内网 IP/端口、私有 CDN 均可）。

Nginx 托管示例：

```nginx
location /swagger-ui-dist/ { alias /srv/www/swagger-ui-dist/; }
```
