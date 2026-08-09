# Changelog

本模板项目的变更记录。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

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
- README/docs 用例数与覆盖率数字同步（309 用例 / 91.90%）
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
