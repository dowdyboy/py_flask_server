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

1. **雪花 ID**：多 worker 下默认按 PID 自动派生 worker_id（防重复）；更推荐显式配置，
   如 gunicorn 配置中为每个 worker 注入不同的 `SNOWFLAKE_WORKER_ID`
2. **缓存/限流**：memory_cache 与限流计数为进程内，多 worker 下互相独立——
   必须配置 `REDIS_URL` 保持一致（启动 banner 会告警）
3. **认证**：access/refresh token 与登录防爆破计数默认存进程内缓存；配置 `REDIS_URL`
   后自动改用 Redis（多 worker 共享，任意 worker 签发的 token 都能校验）。
   `AUTH_STORE=sqlalchemy` 只解决用户数据持久化，**token 共享仍依赖 Redis**
4. **登录锁定**：防爆破计数存缓存，多 worker 下同样需 Redis 才能全局生效
5. **定时任务**：若使用 APScheduler，多 worker 下任务会重复执行——建议单 worker 运行
   或加分布式锁（参考 `examples/scheduler_demo.py`）
6. **日志**：每个 worker 都向 `LOG_FILE_PATH`（默认 `server.log`）写入，
   多进程并发轮转同一文件存在竞态（可能丢行）。生产建议：将日志重定向到 stdout
   （如 `LOG_FILE_PATH=` 禁用文件日志 + 容器日志采集器），或将 `LOG_FILE_PATH`
   指向共享挂载卷后由外部工具统一轮转
7. **Prometheus 指标**：未启用 multiprocess 模式时，各 worker 的 `/metrics` 只含本进程的
   计数（轮询任一 worker 都会漏掉其他 worker 的数据）。需要全局准确指标时：
   配置 `prometheus_multiproc_dir` 共享目录 + `PROMETHEUS_MULTIPROC_DIR` 环境变量，
   并部署 prometheus-client 多进程模式（或用独立采集端聚合）
8. **TCP/UDP 协议服务器**：为每进程独立实例，多 worker 会重复绑定同一端口导致冲突。
   启用时需 `WORKER_NUM=1`，或将协议服务器独立进程部署（启动 banner 会告警）；
   同时需在防火墙放行 TCP/UDP 监听端口（默认 9000 / 9001）。详见
   [protocol-servers.md](protocol-servers.md)

waitress（wsgi.py）与 gunicorn（wsgi_gunicorn.py）均支持优雅退出：
waitress 由 `wsgi.py` 注册的 SIGTERM/SIGINT 处理器释放 DB 连接池、Redis 连接、线程池；
gunicorn 由自身管理信号与 worker 优雅退出。

## Docker 部署

### 一键启动（app + MySQL + Redis）

```bash
cp .env.example .env
docker-compose up -d
docker-compose ps
```

服务：app 在 `http://localhost:5000`，MySQL `3306`，Redis `6379`（均带 healthcheck，
app 依赖 MySQL/Redis 就绪后启动）。

### 容器化生产部署

```bash
docker-compose -f docker-compose.prod.yml up -d --build
```

prod 编排与开发版的差异：
- app 使用 waitress 入口（`python wsgi.py`）
- MySQL/Redis 不暴露宿主端口（仅内网互通）
- `SECRET_KEY` / `MYSQL_ROOT_PASSWORD` 必填校验（缺失直接拒绝启动）
- `server.log` 挂载到宿主便于日志采集

> ⚠️ 镜像内以非 root 用户（`appuser`，uid 1000）运行，compose 将宿主目录
> `./storage`（文件上传落盘）与 `./server.log` 以 bind mount 挂载进容器。
> **Linux 宿主上若这些目录/文件属 root 且权限不足（如 755），appuser 无法写入**，
> 文件上传/日志写入会报错。请确保宿主目录可写：
> `chmod -R a+w storage`（或改属主 `chown 1000:1000`），或改用 named volume。

### 单独构建镜像

```bash
docker build -t flask-server .
docker run -p 5000:5000 --env-file .env flask-server
```

镜像采用多阶段构建（builder 安装依赖 → 最终层仅运行时文件），体积更小。
生产环境可改用 `CMD ["python", "wsgi.py"]`。

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
   （`SECURITY_HEADERS_ENABLED=false` 可关闭）。
5. **限流**：`RATE_LIMIT_ENABLED=true` 按 IP+路径 固定窗口限流（另有 IP 级总配额兜底，
   防随机路径绕过），超限返回 429；`/metrics` 与健康探针端点豁免限流。
6. **可信代理**：`TRUSTED_PROXIES` 内的来源会被信任其 `X-Forwarded-For` 头。
   ⚠️ 使用 CIDR（如 Docker 的 `172.16.0.0/12`）时，该网段内**任何**主机（含同网络下
   其他容器）都能伪造 `X-Forwarded-For`（绕过限流、污染日志 IP）。请确保网段内仅含
   可信服务；如需严格隔离，应改为信任具体网关 IP。
7. **认证与监控**：`AUTH_ENABLED=true` 时 `/metrics` 等未豁免端点需要 `X-AUTH-TOKEN`
   请求头，Prometheus 抓取需配置该头（或按需加入豁免）。

## Swagger UI 内网/离线部署

默认 `/docs` 从 jsdelivr CDN 加载资源，无外网时页面空白。
将 swagger-ui-dist 静态资源托管到内网（如 Nginx），并设置环境变量指向：

```bash
SWAGGER_UI_URL=http://internal-docs.example.com/swagger-ui-dist/
```

Nginx 托管示例：

```nginx
location /swagger-ui-dist/ { alias /srv/www/swagger-ui-dist/; }
```
