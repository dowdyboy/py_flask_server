# 端到端测试报告

- 日期：2026-10-29
- 项目：`py_flask_server`（v0.5.0 + 未发布的任务队列 / 配置注入）
- 环境：Windows 11，Python 3.13.11（`D:\ProgramFiles\miniconda3\envs\t2xpu`），
  真实 Redis 7.4.10 / MySQL 8.4.10（项目 `.env` 指向的远端实例，测试只做只读探测）
- 结论：**未发现 P0/P1 级故障**；核心 HTTP、认证、限流、协议服务器、任务队列主流程均可用。
  修复前发现 3 个核心功能/可靠性问题（P2：Redis stats、死信参数丢失、崩溃恢复饥饿）、
  2 个配置注入边界（P2/P3：SocketIO 开关、AUTH_STORE 单例）和若干文档/配置/脚手架细节问题（P3）；
  **均已在第 8 节修复并复测通过**（582 个 pytest 用例 / 覆盖率 91.27% / E2E 460 项检查全通过）。

---

## 1. 结果总览

| 测试集 | 检查数 | 通过 | 失败 | 说明 |
|---|---:|---:|---:|---|
| 仓库自带 pytest（基线，修复后） | 582 | 582 | 0 | 覆盖率 **91.27%**（门槛 85%），ruff 通过 |
| HTTP / 认证 / 限流 / DB（真实 waitress 进程） | 206 | 206 | 0 | 含真实 Redis、SQLite、远端 MySQL 只读探测 |
| TCP / UDP 协议服务器 | 70 | 70 | 0 | line/fixed/head_tail/raw、粘包/拆包、并发/空闲/超限 |
| 任务队列（修复后） | 110 | 110 | 0 | 含新增 stats / 死信参数 / reclaim 回归检查 |
| 脚手架 / 部署 / CI / 文档（修复后） | 74 | 74 | 0 | 含 README/文档/脚手架排除项检查 |
| **合计** | **460** | **460** | **0** | 另有配置注入/重放/恢复等专项探针（见第 8 节） |

补充验证：

- 使用**项目真实 `.env`**（远端 MySQL + Redis、`AUTH_ENABLED=true`、`RATE_LIMIT_STORE=redis`）
  启动 `wsgi.py`：`/api/v1/health`、`/api/v1/readyz` 均 `db=ok, redis=ok`；未登录 `/api/v1/echo` 返回 401；
  `/docs`、`/metrics` 正常；真实 MySQL `user` 表 19 行，`UserPO` / `AuthService.get_user_by_uid` 查询正常。
- `scripts/scaffold.py` 生成的新项目**独立跑通 582 个测试**。
- `docker compose config`：prod 默认不含 worker（profile 默认不启动），
  `--profile worker` 时包含 worker（profile=`worker`、`stop_grace_period=60s`、命令为 `scripts/worker.py`）。
- CI 实际 9 个 job，与 README “九流水线”一致。
- `scripts/benchmark.py` 40 请求、4 并发：退出码 0、错误 0。

---

## 2. 测试方法

1. 先跑仓库自带测试与 lint，确认基线：
   ```bash
   python -m pytest tests/ -q --cov=flask_server --cov-report=term-missing
   python -m ruff check flask_server/ tests/ scripts/ examples/
   ```
2. 用 `create_app` / 真实 `wsgi.py` 进程 / 真实 TCP·UDP loopback / 真实 Redis / SQLite / 远端 MySQL
   做进程级端到端测试（脚本在 `_e2e/`，不修改业务代码）。
3. 对近期新增功能做专项探针：任务队列（内存/Redis/CLI/内嵌 worker/重试/死信/接管）、
   配置注入（多 app 隔离、SocketIO、AUTH_STORE）、`head_tail` 定界、UDP 超长数据报、
   脚手架、compose profile、CI/文档一致性。

> 测试脚本、结果日志见 `_e2e/`；基线 JUnit 结果在本地 `test-results.xml`（生成物，未提交）。
> 远端 Redis 使用 DB 15 + 唯一 key 前缀并在结束后清理；远端 MySQL 只做 `SELECT`/`COUNT`，未写入。

---

## 3. 已验证正常的功能

### HTTP / 框架
- `/hello`、`/api/v1/healthz`、`/api/v1/readyz`、`/api/v1/health`、`/docs`、`/openapi.json`、`/metrics`。
- 统一响应 `{code,msg,data}`；404/405/422/400/413/500 的状态码与错误结构；
  生产环境 500 脱敏、开发环境回显详情。
- `X-Request-Id` 透传/自动生成/64 字符截断/控制字符清洗；安全响应头（CSP/nosniff/DENY/no-referrer/HSTS 可配）。
- `LOG_FORMAT=json` 与 `LOG_FILE_PATH` 文件日志均带 `request_id`（404 请求验证）。
- `/docs` CSP 按 `SWAGGER_UI_URL` 动态放行外部 CSS/JS；自定义内网 CDN 也能放行。
- CORS 简单请求与预检（认证开启时 OPTIONS 不 401）；`/metrics/`、`/docs/` 308 规范化。
- WebUI：`/`、SPA 回退、缺失静态资源 404、`/api` 与 `/API/...` 不回落 SPA、路径穿越 404。
- 指标：`http_requests_total` / `http_request_duration_seconds`，路由标签聚合。

### 认证 / 限流
- 注册、重复注册、非法用户名/短密码 422、登录、`/me`、登出、refresh 单次轮换、登出吊销 refresh。
- 全局认证拦截：未登录 401、带 token 放行；`/api/v1/auth/*`、健康检查、文档豁免。
- 登录锁定按 `(用户名, IP)` 隔离；达到阈值后正确密码也 429（code 4003）；另一 IP 不受影响；锁定到期自动解锁。
- 限流：路径级、IP 级、探针/监控豁免、OPTIONS 豁免、`X-Forwarded-For` 取最右非可信 IP（左侧伪造不绕过）。
- 限流 Redis 存储与 Redis 不可达时回退内存；`readyz` 依赖故障 503（code 5030）。
- 两个进程共享 Redis token + 共享 SQLite 用户表：A 签发 token 在 B 可用，B 登出后 A 上立即失效。
- `AUTH_STORE=sqlalchemy` + SQLite：注册/登录/`/me`，重启进程后仍可登录；库中密码为 PBKDF2 哈希。
- 远端 MySQL：health/readyz `db=ok`，ORM 映射/查询正常。

### 任务队列
- 内存后端：queued→running→succeeded、延迟、retry（attempts/last_error）、dead、状态 LRU 上限、死信上限、close。
- Worker：`process_one`、app context 内执行、失败重试直到成功、超限进死信、未注册任务进死信、
  4 线程并发消费、`stop()` 等待在途任务完成。
- Redis 后端：enqueue/reserve/ack、延迟任务、retry、dead、状态 TTL 到期 `TaskNotFound`、
  `XAUTOCLAIM` 接管陈旧 pending 并重新投递、Redis 不可用初始化抛 `TaskQueueUnavailable`。
- `scripts/worker.py --once`：成功/重试/死信/未注册任务/空队列；无任务时退出码 1。
- `TASK_WORKER_EMBEDDED=true`：Web 进程内嵌 worker 消费 Redis 任务，慢任务可完成。
- `auto_import_tasks()`：`flask_server/task/` 自动发现；`_` 开头模块跳过。
- 入队失败契约：Redis 不可达时 `enqueue_task` 抛 `TaskQueueUnavailable`，不静默丢弃。

### 协议服务器
- TCP line：单帧、粘包、拆包、空帧跳过、CRLF、超长断开；handler 异常不断连接。
- TCP fixed：精确帧、拆包、粘包+残帧。
- TCP head_tail：剥离帧头帧尾、前缀垃圾丢弃、标记拆包、粘包、内层帧头按文档语义处理、
  帧尾缺失且超限断开。
- TCP raw：原始 recv 块回调。
- 并发连接上限、空闲超时、stop 关闭活动连接、stop 后拒绝新连接、无 handler 不启动、
  非法配置回退（framing/frame_length/帧头帧尾/分隔符/消息上限）。
- UDP：回显、返回 None 不回发、返回 str 告警不回发、主动 `send`、超长数据报平台行为、stop 幂等。

### 工程化
- `create_app(framework_config=Config({...}))`：创建期/请求期配置注入、两个 app 的 `api_version`/`AUTH_ENABLED` 隔离、
  默认 app 不受影响、上下文外回退默认配置、`monkeypatch.setattr(config, ...)` 兼容。
- `server.py` 开发入口、`wsgi.py` waitress 入口、`wsgi_gunicorn.py` 在 Windows 明确报错退出。
- 脚手架：生成/排除 `.env`/日志/缓存/数据库文件、LICENSE 署名替换、目标在模板内或非空时拒绝、
  生成项目 582 测试通过。
- compose prod：app/mysql/redis、worker profile、优雅停止时间、非 root 用户、依赖约束。
- CI 9 job、constraints 覆盖运行时依赖、examples/scripts 编译通过。

---

## 4. 发现的问题

> 以下为**修复前**的发现记录（保留作追溯）；每项的修复与回归结果见第 8 节。

### 4.1 P2：`RedisTaskQueue.stats()` 的 pending/ready 统计错误

**现象**（`_e2e/tasks_result.log`）：

```text
FAIL  Redis reserve 后 pending=1  :: expected=1 actual=0
FAIL  Redis reserve 后 ready=1    :: expected=1 actual=2
```

reserve 取走 1 条后，队列实际有 1 条 pending、1 条 ready；但 `stats()` 返回 `pending=0, ready=2`
（把 pending 算进了 ready）。初始 `ready=2, pending=0` 看起来正常，是因为 pending 恰为 0。

**根因**：`flask_server/module/task_queue.py:543-554`

```python
pending = self.client.xpending(self.stream, self.group)
pending_count = int(pending[0]) if pending else 0
```

redis-py 5.3.1 的 `XPENDING` 摘要返回 **dict**：

```python
{'pending': 1, 'min': '...', 'max': '...', 'consumers': [...]}
```

`pending[0]` 抛 `KeyError`，被外层 `except` 吞掉后 `pending_count=0`，于是 `ready = XLEN - 0` 把 pending 也算作 ready。
仓库测试的 `FakeRedis.xpending` 返回 list（`tests/test_task_queue_redis.py:92-94`），因此没有覆盖真实返回形状。

**建议修复**：

```python
pending = self.client.xpending(self.stream, self.group)
if isinstance(pending, dict):
    pending_count = int(pending.get('pending', 0) or 0)
elif pending:
    pending_count = int(pending[0])
else:
    pending_count = 0
```

并补一个使用真实 Redis（或返回 dict 的 fake）的回归用例。

**影响**：仅运维统计/监控（`task_queue_stats()`），不影响任务实际消费；但会把积压误报为可消费。

---

### 4.2 P2：死信记录不保存 args/kwargs，文档中的重放示例会丢参数

**现象**：`MemoryTaskQueue.dead_letters()` 与 `RedisTaskQueue.dead_letters()` 返回的记录只有
`id/name/status/attempts/max_retries/enqueued_at/last_error`，没有 `args/kwargs`。
测试中 `e2e.always_fail` 入队参数为 `['x']`，死信记录同样没有参数。

**相关代码**：
- `flask_server/module/task_queue.py:199-208` `_status_record()`（死信记录复用状态记录）
- `flask_server/module/task_queue.py:290-295` 内存后端 `dead()`
- `flask_server/module/task_queue.py:467-485` Redis 后端 `dead()` / `_dead_letter_raw()`

**文档**：`docs/task-queue.md:128-138` 的重放示例：

```python
letter = q.dead_letters(limit=1)[0]
q.enqueue(letter['name'], delay=0, task_id=letter['id'])
```

只按 `name + task_id` 重放，**没有 args/kwargs**；带参任务重放后会以“无参数”执行（通常直接失败）。

**建议**：死信记录额外保存 `args`/`kwargs`（只写死信，不写普通状态，避免状态键膨胀/泄露），
重放示例改为 `q.enqueue(letter['name'], *letter.get('args', []), task_id=letter['id'], **letter.get('kwargs', {}))`；
或至少在文档中明确“当前死信重放只适用于无参任务”。

---

### 4.3 P2：崩溃恢复只在“空闲出队”时触发，持续有任务时不会接管 stale pending

**现象**：用一个“永远有任务”的假队列跑 `TaskWorker` 0.3 秒，处理了 **9729** 条任务，
但 `reclaim_stale()` 调用次数为 **0**：

```text
acks 9729 reclaims 0
```

**根因**：`flask_server/module/task_worker.py:111-115`

```python
if reserved is None:
    self._maybe_reclaim()
    continue
self._handle(reserved)
```

`_maybe_reclaim()` 只在 `reserve()` 返回 `None`（队列暂时为空）时调用。只要队列持续有任务，
即使某个 worker 崩溃留下的 pending 已超过 `TASK_VISIBILITY_TIMEOUT`，也不会被 `XAUTOCLAIM` 接管，
直到队列出现空闲。

**影响**：高负载/持续积压场景下，崩溃 worker 的任务可能长期滞留 pending；
交付语义仍是 at-least-once，但“最终”可能被无限推迟。

**建议**：把 `_maybe_reclaim()` 移到每次循环顶部（它内部有 30s 间隔节流），
或每处理 N 条任务后检查一次；补一个“持续有任务时也会 reclaim”的用例。

---

### 4.4 P2/P3：配置注入的两个边界（SocketIO 开关、认证用户存储）

#### (a) `socketio_enabled` 注入被忽略

```python
app = create_app({'TESTING': True}, framework_config=Config({'socketio_enabled': True}))
# app.socketio is None（环境变量 SOCKETIO_ENABLED=false 时）
```

**根因**：`flask_server/app.py:7-14` 在**导入期**把 `_socketio_will_init = config.socketio_enabled` 固定下来；
`app.py:271-287` 的 `_init_socketio()` 只检查 `_socketio_will_init`，不检查传入的 `cfg.socketio_enabled`。
因此 `create_app(framework_config=Config({'socketio_enabled': True}))` 静默无效。

**建议**：`_init_socketio` 以 `cfg.socketio_enabled` 为准；若导入期未做 eventlet monkey patch，
至少对 `threading` 模式初始化并记录告警；若确实不支持注入，应在文档中明确写“SocketIO 开关是导入期/进程级”。

#### (b) `AUTH_STORE` 是进程级单例，跨 app 泄漏

```python
AuthService._store = None
app_a = create_app(..., Config({'auth_enabled': True, 'auth_store': 'memory'}))
app_b = create_app(..., Config({'auth_enabled': True, 'auth_store': 'sqlalchemy',
                                'sqlalchemy_uri': None}))  # 本应报“需要 SQLALCHEMY_URI”
# 先请求 app_a 注册用户后，app_b 的注册也成功（实际用了 memory store）
```

**根因**：`flask_server/component/auth.py:239-253` 的 `_store` 是类属性，`_get_store()` 只在首次调用时按
`config.auth_store` 选择；之后所有 app 共用。`create_app` 的配置注入因此无法真正隔离用户存储，
且 `AUTH_STORE=sqlalchemy` 漏配 URI 的清晰报错会被前一个 app 的 memory store 掩盖。

**影响**：单 app 部署不受影响；同进程多 app（测试/多租户/多配置实例）会出现用户数据串用或误配置静默通过。

**建议**：把 store 放到 `app.extensions`（或按配置对象/URI 做 key），`_get_store()` 优先取当前 app 的 store，
上下文外再回退进程默认；至少应在文档的“进程级单例”清单中补上“认证用户存储”。

---

### 4.5 P3：`head_tail` 同包“内层帧头 + 帧尾”的语义与文档一致，但容易误判

测试确认当前行为符合 `docs/protocol-servers.md:120-121`：

- 帧尾缺失、新帧头先到（且尚未看到帧尾）→ 重同步到新帧头（`\xaaabc` + `\xaay` + `\xbb` → `y`）。
- 新帧头与帧尾**同包**到达 → 按“首帧头 + 首帧尾”解析（`\xaaabc` + `\xaay\xbb` → `abc\xaay`）。

这不是 bug，但代码注释 `flask_server/module/tcp_server.py:288-290` 写的是“帧头后、帧尾前再次出现帧头 →
以更靠后的帧头重新同步”，与“同包有帧尾时按首帧头+首帧尾”的细节不完全一致。建议把注释补全，
避免后续维护者误改；如需严格“内层帧头优先”，则要明确这是协议语义变更。

---

### 4.6 P3：`.env.example` 缺少 `TASK_STATUS_MAX`

`Config` 支持 `TASK_STATUS_MAX`（`flask_server/config.py:326-328`），
`docs/configuration.md` 与 `docs/task-queue.md` 都已列出，但 `.env.example:122-138` 的任务队列段没有该项。
建议补：

```ini
# 内存后端状态表上限（防内存无界增长）
TASK_STATUS_MAX=10000
```

---

### 4.7 P3：`scripts/benchmark.py` 依赖说明过期

`scripts/benchmark.py:13` 仍写：

```text
仅依赖 requests（已含在 requirements.txt）。
```

但 `requests` 已移到 `requirements-dev.txt`（`requirements.txt:24` 也有说明）。
建议改为“需安装 requirements-dev.txt（requests）”。

---

### 4.8 P3：UDP 超长数据报的文档写“静默截断”，Windows 实际是丢弃

`docs/protocol-servers.md:152` 写“超过此大小的数据报会被操作系统静默截断”。
在 Windows 上实测：`UdpServer(max_message_length=64)` 收到 100 字节数据报时，
`recvfrom` 缓冲不足会触发 `WSAEMSGSIZE`，数据报被**丢弃**，handler 根本不会收到回调（客户端无回复）。
Linux 才是截断后返回。建议文档改为“Linux 截断 / Windows 丢弃（取决于平台）”，
或在 `UdpServer` 中捕获该错误并记录告警（当前由 socketserver 的 `handle_error` 处理，日志不显眼）。

---

### 4.9 P3：脚手架会复制缓存/测试产物目录

`scripts/scaffold.py:21-25` 的排除列表只有 `.git/__pycache__/.pytest_cache/.venv/venv/htmlcov/.idea/.vscode/.opencode`，
没有 `.ruff_cache`、`.coverage_html`、`.mypy_cache` 等。实测生成目录包含：

```text
.coverage_html/
.ruff_cache/
test-results.xml
_e2e/            # 本次测试脚本（非产品文件）
```

`.ruff_cache` 是执行 `make lint` 后正常存在的缓存，生成新项目时不应带过去。
建议在 `ALWAYS_EXCLUDE_DIRS` 增加 `.ruff_cache`、`.mypy_cache`、`.coverage_html`、`.tox`、`.nox`，
在 `EXCLUDE_FILES` 增加 `test-results.xml`、`.coverage.*`。

---

### 4.10 P3：`examples/api.http` 的 404 示例在 `AUTH_ENABLED=true` 下实际返回 401

`examples/api.http:61-62`：

```http
### 404 示例
GET {{host}}/api/nonexistent
```

项目 `.env` 默认 `AUTH_ENABLED=true`，全局认证拦截器在路由匹配前执行，
因此该请求返回 **401（code 4002）**，不是 404。这不是安全问题（隐藏未知 API 路径也有合理性），
但示例应注明“需带 token 才能看到 404”，或改用非 `/api/` 路径（如 `/missing.js`）演示 404。

---

### 4.11 备注（非缺陷）：限流“固定窗口”的文档措辞

`docs/api-conventions.md` / `docs/configuration.md` 称限流为“固定窗口”，
但实现（`SimpleMemoryCache.incr` / `RedisCache.incr`）每次自增都会顺延 TTL，
更接近“滑动窗口/活动期内计数”。这是刻意的防爆破设计（`redis_cache.py:139-142` 有注释），
建议文档改成“滑动/顺延窗口”，避免运维误解“每分钟整点重置”。

---

## 5. 未覆盖 / 受限项

1. **Docker 镜像构建与容器运行**：本机 Docker daemon 未启动，只验证了 `docker compose config`
   （含 prod profile 行为）；未执行 `docker build` / `up` / 健康检查。
2. **gunicorn 多 worker**：Windows 不支持 gunicorn；`scripts/smoke_gunicorn.py` 按设计跳过（已验证退出码 0），
   `wsgi_gunicorn.py` 明确报错退出。真实多 worker 的协议服务器跳过/指标聚合需在 Linux CI 或 WSL 验证。
3. **优雅关闭信号**：`wsgi.py` 的 SIGTERM/SIGINT 逻辑由单元测试覆盖；Windows 下用 `CTRL_C_EVENT`
   探测未能让子进程收到信号（无控制台），因此未做真实信号端到端验证。
4. **`scripts/verify_real_env.py`**：会 `CREATE DATABASE`、跑迁移、写入真实 MySQL/Redis；
   为避免改动远端环境未执行。本次对远端 MySQL/Redis 只做只读探测。
5. **SocketIO / WebSocket**：仅验证了关闭状态与配置注入边界，未做真实 WebSocket 收发。
6. **长稳/压力**：只跑了 `benchmark.py` 40 请求的冒烟，未做长时间 soak、并发限流/任务队列压测。
7. **安全审计**：未运行 `pip-audit`（需要网络/依赖库）；本次只做了应用层安全检查。

---

## 6. 复现方式

```bash
# 基线
python -m pytest tests/ -q --cov=flask_server --cov-report=term-missing
python -m ruff check flask_server/ tests/ scripts/ examples/

# 端到端（脚本在 _e2e/，使用真实 Redis DB15 / 远端 MySQL 只读 / SQLite 临时库）
python _e2e/e2e_http.py
python _e2e/e2e_protocols.py
python _e2e/e2e_tasks.py
python _e2e/e2e_misc.py
```

说明：

- `_e2e/common.py` 会把测试进程的关键环境变量重置为安全默认值，避免 `.env` 污染；
  子进程通过显式 env 启动。
- 任务队列测试使用 `REDIS_URL` 的 DB 15 + 唯一 stream/group，并在结束后 `SCAN` 删除 `e2e:*` 键；
  限流测试使用 `10.x` 测试 IP 并在结束后清理。
- `_e2e/tmp`、`_e2e/logs` 为运行产物；`_e2e/*_result.log` 为各套件结果。

---

## 7. 建议修复优先级（均已处理）

| 优先级 | 项 | 建议 |
|---|---|---|
| P2 | 4.1 Redis stats | 修 `xpending` dict 分支 + 真实 Redis 回归用例 |
| P2 | 4.2 死信参数 | 死信记录保存 args/kwargs；修文档重放示例 |
| P2 | 4.3 reclaim 饥饿 | 每轮循环按 30s 节流调用 `_maybe_reclaim()` |
| P2/P3 | 4.4 配置注入边界 | SocketIO 按 `cfg` 初始化；Auth store 改为 per-app |
| P3 | 4.6–4.10 | `.env.example`、benchmark docstring、UDP 文档、脚手架排除、api.http 示例 |
| 备注 | 4.11 | 限流窗口措辞改为“滑动/顺延窗口” |

以上项均已在第 8 节修复；4.5 为文档语义说明，无需代码变更。

---

## 8. 修复记录与复测（2026-10-29）

### 8.1 修复内容

| 问题 | 修复 |
|---|---|
| 4.1 Redis stats | `flask_server/module/task_queue.py::RedisTaskQueue.stats()` 兼容 `XPENDING` 的 dict/list 两种返回；`pending` 不再恒为 0，`ready = XLEN - pending` |
| 4.2 死信参数 | `BaseTaskQueue._dead_letter_record()` 在死信记录中保存 `args`/`kwargs`；内存/Redis 后端统一使用；普通状态记录仍不携带参数 |
| 4.3 reclaim 饥饿 | `TaskWorker._loop()` 在每轮循环顶部调用 `_maybe_reclaim()`（保留 30s 节流），不再只在队列空闲时检查 |
| 4.4a SocketIO 注入 | `create_app` 的 `_init_socketio()` 改为按 `cfg.socketio_enabled` 初始化；eventlet 未在导入期 monkey_patch 时降级 threading 并告警 |
| 4.4b Auth store 泄漏 | `AuthService` 增加 per-app store（`app.extensions['flask_server.auth_store']`）；无 app 上下文时仍回退进程默认 `_store`，兼容 CLI/后台线程/服务层直测 |
| 4.6 `.env.example` | 补 `TASK_STATUS_MAX=10000` |
| 4.7 benchmark docstring | 改为“需安装 requirements-dev.txt” |
| 4.8 UDP 文档 | 说明 Linux 截断、Windows `WSAEMSGSIZE` 丢弃；同步 `udp_server.py` 注释 |
| 4.9 脚手架 | 排除 `.ruff_cache` / `.coverage_html` / `.mypy_cache` / `.tox` / `.nox` / `test-results.xml` |
| 4.10 api.http | 404 示例补充“AUTH_ENABLED=true 时未带 token 先 401”的说明 |
| 4.11 限流措辞 | 代码注释与 `api-conventions.md` / `configuration.md` / `deployment.md` 改为“顺延窗口” |

### 8.2 新增/更新回归测试

- `tests/test_task_queue_redis.py`：`FakeRedis.xpending` 改为真实 redis-py 5.x 的 dict 形状；
  新增 `test_stats_pending_after_reserve`、`test_stats_accepts_legacy_list_xpending`；
  死信读取断言 args/kwargs。
- `tests/test_task_queue.py`：新增 `test_worker_reclaims_stale_while_busy`；
  内存死信断言 args/kwargs，且普通状态不含参数。
- `tests/test_config_injection.py`：新增 SocketIO 注入、per-app AUTH_STORE 隔离、
  `AUTH_STORE=sqlalchemy` 漏配 URI 的 per-app 报错测试。
- `tests/test_auth.py`：`fresh_store` 清理默认 app 的 per-app store；
  模拟用户删除/存量用户名的用例改为在 app context 内取 store。
- `tests/test_scaffold.py`：断言 `.ruff_cache` / `.coverage_html` / `test-results.xml` 不被复制。
- `tests/test_rate_limit.py`：docstring 改为“顺延窗口”。

### 8.3 复测结果

| 项 | 结果 |
|---|---|
| `pytest tests/ -q --cov=flask_server` | **582 passed / 0 failed**，覆盖率 **91.27%** |
| `ruff check flask_server/ tests/ scripts/ examples/` | **All checks passed** |
| E2E HTTP / 认证 / 限流 / DB | **206/206** |
| E2E TCP / UDP | **70/70** |
| E2E 任务队列 | **110/110**（修复前 106/108） |
| E2E 脚手架 / 部署 / CI / 文档 | **74/74**（修复前 70/74） |
| **E2E 合计** | **460/460** |
| 生成项目独立测试 | **582 passed** |

### 8.4 变更文件

- 代码：`flask_server/module/task_queue.py`、`flask_server/module/task_worker.py`、
  `flask_server/app.py`、`flask_server/component/auth.py`、`flask_server/component/rate_limit.py`、
  `flask_server/module/udp_server.py`
- 配置/脚本：`.env.example`、`scripts/benchmark.py`、`scripts/scaffold.py`、`examples/api.http`
- 文档：`README.md`、`CHANGELOG.md`、`docs/api-conventions.md`、`docs/architecture.md`、
  `docs/configuration.md`、`docs/deployment.md`、`docs/development.md`、
  `docs/protocol-servers.md`、`docs/task-queue.md`
- 测试：`tests/test_task_queue.py`、`tests/test_task_queue_redis.py`、
  `tests/test_config_injection.py`、`tests/test_auth.py`、`tests/test_scaffold.py`、
  `tests/test_rate_limit.py`

### 8.5 未处理项

- 4.5 `head_tail` 同包语义：经复核符合 `docs/protocol-servers.md:120-121` 的既有约定，
  不是缺陷；仅建议后续把代码注释补全（本次未改代码，避免改变协议行为）。
- 第 5 节的 Docker/gunicorn/信号/真实环境自检限制仍存在，属于测试环境限制，不是代码缺陷。

