# Changelog

本模板项目的变更记录。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 修复

- **任务队列自查修正**（随功能上线前的一轮自审）：
  - `scripts/worker.py --once` 原先会先 `start()` 起消费线程、主线程再同步消费，语义混乱且
    可能在线程仍工作时退出；改为 `TaskWorker.process_one()` 同步处理（不开线程）
  - `RedisTaskQueue.__init__` 建 consumer group 失败时抛出裸 `ConnectionError`，
    与"入队失败抛 `TaskQueueUnavailable`"的契约不一致（控制器会漏成 500）；
    现统一包装为 `TaskQueueUnavailable`；`get_status` 读取失败同样包装
  - 补 `RedisTaskQueue.dead_letters()`（与内存后端对齐，运维排查用）
  - `TASK_WORKER_EMBEDDED=true` 时内嵌 worker 收到 SIGTERM 未停止（在途任务会被直接杀掉）：
    `wsgi.py` 关闭流程现在**先停内嵌 worker**，再停协议服务器、释放 DB/Redis
  - 装饰器 `task` 不再从 `flask_server.module` 的 `__all__` 导出（与子包 `flask_server.task`
    同名，经 `from .module import *` 会在包属性上互相覆盖，与早前 `config` 的坑同类）
- **文档与代码同步（本轮全量核对）**：
  - `docs/configuration.md` / `.env.example` 补齐 `API_TITLE`、`API_VERSION`、
    `API_SPEC_URL`、`API_DOCS_URL`（此前代码支持但两处文档均未列出）
  - `docs/architecture.md` 启动流程补内嵌 worker 与退出顺序；`module/` 职责补任务队列
  - `docs/deployment.md` 多进程注意事项补"任务消费者独立部署"；优雅退出表补
    `scripts/worker.py` 与内嵌 worker 的停止行为
  - `docs/task-queue.md` 死信重放示例改用 `dead_letters()`；配置表补 `TASK_STATUS_MAX`
  - README：CI 流水线数（七 → 九）、`scripts/` 职责、用例数与覆盖率（576 / 91.1%）
  - `docs/development.md` 补 worker 命令；用例数同步

### 新增

- **可持久化的后台任务队列**（`flask_server/module/task_queue.py` + `task_worker.py` +
  `scripts/worker.py`）——补上脚手架最后一个"业务必然自己造轮子"的能力：
  - 后端：配置 `REDIS_URL` 用 **Redis Streams**（consumer group/XACK，多 worker 正确、
    重启不丢、崩溃可接管）；未配置时退化为进程内内存队列（单进程开发/测试）
  - 声明：`flask_server/task/` 下 `@task('name', max_retries=N)` 即自动发现
    （worker 启动时 `auto_import_tasks()`）
  - 入队：`enqueue_task(name, *args, delay=..., **kwargs)` 返回 `task_id`；
    后端不可用时抛 `TaskQueueUnavailable`（**不静默丢弃**，与缓存降级策略刻意不同）
  - 消费：`scripts/worker.py --concurrency 4`（`make worker`），失败按
    `backoff * 2**(n-1)` 指数退避重投，超上限进死信（`{stream}:dead`）；
    worker 崩溃留下的 pending 消息超过 `TASK_VISIBILITY_TIMEOUT` 由 `XAUTOCLAIM` 接管
  - 状态：`get_task_status(task_id)` → `queued/running/succeeded/retrying/dead` + `attempts` + `last_error`
  - 开发便利：`TASK_WORKER_EMBEDDED=true` 在 Web 进程内嵌 worker（`server.py` / `wsgi.py`），
    内存后端下也能单进程跑通全链路
  - 交付语义 **at-least-once**，handler 必须幂等（文档已强调）
- **配置**：`TASK_QUEUE_STREAM` / `TASK_QUEUE_GROUP` / `TASK_WORKER_CONCURRENCY` /
  `TASK_MAX_RETRIES` / `TASK_RETRY_BACKOFF` / `TASK_VISIBILITY_TIMEOUT` /
  `TASK_STATUS_TTL` / `TASK_DEAD_LETTER_MAX` / `TASK_STATUS_MAX` / `TASK_RESERVE_TIMEOUT` /
  `TASK_WORKER_EMBEDDED`
- **`docs/task-queue.md`**：三步接入、语义与保证、状态机、配置表、死信运维与重放、注意事项
- `tests/test_task_queue.py` + `tests/test_task_queue_redis.py`（50 例）：注册表/内存后端/
  worker 端到端（成功·重试·死信·未注册·并发·停止·后端故障恢复）/CLI/内嵌 worker，
  以及 Redis 后端的命令级验证（假客户端模拟 Streams/ZSET/LIST 语义）

### 新增

- **配置注入**：`Config(overrides)` + `create_app(framework_config=...)`，配合
  `ConfigProxy` 实现"按 app 解析配置"而不改动任何 `config.xxx` 调用点：
  - `Config({'api_version': 'v9', 'auth_enabled': True})` 可覆盖任意环境变量派生字段
  - `create_app(framework_config=cfg)`：创建期装配（CORS/SECRET_KEY/OpenAPI/SocketIO/
    DB/组件开关）与请求期 `config.xxx` 都解析到该对象（存于
    `app.extensions['flask_server.config']`，亦可通过 `app.framework_config` 取用）
  - app 上下文之外（后台线程、CLI、导入期）回退进程默认配置，
    `monkeypatch.setattr(config, 'x', y)` 等既有写法与测试**完全不受影响**
- **`docs/architecture.md`**：架构总览——分层与依赖方向、目录职责、启动流程、
  create_app 装配顺序、请求生命周期（钩子顺序/异常映射/路由匹配）、配置解析链、
  进程模型与单例边界、扩展点、改动决策表、已知约束
- `tests/test_config_injection.py`（11 例）：覆盖 Config 覆盖、创建期/请求期注入、
  按 app 隔离的 AUTH_ENABLED、上下文外回退默认、代理写入语义

### 变更

- **引入 `create_app()` 应用工厂**（架构重构，用法向后兼容）：
  - `flask_server/app.py` 新增 `create_app(app_config=None)`，显式创建并装配应用；
    模块底部的 `app = create_app()` 保留 `from flask_server import app`、
    `FLASK_APP=flask_server.app:app` 等既有用法
  - 组件（限流 / 认证 / 指标）不再在 import 时修改全局 app，改为暴露
    `init_rate_limit(app)` / `init_auth(app)` / `init_metrics(app)`，
    由 `component.init_components(app)` 统一装配（顺序：限流 → 认证 → 指标）
  - controller 自动发现改为 `register_controllers(app, api)`：除 Blueprint 外，
    模块可定义 `def register(app)` 挂载 `@app.route` 风格路由
    （`hello_controller` / `webui_controller` 已改用该方式），装配幂等（标记在
    `app.extensions`，重复调用不会产生重复路由）
  - 修复 `init_SQLAlchemy()` 二次调用会把已初始化的 `db` 重置为 `None` 的问题
    （否则第二个 app 实例会丢掉 ORM 绑定）
  - `flask_server/__init__.py` 先导入 config 再导入 app，避免子模块导入把包的
    `config` 属性覆盖成模块对象（`from flask_server import config` 拿到模块的坑）
  - 仍是进程级单例的部分（SQLAlchemy / Migrate / SocketIO / Prometheus 指标 /
    缓存）已在 `create_app()` 文档字符串与 `docs/development.md` 中说明

### 新增

- `tests/test_factory.py`（10 例）：独立实例、路由装配完整性、钩子注册顺序、
  异常处理、重复创建可重入、controller 装配幂等、`db` 单例复用

- **gunicorn 多进程冒烟脚本**（`scripts/smoke_gunicorn.py` / `make smoke-gunicorn`）：
  在真实 gunicorn 下校验——`WORKER_NUM=2 + TCP_ENABLED=true` 时协议服务器被跳过
  且全部 worker 存活（避免 0.5.0 修复的那条崩溃循环回归）、`WORKER_NUM=1` 时协议
  服务器正常监听、SIGTERM 后 master 退出码为 0。已接入 CI
  （`test-gunicorn-multiprocess` job，Linux）
- **依赖锁定**：新增 `constraints.txt`（由 `scripts/freeze_constraints.py` 生成，
  只锁版本不改依赖关系）与 `test-locked-deps` CI job；Dockerfile 构建时应用约束，
  保证"CI 测的版本"与"镜像里的版本"一致
- **`/docs` CSP 与真实页面一致性用例**：从 `/docs` 返回的 HTML 中提取每个 CSS/JS 的
  origin，逐个校验对应 CSP 指令已放行——比"断言包含某个域名"更强，能直接捕获
  "头部看着对、浏览器却拦了资源"这类问题（0.5.0 修复的正是这一类）
- **启动自检补一条**：`AUTH_ENABLED=true` 且 `RATE_LIMIT_ENABLED=false` 时告警。
  防爆破锁定按 (用户名, IP) 隔离后，单个 IP 仍可轮换用户名试探，没有全局限流时
  撞库只受 PBKDF2 的 CPU 成本约束

## [0.5.0] - 2026-10-22

### 行为变更（升级前必读）

本版修复了多处"生产部署才暴露"的问题，其中 5 项会改变既有行为：

1. **X-Forwarded-For 改为取最右**：由"取第一个非空项"改为从右向左取第一个非可信 IP。
   若你的部署依赖"取最左"（例如多级代理但只把第一跳配进 `TRUSTED_PROXIES`），
   真实客户端 IP 的判定结果会变化，请复核 `TRUSTED_PROXIES` 配置。
2. **waitress 不再丢弃 `X-Forwarded-*`**（`clear_untrusted_proxy_headers=False`）：
   信任决策统一交给 `TRUSTED_PROXIES` + `get_real_ip()`。若此前依赖 waitress 清除
   代理头来做隔离，请确认 `TRUSTED_PROXIES` 只包含可信代理。
3. **登录锁定键格式变化**：`auth:login_lock:{username}` → `auth:login_lock:{username}:{ip}`
   （失败计数同理）。滚动升级期间旧键不再生效，进行中的锁定会提前解除，影响可忽略。
4. **PBKDF2 迭代次数默认 100000 → 600000**：旧哈希照常校验（迭代次数写在哈希串里），
   但新写入哈希的登录/注册 CPU 成本约 **×6**（单次约 50ms → 250ms）。
   高并发登录场景请评估：可调回 `AUTH_PBKDF2_ITERATIONS=310000`，或改用 Argon2id。
5. **`requests` 移出运行时依赖**：`scripts/benchmark.py` 需安装 `requirements-dev.txt`。

（`flask_server/controller/` 的导入失败现在会给出明确的 `RuntimeError`，行为仍是
fail-fast，仅报错信息变化。）

### 修复

- **gunicorn 多 worker + 协议服务器崩溃循环（P0）**：`post_worker_init` 在每个 worker
  都会执行，原实现让每个 worker 都 `start_protocol_servers()` —— worker 2..N 因端口已被
  占用抛 `OSError`，异常穿出钩子导致 worker 退出 → master 重拉 → 无限重启（HTTP 只剩
  1 个 worker 可用）。现在 `WORKER_NUM>1` 时跳过启动并只告警一次；单 worker 下绑定失败
  也只记 ERROR，不再让 worker 崩溃
- **waitress 优雅关闭（P0）**：`wsgi.py` 改为 `waitress.create_server()` + 信号驱动
  `server.close()`（停止 accept，等在途请求处理完）→ 释放依赖 → 退出；空闲 keep-alive
  连接由 15s 看门狗兜底，二次信号立即退出。修复前信号处理器直接 `os._exit(0)`，
  在途请求被强杀（滚动更新偶发 502）
- **X-Forwarded-For 解析可被伪造（P1）**：由"取最左非空项"改为**从右向左取第一个
  非可信且合法的 IP**。反向代理普遍是追加语义（`$proxy_add_x_forwarded_for`），最左项
  由客户端提供、可任意伪造——修复前攻击者每个请求换一个假 XFF 即可绕过 IP 限流、
  污染审计日志。整条链路均为可信代理时回退最左项，非法字面量跳过
- **waitress 默认丢弃 X-Forwarded-For（P1，实机验证发现）**：waitress 3 的
  `clear_untrusted_proxy_headers` 默认 True，未配置 `trusted_proxy` 时会**删除**请求中的
  `X-Forwarded-*`。这会让上面的可信代理逻辑在生产入口下完全失效——置于反向代理之后时
  所有请求的 `remote_addr` 都是代理 IP，IP 限流退化成全站共享一个桶、审计日志丢失真实来源。
  `wsgi.py` 现显式传 `clear_untrusted_proxy_headers=False`，信任决策仍由
  `TRUSTED_PROXIES` + `get_real_ip()` 统一负责
- **`/docs` 被自身 CSP 打残（P1）**：CSP 改为按 `SWAGGER_UI_URL` 动态放行
  `scheme://host`，并补齐 `img-src`/`font-src`。修复前 `style-src` 未放行 CDN
  （swagger-ui.css 被拦 → 页面无样式），按文档改内网镜像后脚本也被拦（/docs 白屏）
- **容器内雪花 ID 撞号（P1）**：worker_id 由 `pid % 32` 改为
  `(crc32(hostname) + pid) % 32`（容器内 PID 恒为 1，原实现所有副本同为 1）；
  新增 `SNOWFLAKE_DATACENTER_ID`；worker 改为惰性初始化（导入期不再读配置/建对象）；
  新增 `KeyGenerator.generate_uuid7()`（时间有序 UUID，无需分配机器号）
- **TCP 空闲连接耗尽并发槽位（P1）**：新增 `TCP_IDLE_TIMEOUT`（默认 300s），
  只连不发的连接由服务端关闭并释放槽位；`≤0` 关闭超时。修复前 256 个空连接即可
  占满 `TCP_MAX_CONNECTIONS`，正常客户端被拒
- **登录锁定型 DoS（P1）**：防爆破计数/锁定键由"仅按用户名"改为**(用户名, 来源 IP)**，
  攻击者无法再靠故意打错密码把受害者账号锁死；跨 IP 失败累计 50 次打告警日志
  （观测分布式撞库，不阻断）。`login()` 新增 `client_ip` 参数
- **refresh token 换取令牌可能 500（P1）**：`refresh_access_token` 校验用户是否仍存在
  （已删除 → 返回 401），`/login` 与 `/refresh` 均对 `user is None` 兜底，
  修复前对 `None` 取 `user.uid` 触发 AttributeError
- **密码哈希强度与比较方式**：PBKDF2 迭代次数默认值改为 `AUTH_PBKDF2_ITERATIONS`
  （默认 600000，对齐 OWASP 2023）；校验改用 `hmac.compare_digest`（常量时间）。
  时序均衡用的虚拟哈希与真实哈希参数保持一致
- **日志伪造（log forging）**：`Logger` 出口统一转义消息中的 CR/LF，用户可控输入
  （URL/用户名/请求头）中的换行不再能拆出伪造日志行；**新注册**用户名收敛为
  `[A-Za-z0-9_.@-]`，**登录**仅禁止控制字符（不用严格白名单，避免存量中文/带空格
  用户名在升级后 422 登录失败）
- **SQLite 标识符注入面**：表名/列名改为白名单校验 + 引用（`ValueError` 拒绝
  `users; DROP TABLE ...` 之类），`limit` 强制整数；`conditions`/`order_by`
  明确标注为原始 SQL 片段（仅可传常量）
- **scaffold 自嵌套**：目标目录位于模板目录内部时拒绝执行（修复前 `copytree`
  会边复制边递归复制自身，直到路径过长）
- **`SafeThread` 崩溃日志**：`traceback.print_exc()`（stderr，容器里可能丢弃）改为
  `Logger.error(exc_info=True)`，与统一日志/JSON 格式一致
- **controller 自动发现的报错可读性**：模块导入失败时包装为明确的 `RuntimeError`
  （指出模块名与排查方向），并文档化"controller fail-fast / handler 容错跳过"的差异
- **启动噪音**：`TCP_FRAME_HEAD`/`TCP_FRAME_TAIL` 默认留空不再触发
  `[Config WARNING]`（空值属未配置；非法转义仍告警）。修复前 `cp .env.example .env`
  后每次启动都打两条无意义告警
- **banner 多 worker 判定与 gunicorn 默认对齐**：`check_production_config` /
  `print_startup_banner` 新增 `worker_num` 参数，gunicorn 入口（`wsgi_gunicorn.py`）
  传入实际 worker 数（未设 `WORKER_NUM` 时默认 4），使 Redis/token/TCP/日志的多
  worker 告警在默认配置下也能触发；waitress（wsgi.py）单进程不误报
- **Logger request_id 上下文线程安全**：`_request_id_ctx` 改为模块级创建
  `ContextVar`，移除惰性 `_ensure_ctx` 的并发竞态（多线程首个请求窗口个别日志
  rid 关联错位）
- **文档事实修正**：`LOG_FILE_PATH` 说明改为"未设置/为空时仅控制台、不写文件"
  （此前文档称默认写 `server.log`，与代码行为不符）

### 新增

- **Prometheus 多进程指标**：支持 `PROMETHEUS_MULTIPROC_DIR`，`/metrics` 聚合所有
  worker 的指标（目录不存在自动创建），gunicorn 通过 `child_exit` 调用
  `mark_process_dead` 清理退出 worker 的指标文件。修复前多 worker 下每个进程各自计数，
  `/metrics` 只能看到被刮到的那一个进程
- **HSTS**：`HSTS_ENABLED`（默认 false）/ `HSTS_MAX_AGE`，置于 HTTPS 反代之后建议开启
- **配置项**：`TCP_IDLE_TIMEOUT`、`AUTH_PBKDF2_ITERATIONS`、`SNOWFLAKE_DATACENTER_ID`、
  `PROMETHEUS_MULTIPROC_DIR`、`HSTS_ENABLED` / `HSTS_MAX_AGE`
- **测试**：424 → 490 用例（覆盖率实测 91.9%），新增多 worker 钩子、优雅关闭、
  XFF 伪造、CSP/HSTS、UUIDv7、TCP 空闲超时、IP 隔离锁定、refresh 兜底、
  日志注入、SQLite 标识符、scaffold 守卫等回归用例

### 变更

- `requests` 从运行时依赖移至 `requirements-dev.txt`（仅 `scripts/benchmark.py` 使用，
  生产镜像少装 urllib3/certifi/idna/charset-normalizer 等 5 个传递依赖）

### 文档

- 同步 `docs/configuration.md`（新增环境变量表项）、`docs/api-conventions.md`
  （XFF 解析规则、防爆破语义、refresh 兜底）、`docs/deployment.md`（多 worker 防护、
  优雅关闭、多进程指标、HSTS、CSP 与 SWAGGER_UI_URL、waitress 代理头清理）、
  `docs/protocol-servers.md`（空闲超时）

## [0.4.0] - 2026-08-13

### 修复

- **文档事实修正**：`/metrics` 不在 `/api/` 下、不受全局认证拦截（无需 X-AUTH-TOKEN），
  修正部署指南中的错误说法；教程模型 passwd 列宽 128→256（PBKDF2 存储约 168 字符，
  128 列宽在 MySQL 严格模式下插入报 Data too long）
- **examples 密码示例统一**：`user_crud_service.py` 的 create/update 改用
  `pbkdf2_hmac`（与 `user_service.py` 一致，修复前两处仍用 sha256，教学样例自相矛盾）
- **scaffold 排除内部评估报告**：生成新项目不再复制 `PROJECT_EVALUATION.md`
  （内部评估/审查历史不应随模板分发）
- **WORKER_NUM 校验**：≤0 告警回退默认（gunicorn workers 传负数/0 直接崩溃）；
  新增 `_parse_worker_num` 统一 banner 与 gunicorn 入口的解析
- **banner 多 worker 告警补全**：`AUTH_ENABLED=true` + `AUTH_STORE=memory` 多 worker
  部署时告警（用户表进程内，注册/登录跨 worker 不一致）
- **集成测试库名标识符校验**：`test_sqlalchemy_integration.py` 建库前校验库名为
  合法标识符（与 verify_real_env 风格一致，防标识符注入）
- **反射与声明式模型共存**：`DB_REFLECT_ON_START=true`（默认）+ 已建表（如
  `flask db upgrade` 建好的 user 表）+ 声明式 UserPO 时，二次启动不再崩溃
  （修复前 reflect 先于模型导入执行，`Table 'user' is already defined`
  InvalidRequestError）；现在先导入声明式模型占据表名，仅反射未声明的表
- **auth token TTL 校验**：`AUTH_TOKEN_TTL` / `AUTH_REFRESH_TOKEN_TTL` ≤0 告警回退默认
  （修复前 0=永久 token、负数=Redis setex 报错降级与内存立即过期的分裂行为）
- **SQLite 初始化脚本容错**：`INIT_SQL_PATH` 脚本语法错误时告警降级继续启动，
  不再因 `executescript` 异常导致应用 import 即崩溃
- **`@json_response` 统一 JSON**：视图返回 str/bytes（含元组首元素）时归一为
  `GraceResult.ok(data=值)`，修复前被 Flask 渲染为 text/html 破坏统一响应格式
- **socketio 示例 None 保护**：`SOCKETIO_ENABLED=false`（默认）时跳过注册并告警，
  修复前直接 `@socketio.on` 触发 AttributeError
- **登出吊销 refresh token**：`POST /api/v1/auth/logout` 支持可选 body `{"refresh_token"}`，
  传入即一并吊销（旧 refresh 无法再换取新令牌，会话真正结束；不携带保持向后兼容，
  refresh 到期自然失效）
- **限流/认证计数参数范围校验**：`RATE_LIMIT_PER_MINUTE`（≤0 时计数 1 即全量 429）、
  `AUTH_LOGIN_MAX_FAILS`（≤0 时首次失败即锁定）、`AUTH_LOGIN_LOCK_SECONDS`（≤0 时
  ttl=0 写成永久锁定键）非法值告警并回退默认
- **CORS 空配置兜底**：`CORS_ORIGINS` 显式设为空字符串时告警并回退 `*`
  （修复前解析为 `[]` 导致 CORS 静默失效）
- **4xx 响应体精简**：HTTP 异常 4xx 只回显 `description`，去掉
  `"405 Method Not Allowed:"` 这类英文前缀长句（保持响应文案简洁一致）
- **X-Request-Id 清洗**：透传的 `X-Request-Id` 清洗控制字符并截断至 64 字符
  （防日志格式污染/超长日志行与响应头），全控制字符回退自动生成
- **`LocalFileStorage.resolve_path` 公开接口**：对外提供经路径穿越校验的路径解析
  （examples 文件样例不再调用私有 `_gen_final_path`）
- **examples 教学修正**：`user_service.py` 密码存储改 PBKDF2（原 sha256 违背安全最佳
  实践，配套 `user_declared.py` passwd 列宽 128→256）；`article_service.modify_by_aid`
  补 None 判空；`user_crud_controller.py` 注册说明改为自动注册（原注释与机制矛盾）
- **`verify_real_env.py` 加固**：限流步骤显式开启 `RATE_LIMIT_ENABLED`（不再依赖 .env
  是否启用，修复前未启用时限流步骤必然 FAIL）；`CREATE/DROP DATABASE` 增加库名标识符
  校验（MySQL 不支持参数化 DDL，防标识符注入）
- **日志路径兜底**：`LOG_FILE_PATH` 指向不存在的目录时自动创建；创建/打开失败
  （权限不足、父路径为文件等）降级为控制台日志并告警，不再因日志路径问题导致
  应用 import 即崩溃
- **JSON 日志补堆栈**：`LOG_FORMAT=json` 时 `exc_info=True` 的日志附带 `exception`
  字段（完整 traceback），修复前 JSON 模式下堆栈被丢弃、ELK 排查生产故障困难
- **密码脱敏完整性**：`mask_uri` 对含 `@` 的密码（如 `p@ssw0rd`）完整脱敏到凭据
  分隔符，修复前 `user:p@ss@host` 会泄漏 `@` 后残段；`@` 出现在 query 中不受影响
- **探针尾斜杠豁免**：限流豁免路径判定对尾斜杠归一（`/metrics/` 与 `/metrics`
  同豁免），防 Prometheus 等采集端配置尾斜杠 URL 时被误限流 429
- **webui 尾斜杠重定向**：带尾斜杠的路径命中已注册路由（`/metrics/` `/docs/`
  `/hello/` 等）时返回 308 重定向到规范路径，修复前落入 SPA 回退被吞掉
  （Prometheus 抓 `/metrics/` 拿到 200 index.html，指标解析失败且无感知）
- **内存缓存写契约**：`SimpleMemoryCache.set` 返回 `True`（与 RedisCache 一致），
  修复认证 `_cache_set` 在内存模式下恒返回 False 的契约误导
- **限流键截断**：限流计数键的路径部分超长（>512 字符）时改用 SHA-256 哈希，
  防超长 URL（`MAX_CONTENT_LENGTH` 不管 URL 长度）撑爆缓存键内存
- **多 worker 日志告警**：生产自检新增多 worker 写同一日志文件轮转竞态告警
  （建议 `LOG_FILE_PATH=` 改用容器日志采集），与 Redis/token/TCP 告警风格一致

### 新增

- **TCP / UDP 协议服务器**：零新依赖（stdlib socketserver 线程模型，Windows/Linux 可用），
  在 `flask_server/handler/` 下新建文件用装饰器注册处理器即接收消息（复用 controller
  自动发现模式，建文件即出接口）
  - TCP：每连接一线程，`on_connect` / `on_message` / `on_disconnect` / `on_error` 钩子；
    消息定界支持 `line`（默认，分隔符可配如 `\n`/`\r\n`，自动处理粘包/拆包，纯空帧跳过）、
    `fixed`（固定长度 `TCP_FRAME_LENGTH`）、`head_tail`（帧头帧尾定界，回调负载；
    帧头前垃圾自动丢弃、帧尾缺失时以新帧头重同步、超限断开）与 `raw`（原始 recv 流）；
    单条消息超限（`TCP_MAX_MESSAGE_LENGTH`）视为协议错误断开连接（防内存 DoS）
  - UDP：每数据报一线程，`on_message(data, addr)` 返回 bytes 自动回发到来源地址
    （返回 None 可用 `udp_server.send` 主动发送）
  - 配置默认关闭（`TCP_ENABLED` / `UDP_ENABLED`），三个入口（server.py / wsgi.py /
    wsgi_gunicorn.py）启动时自动拉起，优雅关闭时停止（atexit 兜底）；启用但未注册
    处理器时告警不启动，端口占用直接报错不静默
  - banner 新增 TCP/UDP 状态行；gunicorn 多 worker 部署会端口冲突，启动告警提示
    （`WORKER_NUM=1` 或独立进程部署）
  - 配置解析新增 `_parse_bytes_env`（latin-1 保真的字节串转义解析，`\xaa\x55` 二进制可用）
  - **并发防护**：`TCP_MAX_CONNECTIONS`（默认 256，超限拒绝新连接）/ `UDP_MAX_CONCURRENCY`
    （默认 256，超限丢弃数据报）信号量限流，防线程耗尽 DoS；`≤0` 表示不限制；
    超限告警带冷却窗口（10s 内仅首次告警并累计拒绝计数，冷却结束输出摘要，
    防洪泛场景日志刷屏）
  - **帧硬上限**：line/head_tail 切帧后校验负载长度，分隔符/帧尾与超长帧同包到达时
    同样断开（修复 max 判定存在 recv 块粒度窗口的问题）；head_tail 重同步次数超限
    （256 次）断开连接（防"帧头+垃圾"反复重同步无限持有连接）
  - **stop() 关闭活动连接**：TCP 服务器停止时统一关闭已建立的连接（热重启无残留）；
    连接登记竞态闭合——stop 置 `_server=None` 后到达的 handler 线程在登记时即被
    拒绝并关闭，不会成为 close_all 的漏网之鱼
  - **dev reloader 修复**：`server.py` 在 debug+reloader 模式下仅子进程（真实服务进程）
    启动协议服务器，父进程（监督者）不绑定端口——修复子进程二次绑定 EADDRINUSE
    崩溃循环（应用永远起不来）
  - handler 自动发现导入容错：单个模块导入失败记录 ERROR 后跳过，不中断其他模块
  - **UDP 非 bytes 返回值告警**：`on_message` 返回 `str` 等非 bytes 类型时 WARN 提示
    且不回发（修复前静默忽略，类型错误难排查）
  - **优雅关闭顺序**：wsgi.py 先停止协议服务器再关闭 DB/Redis 连接池
    （修复前先关依赖，处理中的消息可能因 DB 已关而失败）
  - 文档补充：fixed 模式内存与帧长成正比、协议 handler 内 DB 访问需 app context、
    TCP/UDP 默认 0.0.0.0 暴露面、UDP 在途数据报说明
  - **帧解析异常兜底**：TCP 读帧循环捕获意外异常统一进 Logger 后断开
    （修复前 socketserver 把裸 traceback 打到 stderr，绕过统一日志）；
    `frame_head`/`frame_tail` 增加 bytes 类型校验（str 等非 bytes 回退 line，
    修复 `buffer.find(str)` TypeError 断连）
  - **消息上限范围校验**：`TCP_MAX_MESSAGE_LENGTH` / `UDP_MAX_MESSAGE_LENGTH` ≤0
    告警回退默认 65536（修复 max=0 时 line/head_tail 全部帧"超长"断开、
    raw 模式 recv(0) 秒断连接、UDP recvfrom(0) 收到空数据报）
  - **并发槽位前置检查**：TCP/UDP 槽位检查上移到 `verify_request`（spawn 线程前），
    超限连接/数据报被直接拒绝/丢弃且不创建线程——修复洪泛场景下被拒请求仍消耗
    线程创建成本的 CPU 放大（10 万数据报/秒 ≈ 数倍 CPU 开销）；
    `process_request` 线程 spawn 失败时释放槽位（防泄漏）
  - **重同步计数语义修正**：head_tail 重同步计数在成功切帧后重置（"连续未完成帧"
    语义）——修复合法长连接累计 256 次偶发损坏被误断；"帧头+垃圾"攻击永不完成帧，
    上限照常生效
  - **start() 线程启动失败回滚**：serve 线程启动异常时重置状态并关闭 socket
    （修复 is_running 误报 True 与端口泄漏）
  - 测试 26+ 例（行帧拆包/粘包/超长断开/raw 模式/多客户端/多来源回发/异常钩子/生命周期/
    并发上限/stop 关连接/告警冷却）；文档 `docs/protocol-servers.md` +
    `examples/protocol/` 教学样例与客户端

### 修复

- **异常日志携带 traceback**：`Logger.error/warn` 新增 `exc_info` 参数；
  全局 errorhandler 与 `sqlalchemy_trans` 记录完整堆栈（修复前全局 handler 接管异常后
  Flask 不再打印 traceback，线上 500 仅一行消息无法定位）
- **500 错误详情脱敏**：非 development 环境 5xx 响应只回显通用消息，
  内部异常详情（SQL/路径/连接串）仅入日志
- **限流原子化**：固定窗口计数改用原子 INCR（并发下不再绕过阈值）；
  Redis 主存储不可用时回退内存计数（与 auth 模块降级策略一致，单实例限流仍生效）
- **Docker 网关限流误伤**：`get_real_ip` 支持 CIDR 可信代理（如 `172.16.0.0/12`），
  compose 默认信任 Docker 网关网段，修复容器部署下所有客户端共享同一限流桶的误报 429
- **`@json_response` 透传 Response**：视图返回 `Response`（send_file/redirect）时原样返回，
  不再被序列化为对象字典
- **健康检查依赖探测缓存**：非 debug 模式结果短 TTL 缓存（5s），
  探针高频轮询不再每次真实连库/连 Redis；readyz 保持实时
- **测试日志隔离**：新增 `LOG_FILE_PATH` 配置，测试环境默认禁用文件日志
  （修复前 pytest 运行向项目根 `server.log` 追加写入）
- **SQLite 并发改进**：per-thread 连接替代单连接+全局锁（长查询不再阻塞全部操作）；
  初始化脚本改 `executescript` 整体执行（兼容存储过程/字符串内分号）
- `async_run_func` 改为 `asyncio.to_thread` 真正在线程中异步执行（修复前为同步直调）
- `/metrics` 豁免限流（监控抓取不再被误伤 429）
- `/me` 视图去掉重复 token 查询（`login_required` 已写 uid，新增 `AuthService.get_user_by_uid`）
- `obj_to_dict` 对 datetime/date 输出 ISO 8601（修复前为 `str()` 本地格式）
- `wsgi.py` 优雅关闭改用 `os._exit`（避免 SystemExit 在信号处理器中被吞导致进程不退）

### 新增

- `LOG_FILE_PATH` 配置项：自定义日志文件路径；空字符串禁用文件日志
- **限流 IP 级总配额**：`rate:{ip}` 兜底计数，封堵「随机路径绕过路径级配额」的漏洞
  （webui catch-all 使任意唯一路径各有独立计数键，攻击者可拼随机路径绕过原实现）
- `json_response` 支持 Flask 3 元组 `(data, status, headers)`（修复前 3 元组抛 ValueError）
- SQLite 并发写容错：连接级 `busy_timeout` + WAL 模式 + `database is locked` 有限重试
  （修复前 per-thread 连接在高并发写下会抛锁冲突异常；重试前回滚避免重复写入）
- pyproject.toml 补齐 `prometheus-client` 依赖（修复 deps-sync CI job 必然失败的清单不一致，
  `pip install -e .` 依赖残缺）
- `test_subprocess_task_sentinel_to_subprocess_queue` 修复 multiprocessing.Queue
  feeder 线程刷新竞态（带超时轮询，消除 CI/本机偶发失败）
- `SQLite._get_conn` 未配置 `SQLITE_DB_PATH` 时工作线程复用模块级连接（修复注入场景 TypeError）
- CI lint job 覆盖 `wsgi_gunicorn.py`/`scripts/`（与 Makefile 一致）
- README/docs 用例数与覆盖率数字同步（313 用例 / 91.90%）
- **探针端点豁免限流**：`/metrics`、`/api/v1/healthz`、`/api/v1/readyz`、`/api/v1/health`
  不再计数（修复 readyz 被 429 导致编排系统摘除实例 → 流量集中 → 更 429 的雪崩循环）
- `get_real_ip` 跳过 `X-Forwarded-For` 空条目（修复 `, 1.2.3.4` 首项为空时 IP 归一为空串，
  全空时回退 remote_addr）
- **认证并发注册竞态修复**：`SqlAlchemyAuthStore.create` 捕获唯一索引 `IntegrityError`
  映射为重复注册语义（修复先查重后插入的 TOCTOU 竞态在并发下返回 500）
- docker-compose.prod.yml：`REDIS_URL` 带上 `${REDIS_PASSWORD}`（修复设置 Redis 密码后
  app 无法认证连接的不一致）
- 文档：CIDR 可信代理的 XFF 伪造风险、多 worker 日志轮转建议、gunicorn 优雅关闭措辞修正、
  `AUTH_ENABLED` 下 `/metrics` 抓取需 token 说明
- requirements-dev.txt 补 `ruff`（`make lint` 依赖）
- scaffold.py 排除 `.opencode/` 与 `storage/` 下用户数据文件
- `verify_real_env.py` 适配双层限流：阈值调至 100、burst 增至 110
  （修复 IP 级总配额使流程第 11 个请求即 429、防爆破断言必然失败的回归）
- 文档：api-conventions.md 限流章节同步（双层配额/探针豁免/降级语义）、
  deployment.md 补 gunicorn 多 worker 下 `/metrics` 进程级计数说明
- Redis 降级路径去除逐请求重复 warn（冷却期内刷屏；故障开始/恢复仍由 RedisCache 告警）
- scaffold.py 移除未使用的 `STORAGE_EXCLUDE_NAMES` 常量
- **认证拦截放行 CORS 预检**：`auth_interceptor` 对 `OPTIONS` 请求不再要求 token
  （修复 AUTH_ENABLED=true 时跨域前端 preflight 被 401 拦截、所有 API 调用失败的缺陷）；
  api-conventions.md 同步说明
- **异步命令超时**：`async_run_command` 支持 timeout（默认 600s），超时 kill 子进程并走
  on_error 回调（修复挂死命令永久占用有界线程池工作线程）
- `RedisCache.getdel` 解析损坏 JSON 时清除该键（与 get 的自愈行为一致）
- 限流：`OPTIONS` 预检豁免计数；`remote_addr` 为空时用 `unknown` 兜底（防 `rate:None` 键）
- **非 dict JSON body 归一化**：顶层数组/字符串 body 归一为 `{}`（修复视图按字段访问
  payload['x'] 抛 TypeError 导致 500 的问题，统一走 KeyError → 400 参数错误）
- `RedisCache.getdel` 损坏值自愈删除行为补测试断言
- `SimpleMemoryCache.expire(ttl=None)` 直接返回（与 RedisCache 行为一致，修复 TypeError 隐患）
- `sqlalchemy_trans`/`in_app_context` 对 `_app` 未初始化给出清晰 RuntimeError（替代 AttributeError）
- 文档：deployment.md 提示 bind mount `storage/`/`server.log` 在 Linux 宿主的 appuser 写入权限要求
- **路径大小写归一化**：`webui` 的 `/api` 404 保护与 `auth_interceptor` 前缀/豁免匹配改为
  大小写不敏感（修复 `/API/...` 变体绕过 SPA 回退保护与认证前缀匹配，属防御纵深）

## [0.3.2] - 2026-08-08

### 修复

- **雪花 ID 配置兜底**：`SNOWFLAKE_WORKER_ID` 非数字/越界（非 0-31）不再导致启动崩溃，
  告警后回退按 PID 派生
- **认证原子化**：refresh 轮换改 GETDEL（并发复用同一 refresh_token 仅一次成功）、
  防爆破计数改 INCR（多实例一致），消除先查后删竞态
- **登录时序均衡**：用户名不存在时也执行等价 PBKDF2 校验（防用户名枚举时序侧信道）
- **登录入参限长**：username 与注册对齐限 3-64、refresh_token 限 ≤256
  （防超大缓存 key/日志行 DoS）
- `verify_pbkdf2` 防御：盐值非合法 hex（数据损坏）返回 False 而非登录 500；
  iterations 超上限（>100 万）拒绝校验（防 DB 中毒 CPU DoS）
- **认证存储配置校验**：`AUTH_STORE=sqlalchemy` 未配置 `SQLALCHEMY_URI` 时启动即报清晰错误
  （修复前运行期 AttributeError 500）；修正占位模型说明（加载顺序保证真实 ORM 模型，无 reload）
- **缓存计数损坏值防御**：`incr` 遇到非数字值（内存/Redis）按 0 重计或清理，不再抛异常
  （防登录 500）
- **`/api` 精确路径 404**：修复前 `GET /api` 落入 SPA 回退返回 index.html（/api/xxx 才 404）
- `APP_ENV` 未知值（如拼写错误）打印告警并回退 development 预设（修复前静默）
- **Docker 生产默认入口**：镜像默认 `APP_ENV=production` + waitress（`python wsgi.py`），
  开发调试由 docker-compose.yml 显式覆盖（server.py + development）
- **metrics 运行时开关**：`METRICS_ENABLED=false` 时 before/after_request 零开销
- **健康检查尊重 Redis 冷却**：`RedisCache.ping()` 冷却期内快速返回，不真实连接
- 404/405 等 4xx 日志降级 WARNING（不再刷 ERROR）
- 移除 SQLite 未转义字面量拼接死代码 `_parse_value/_parse_values`（统一占位符参数化）；
  `SQLITE_DB_PATH` 目录不存在时自动创建
- CHANGELOG 移除真实云服务器 IP（公开仓库信息脱敏）

### 新增

- `RedisCache`/`SimpleMemoryCache` 原子操作 `getdel`/`incr`（refresh 轮换、防爆破计数专用）
- CI **deps-sync** job：pyproject.toml 与 requirements.txt 依赖清单一致性检查
  （CI 四 → 七流水线）

### 测试

- 用例 283 → **291**，覆盖率 92.98% → **93.36%**（配置校验、损坏值防御、/api 边界、
  redis 未覆盖分支、APP_ENV 告警补测）

## [0.3.1] - 2026-08-08

### 新增

- **真实环境自检脚本** `scripts/verify_real_env.py`：连接探测 → 自动建库 → CI 对齐集成测试 →
  Flask-Migrate 建表 → HTTP 全流程（认证/防爆破/限流/readyz）→ 反射容错 → 真实启动冒烟，
  幂等可复跑，密码自动脱敏
- **CI test-windows job**：Windows + Python 3.12 全量测试（覆盖 Windows 专属路径）
- **认证 token 落 Redis**：配置 `REDIS_URL` 后 access/refresh token 与防爆破计数自动改用
  Redis（多 worker 共享），未配置回退进程内内存

### 修复

- `.env.example` 空值配置导致启动崩溃：`SQLALCHEMY_URI`/`REDIS_URL`/`SQLITE_DB_PATH`
  空字符串归一化为未配置（按文档 `cp .env.example .env` 即可启动）
- `LOG_TO_CONSOLE` 空值不再静默覆盖 APP_ENV 预设档
- **认证存储降级兜底**：Redis 不可达时 token/防爆破计数自动回退内存缓存（登录不再
  "假成功"），Redis 恢复后自动回 Redis（读/删双向兜底）
- 认证豁免路径改整段精确匹配（防 `/docsanything` 前缀绕过）
- 注册/登录密码校验改用 `validate.Length`（兼容 marshmallow 3/4，修复返回 bool 的
  lambda 校验在 marshmallow 4 下静默失效）；登录密码增加长度上限（防 PBKDF2 CPU DoS）
- CSP 收紧：仅 `/docs` 放行 CDN/内联脚本，其余路径 `script-src 'self'`
- `/metrics` 未匹配路由使用固定标签（防高基数）；`METRICS_ENABLED=false` 运行时返回
  503 并告警
- `flask_server` 启动链导入 model：`flask db migrate` 才能识别 UserPO 生成建表迁移
- `scripts/db.py` 改用 `python -m flask`（虚拟环境 PATH 外可用）、migrate 消息整体传参
  （含空格不再拆分）
- examples 文件下载端点路径穿越防护
- tests/conftest 中和本地 `.env`，测试不被真实配置（认证/限流/真实数据库）污染
- benchmark p95/p99 小样本索引越界保护；memory_cache 标注 pickle 安全边界

### 测试

- 用例 224 → **261**，覆盖率 88.68% → **93.88%**（健康检查故障/成功分支、缓存降级
  与自愈、防御分支、配置/边界分支补测）

### 验证

- 真实 MySQL 8.4 + Redis 7.4（云服务器）全链路 20 项通过：迁移建表、
  auth 全流程、token 落 Redis、Redis 限流、防爆破锁定、反射容错、healthz/readyz/health

## [0.3.0] - 2026-08-08

### 新增

- **登录防爆破**：连续失败 `AUTH_LOGIN_MAX_FAILS`（默认 5）次锁定 `AUTH_LOGIN_LOCK_SECONDS`（默认 300）秒，锁定期间正确密码也拒绝（code 4003）
- **Refresh Token 轮换**：登录返回 `{token, refresh_token}`；`POST /api/v1/auth/refresh` 单次使用换新（`AUTH_REFRESH_TOKEN_TTL` 默认 30 天）
- **雪花 ID 多进程隔离**：`SNOWFLAKE_WORKER_ID` 显式配置或按 PID 自动派生（修复 gunicorn 多 worker 生成重复 ID）
- **分布式限流**：`RATE_LIMIT_STORE=redis`（多实例准确，需 REDIS_URL）
- **性能基准脚本** `scripts/benchmark.py`：并发压测，输出 QPS/平均/P50/P95/P99 延迟
- **示例补齐**：SocketIO 事件演示（`examples/socketio_demo.py`）、文件上传/下载/删除端点（`examples/controller/file_controller.py`）、APScheduler 定时任务（`examples/scheduler_demo.py`）
- **发布流程**：`v*` tag 自动创建 GitHub Release（notes 提取自 CHANGELOG）
- **CI docker-build job**：多阶段构建验证 + 容器启动冒烟 + 镜像体积输出
- **启动自检增强**：多 worker 未配 Redis 时 banner 告警（memory_cache 进程内不一致）

### 修复

- 认证 `SqlAlchemyAuthStore` 无 app context 崩溃（新增 `in_app_context` 通用辅助）
- 认证 sqlalchemy 存储补集成测试（含占位模型 reload 语义）

### 测试

- 认证 9 → 18 例（防爆破 3、refresh 轮换 2、sqlalchemy store 4）
- 新增 benchmark 4 例、分布式限流 3 例、雪花 PID 派生 3 例、banner 多 worker 告警 2 例
- 用例总数 184 → **206**，覆盖率 ~87%

### 文档

- docs 补充：benchmark 用法、登录防爆破与 refresh 说明、多进程部署注意事项

## [0.2.0] - 2026-08-08

### 新增

- **认证模块**（默认关闭）：注册/登录/登出 + Token 签发（缓存 TTL 可配）、`@login_required` 装饰器、`AUTH_ENABLED=true` 全局保护（豁免 auth/文档/健康检查）、`AUTH_STORE=memory`（零配置）或 `sqlalchemy`（UserPO 持久化）
- **Prometheus 指标**：`/metrics` 端点（请求计数 + 延迟直方图，路由规则聚合防高基数），`METRICS_ENABLED` 可配，缺库优雅降级
- **gunicorn 生产入口**：`wsgi_gunicorn.py`（多 worker；`SOCKETIO_ENABLED=true` 自动 eventlet worker 支持 WebSocket；Windows 守卫明确报错）
- **覆盖率门槛**：pytest-cov（分支覆盖率阈值 80%）接入本地与 CI
- **pre-commit hooks**：ruff + 空白/文件结尾/合并冲突检查
- **Docker 多阶段构建**：builder 层安装依赖，最终层仅运行时文件（镜像体积显著减小）
- **CI security job**：pip-audit 依赖漏洞扫描（当前零漏洞）

### 测试

- 认证 9 例（注册/重复注册/登录/错误密码/登出/me/全局拦截/装饰器）
- 指标 3 例、gunicorn 配置 3 例
- 用例总数 160 → **175**，行覆盖率 ~85%

### 文档

- README：认证模块、指标、gunicorn 选型表、质量工具命令、配置表
- .env.example：AUTH_*/METRICS_ENABLED

## [0.1.0] - 2026-08-08

### 新增

- **分层架构脚手架**：Controller-Service-Model-Module 四层 + 统一响应（GraceResult）
- **参数校验与 API 文档**：flask-smorest + marshmallow，`/docs` Swagger UI 自动生成
- **基础设施模块**：SQLAlchemy（连接池/事务装饰器/启动反射容错）、SQLite、内存缓存（TTL+后台清理）、Redis 缓存（socket 超时 + 冷却式自动恢复）、本地文件存储（路径穿越防护）
- **工具库**：日志（request_id 链路 + JSON 格式 + 轮转）、加密（PBKDF2 `salt$iterations$hash`）、雪花 ID、随机数、日期时间（含 UTC）、异步任务（有界线程池 + 命令执行 + SafeThread + SubprocessTask 哨兵停止协议）
- **安全加固**：URI 密码脱敏、可信代理 IP（TRUSTED_PROXIES）、安全响应头、限流组件（可配）、请求体/WebSocket 消息大小上限、默认 SECRET_KEY 生产告警
- **可观测性**：request_id 请求头透传 + 响应回写 + JSON 日志字段
- **健康检查**：`/api/v1/health`（详情）、`/healthz`（存活）、`/readyz`（就绪，依赖故障 503）
- **部署**：Dockerfile + docker-compose（dev/prod，app healthcheck 用 readyz）、waitress 生产入口（SIGTERM/SIGINT 优雅关闭）、MySQL charset=utf8mb4
- **工程化**：GitHub Actions CI（Python 3.10/3.12 + 真实 MySQL 集成测试 + ruff lint）、pyproject.toml（支持 `pip install -e .`）、.env 自动加载、CHANGELOG
- **测试**：156 个用例（HTTP 集成、事务提交/回滚、缓存降级恢复、限流、安全回归等）

### 修复

- flask-smorest 版本范围（PyPI 无 1.x，修正为 `>=0.42.0,<1.0`）
- 422 校验错误统一为 GraceResult 格式（保留字段级错误）
- `sqlalchemy_trans` 自动管理 app context（原在 context 外调用崩溃）
- `SubprocessTask.stop()` 永久挂死（哨兵解除阻塞 + join 超时）
- Windows 下 `shlex` 命令拆分吞反斜杠路径
- `LocalFileStorage.exists()` 副作用（不再创建目录）
- SQLite `LIMIT 0` 语义、`_parse_value(None)`
- Redis 每次操作 ping 的多余 RTT（改为零探测直通）
- webui 静态缓存无界增长（OrderedDict LRU + 上限）
- 异步线程池无界队列（BoundedExecutor）
- `import *` 命名空间污染、雪花算法锁、PBKDF2 迭代次数硬编码等历史问题

### 文档

- README（958 行）：快速开始、完整教程、配置表、部署、FAQ、分页/ETag 用法
- PROJECT_EVALUATION.md：四轮评估与修复归档
- examples/：可运行的完整教学样例（用户 CRUD / 文章 / 鉴权拦截器）
