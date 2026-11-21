# 运维手册（Runbook）

按**部署方式**分节：waitress（`wsgi.py`）、gunicorn（`wsgi_gunicorn.py`）、Docker Compose、
ARMv7 离线包。先看"通用"一节，再跳到你的部署方式。

---

## 1. 通用

### 探针

| 端点 | 含义 | 失败时 |
|---|---|---|
| `GET /api/v1/healthz` | 存活（不查依赖） | 进程已死/端口不通 |
| `GET /api/v1/readyz` | 就绪（DB/Redis 连通性） | 返回 503 + `data` 里的 `db`/`redis` 状态 |
| `GET /api/v1/health` | 详细状态（短 TTL 缓存） | 仅用于人工排查 |

`/metrics` 暴露 Prometheus 指标（`METRICS_ENABLED=true` 时）。探针与 `/metrics` 默认豁免限流。

### 日志在哪

| 配置 | 行为 |
|---|---|
| `LOG_FILE_PATH` 未设置/空 | **不写文件**；控制台输出由 `LOG_TO_CONSOLE` 与 `APP_ENV` 预设决定 |
| `LOG_FILE_PATH=/app/logs/server.log` | 写文件并按 `LOG_MAX_BYTES` 轮转（目录自动创建） |
| 文件与控制台都关闭 | `Logger.init` 兜底启用控制台并打一条 WARNING（不会静默丢 INFO） |

容器部署建议**不写文件**（`LOG_FILE_PATH=`），用 `docker logs` / 采集端收 stdout。
`LOG_FORMAT=json` 便于 ELK/Loki 解析，日志里带 `request_id`。

### 优雅关闭

| 入口 | 关闭行为 |
|---|---|
| `wsgi.py`（waitress） | SIGTERM → 停止 accept → 等在途 HTTP 请求（最多 15s）→ 停内嵌 worker → 停 TCP/UDP（等在途 handler，最多 5s）→ dispose DB/关闭 Redis → 退出码 0；serve loop 崩溃则退出码 1 |
| `wsgi_gunicorn.py` | gunicorn `graceful_timeout=15s`；worker 退出前由 gunicorn 回收；`child_exit` 清理多进程指标文件 |
| `scripts/worker.py` | SIGTERM → 不再取新任务 → 等在途任务（最多 15s，`stop_grace_period` 应更大） |

> Docker/K8s 的 `stop_grace_period` / `terminationGracePeriodSeconds` 必须**大于**上面的窗口，
> 否则会先 SIGKILL，在途请求/任务被强杀。

### 凭据轮换

见 [SECURITY.md](../SECURITY.md#轮换凭据)。步骤里区分了通用路径与 ARMv7 专有路径。

---

## 2. waitress（`python wsgi.py`）

```bash
# 启动（前台）
APP_ENV=production SERVER_HOST=0.0.0.0 python wsgi.py

# systemd 示例
sudo systemctl restart pyflask        # 服务名以你的 unit 为准
journalctl -u pyflask -f
```

- 单进程多线程（`THREAD_NUM`）；**不要**在 waitress 后面再套多进程共享同一端口。
- WebSocket 不支持：`SOCKETIO_ENABLED=true` 时启动会告警，请改用 `server.py` 或 gunicorn+eventlet。
- 日志：默认控制台（systemd 捕获）；需要文件时设 `LOG_FILE_PATH`（单进程写文件安全）。

---

## 3. gunicorn（`python wsgi_gunicorn.py`）

```bash
WORKER_NUM=4 PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus-multiproc python wsgi_gunicorn.py
kill -TERM <master_pid>      # 优雅退出
```

- **多 worker 时协议服务器不会启动**（`workers > 1` 直接跳过并告警）；需要 TCP/UDP 请
  `WORKER_NUM=1` 或把协议服务器拆成独立进程。
- 多 worker **必须**配 `PROMETHEUS_MULTIPROC_DIR`，否则 `/metrics` 只看到被刮到的那个 worker。
- 多 worker 写同一个 `LOG_FILE_PATH` 存在轮转竞态：建议 `LOG_FILE_PATH=` 用 stdout 采集。
- 多 worker 的 token/限流/会话共享需要 `REDIS_URL`；`AUTH_STORE=memory` 会导致各 worker 用户表不一致。

---

## 4. Docker Compose

```bash
# 开发
docker compose up -d --build
docker compose logs -f app

# 生产（需要 .env 里的 SECRET_KEY / MYSQL_ROOT_PASSWORD 等）
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml --profile worker up -d --build   # 可选 worker
```

- **日志挂载**：prod compose 把 `./logs` 挂到 `/app/logs`，并设 `LOG_FILE_PATH=/app/logs/server.log`。
  不要挂载单个 `./server.log` 文件——宿主文件不存在时 Docker 会把它建成**目录**，导致应用
  文件日志初始化失败（会降级为控制台）。
- 健康检查用 `/api/v1/readyz`；依赖没起来时容器保持 unhealthy，`depends_on: service_healthy`
  会阻止 app 启动。
- 停止：`docker compose stop` 会先发 SIGTERM，`stop_grace_period` 已设为大于优雅关闭窗口。

---

## 5. ARMv7 离线包（`deployments/armv7`）

```bash
# 板端安装（校验 SHA256SUMS 后解包）
cd deployments/armv7 && ./install.sh --prefix /opt/pyflask

# 服务管理
/etc/init.d/pyflask restart
tail -f /var/log/pyflask.log
```

- 该包使用自己的 `config/env.example`（不完全等同根目录 `.env.example`）。
- 日志在 `/var/log/pyflask.log`（仅此部署方式）；其它部署方式默认不写文件。
- 卸载：`./uninstall.sh --prefix /opt/pyflask`。

---

## 6. 常见故障速查

| 现象 | 先查 |
|---|---|
| `readyz` 503 且 `db=error` | `SQLALCHEMY_URI`、网络、MySQL 健康；`DB_REFLECT_ON_START=false` 的大库更稳 |
| `readyz` 503 且 `redis=error` | `REDIS_URL`、密码、Redis AUTH；框架会降级内存（单实例可用） |
| 登录后另一台机器 401 | 多 worker 未配 `REDIS_URL`（token 只在签发 worker 内有效） |
| 429 且 `Retry-After` 很大 | 触发的是登录锁定（剩余秒数），不是限流；等锁定结束 |
| 任务长时间 `pending` | worker 是否在跑、`TASK_VISIBILITY_TIMEOUT` 是否小于任务耗时、consumer group 名是否一致 |
| 死信堆积 | `python scripts/task_replay.py --list`，确认 handler 幂等后 `--replay` |
| 容器启动即退出 | 看 `docker compose logs app`：`AUTH_STORE=sqlalchemy` 漏配 URI 会在启动期直接失败（fail-fast） |
| 静态页面 404 | `webui/` 是否挂载/存在；`/api/...` 前缀不会走 SPA 回退 |
