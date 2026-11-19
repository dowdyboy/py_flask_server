# 代码 / 文档 / 注释 审计报告

> ## ✅ 修复状态（2026-11-18 更新）
>
> 本报告所列问题**已按下文"建议修复顺序"完成修复**。当前基线：
> **628 个用例全部通过**（1 skipped：可选依赖 Flask-SocketIO 未安装）、
> **覆盖率 91.11%**（门槛 85%）、`ruff check .` 全仓库零告警（此前 `_e2e/` 15 处、
> `deployments/` 3 处）。
>
> | 类别 | 状态 |
> |---|---|
> | P0 五条（head_tail 定界、日志黑洞、退出码 0、存储根删除、删迁移） | ✅ 全部修复，其中 4 条有回归测试且已实测验证 |
> | P1 九条（gunicorn worker class、DB 池 dispose、占位 UserPO、PBKDF2 上限、入口数值校验、内嵌 worker 拖死启动、任务队列原子化、冷却期撤销、枚举配置校验） | ✅ 全部修复 |
> | P2 注释/文档漂移（30 余处） | ✅ 全部修复；另修正了 `examples/model/user_declared.py`、`_e2e/e2e_misc.py` 的过期断言 |
> | P3 一致性与卫生 | ✅ 主要项已修（`stats()`/`dead_letters()` 双后端对齐、统一 Logger、`.gitignore`/`.dockerignore`、`make lint` 范围、scaffold 排除、compose `init`/grace、CI 矩阵与锁版本、`deployments/` 二进制入 ignore） |
>
> **仍有意保留 / 未做**（不影响上述结论）：
> - `parse_request_json` 仍在 `before_request` 阶段对非法 JSON 抛裸 400（不带字段级 `messages`），
>   因此"4xx → code=1001"只在 webargs 自己抛错的路径可达；文档已按**条件式**描述，未虚构。
>   彻底统一需把该钩子改成 `get_json(silent=True)`，属行为变更，留待确认后单独做。
> - `_promote_delayed` 的 `ZREM → XADD` 跨进程崩溃窗口仍在（连接错误已改为抛
>   `TaskQueueUnavailable`，窗口已收窄）；要彻底消除需 Lua/WATCH。
> - `verify_real_env.py` 在 `user` 表已存在时跳过迁移，仍不校验 revision/列结构（可能掩盖 schema 漂移）。
> - `_e2e/` 仍未纳入 CI（需真实 MySQL/Redis 与未声明的 PyYAML）；`release.yml` 无测试门禁。
> - 基础镜像未固定 digest（`python:3.12-slim` / `mysql:8.0` / `redis:7-alpine`）——离线环境无法取得 digest。
> - 测试层的 `sleep` 竞态、弱断言、`test_logger`/`test_wsgi` 的全局状态污染未逐一整改
>   （本轮只补了回归测试，未重构既有用例）。
> - `deployments/armv7` 的 install.sh 自检退出码、SHA256 校验、`--prefix` 失效三项未改。

- 仓库：`py_flask_server`（审计时 v0.5.0，HEAD `fb7ad20`；修复后随 **v0.6.0** 发布）
- 日期：2026-11-18（修复状态于 2026-11-19 更新）
- 范围：`flask_server/**`（全部）、三个入口、`tests/`、`_e2e/`、`scripts/`、`examples/`、`deployments/`、
  `docs/` + `README.md` + `CHANGELOG.md` + 两份历史报告、`Dockerfile` / compose / CI / 依赖锁
- 方式：只读静态审查 + **可执行验证**（独立 venv 装了 `requirements.txt + -r requirements-dev.txt`，
  跑通测试、ruff、覆盖率，并用脚本复现了下文标注「已实测」的每一条）
- 未改动任何源码/配置；临时探针脚本与审计 venv 已全部删除，`git status` 已复原

---

## 0. 基线事实（先给结论，避免误判项目质量）

| 项 | 实测 | 说明 |
|---|---|---|
| 测试 | **582 个用例，581 passed / 1 skipped，0 failed** | README「582 个用例」属实 |
| 覆盖率 | **91.04%**（门槛 85%） | README/docs 写 91.3%、E2E 报告写 91.27%，小幅漂移 |
| lint | `make lint` **全绿**；但 `ruff check .` 有 **18 处**（`_e2e/` 15 + `deployments/` 3） | lint 目标未覆盖这两个目录 |
| 环境变量 | `config.py` 读取 **79 个键**；`.env.example` 与 `docs/configuration.md` **零缺失、零多余、无错误默认值** | 文档在这块的准确度罕见地高 |
| 依赖一致 | `requirements.txt` ↔ `pyproject.toml` 13 项完全一致；`constraints.txt` 全覆盖 | — |
| 磁检 | `.env` 已 gitignore、已进 `.dockerignore`、已被 scaffold 排除 | 处理正确 |

**总体判断**：这是一个成熟度明显高于一般脚手架的仓库——分层清晰、异常与安全头处理扎实、
文档与环境变量表的对齐程度很高。下面的问题集中在三类：**少数会在生产静默失效的缺陷**、
**注释先于实现/实现变更后注释未跟上的漂移**、**跨后端/跨入口的语义分裂**。

---

## P0 — 会静默损坏数据或让生产「看起来正常」

### P0-1 `head_tail` 定界依赖 TCP 分包，同一字节流产生不同帧

`flask_server/module/tcp_server.py:284-336`

`_read_head_tail_frame` 在缓冲里发现「帧头之后又出现帧头」时，会丢弃到更靠后的帧头「重同步」。
这个重同步**作用于已收到的全部缓冲内容**，与 TCP 如何分包无关地执行，于是同一段字节流：

| 投递方式 | 解析结果 |
|---|---|
| 一次投递 `AA55 61 AA55 62 BB` | `b'a\xaaUb'`（发送方的本意） |
| 切成两块投递 | `b'b'`（`a<HEAD>` 被静默丢弃） |
| 切成三块投递 | `b'b'` |

**已实测**。后果：只要负载合法地包含帧头字节，帧内容就取决于网络分包——本地/小包测试永远正常，
线上大概率丢字段，且没有任何日志。`docs/protocol-servers.md` 与单测把这个「重同步」当作特性写进了契约，
`_e2e/e2e_protocols.py` 与 `tests/test_tcp_server.py` 甚至分别固化了两种不同结果。

**修复**：先定死协议语义。无转义协议应当「首个帧头 + 其后首个帧尾」定界；
「重同步」只能在一个已确认损坏的帧之后执行，不能对未完成帧内的合法内容生效。
定好后同时改写单测（同包/拆包两种喂法必须同结果）、E2E 与文档。

### P0-2 生产环境默认「日志黑洞」：INFO 全部丢弃，没有一处 sink

`flask_server/util/logger.py:94-117` + `flask_server/config.py:62-64,186-189` + `docker-compose.prod.yml:18-29,44`

- `staging`/`production` 预设 `log_to_console=False`（`config.py:63-64`）
- `LOG_FILE_PATH` 未设置 → `log_to_file=False`（`config.py:187`）
- `Logger.init()` 两者皆 False 时 **一个 handler 都不加**，且 `propagate=False`（`logger.py:89`）

**已实测**：`APP_ENV=production`、未设 `LOG_FILE_PATH` 时 `logging.getLogger('flask_server').handlers == []`，
`Logger.info(...)` 输出为空，只有 WARNING+ 经 `logging.lastResort` 打到 stderr（无格式、无 request_id、不进文件）。

而 `docker-compose.prod.yml` 恰好就是这个组合：`app` 服务没有 `LOG_FILE_PATH`，却把
`./server.log:/app/server.log` 挂进容器（第 44 行）——这个文件永远是空的；`worker` 服务更显式设了
`LOG_FILE_PATH: ""`（第 62 行），连 WARNING 都只剩 lastResort。

**修复**：在 `Config` 里保证至少一个 sink——两者都为 False 时强制 `log_to_console=True` 并打印
`[Config WARNING]`；或删掉 compose 里那个误导性的挂载。同步修正 `config.py:185` 的注释
「设为空字符串则禁用文件日志（**仅控制台**）」——控制台由 `LOG_TO_CONSOLE` 决定，不会自动补上。

### P0-3 `wsgi.py` 把服务循环崩溃伪装成「优雅关闭」（exit 0）

`wsgi.py:159-163` + `74-82`

```python
try:
    server.run()
finally:
    _graceful_exit('Graceful shutdown complete')   # 内部 os._exit(0)
```

`server.run()` 里任何未预期异常都会走 `finally` → 清理 → `os._exit(0)`：**退出码 0、日志写「优雅关闭」、
没有 traceback**。systemd `Restart=on-failure` / K8s 重启策略不会重启，排障时还会被日志误导。

**修复**：`except BaseException: Logger.error(..., exc_info=True); os._exit(1)`，
`else: _graceful_exit(...)`。

### P0-4 `LocalFileStorage.delete('.')` 会删空整个存储根

`flask_server/module/local_file_storage.py:15-26` + `examples/controller/file_controller.py:57-68`

`_gen_final_path` 的校验是 `not startswith(root + sep) and final_path != root_real` ——
**根目录自身被显式放行**。于是 `path='.'` 归一成 `root_real`，通过校验，`delete()` 的
`os.path.isdir()` 为真 → `shutil.rmtree(root)`。

**已实测**：`delete('.')` 不抛异常，存储根连同内容一起消失。
`examples/controller/file_controller.py` 把 `request.payload['path']` 直接传给 `delete()`，
文件头还写着「接入工程：将本文件放入 flask_server/controller/」，一旦照做就是一个
「POST 一个 `{"path": "."}` 清空 storage」的远程端点。`copy`/`move` 同理。

**修复**：变更类操作（save/copy/move/delete）增加
`if final_path == root_real: raise ValueError('refusing to operate on storage root')`。

### P0-5 `scripts/verify_real_env.py` 删除真实项目的 `migrations/`

`scripts/verify_real_env.py:162-164,181-189`

`step_migrate` 在 `migrations/` 已存在时直接 `shutil.rmtree`，并在 `finally` 里删除
`migrations/` 与 `alembic.ini`（除非 `--keep-migrations`）。而 `make verify` 是 README 第 73 行列出的命令。
在一个已经用 `flask db migrate` 积累了迁移历史的真实项目上跑一次，**迁移历史就没了**。

**修复**：改到临时目录执行，或检测到已有 `migrations/` 就拒绝运行并要求显式 `--force`。

---

## P1 — 在真实部署/配置下会失败

| # | 位置 | 问题 | 判定 |
|---|---|---|---|
| P1-1 | `wsgi_gunicorn.py:33-37` | `_worker_class()` 只看 `SOCKETIO_ENABLED` 就返回 `eventlet`，忽略 `SOCKETIO_ASYNC_MODE` 与 `app.py:9-14` 的降级。`.env.example` 的默认组合（enabled + threading）也走这条分支；eventlet 未安装（`requirements.txt` 里是注释）时 gunicorn 加载 worker class 直接失败，`app.py` 的降级逻辑被完全绕过 | 代码矛盾确定 |
| P1-2 | `wsgi.py:49-56` | `db.engine.dispose()` 在无 app context 下调用 → **已实测**抛 `RuntimeError: Working outside of application context`，被 `except Exception` 吞掉，只打一条 `SQLAlchemy dispose error`。**连接池实际从未 dispose**，与函数 docstring「释放 DB 连接池」不符 | 已实测 |
| P1-3 | `component/auth.py:176-181` + `model/po/user.py:8-20` | `_get_po()` 注释称「取最新定义的 UserPO」，但 `importlib.import_module` 返回缓存模块。进程默认无 `SQLALCHEMY_URI` 时 `UserPO` 已是**占位裸类**（无 `.query`）；此后 `create_app(framework_config=Config({'sqlalchemy_uri':..., 'auth_store':'sqlalchemy'}))` 仍是占位类 → 注册/登录 500。`tests/test_auth.py:419` 必须 `reload(user_module)` 才能拿到真实模型，正好证明注释不成立 | 代码确定 |
| P1-4 | `config.py:272-276` vs `util/data_encrypt_util.py:94-96` | `AUTH_PBKDF2_ITERATIONS` 只校验下限（≥10000），校验侧硬拒 `iterations > 1_000_000`。设成 2000000 时配置不告警、注册成功，但此后**所有登录恒 401** | 代码确定 |
| P1-5 | `config.py:83-84,175` | `THREAD_NUM=0/负` → 导入 `flask_server.util` 时 `ThreadPoolExecutor(0)` 抛 `ValueError`，**应用无法导入**（错误信息与配置项无关）；`MAX_CONTENT_LENGTH=0` → 所有带 body 请求 413。同文件十余项数值配置都有「≤0 告警回退」，这三项漏了 | 代码确定 |
| P1-6 | `module/__init__.py:58-78` + `wsgi.py:152`/`server.py:31` | `TASK_WORKER_EMBEDDED=true` + 有一项任务 + Redis 暂时不可达 → `TaskQueueUnavailable` 从入口逃逸，**Web 进程起不来**（容器重启循环）。与缓存「失败降级」策略不一致 | 已实测 |
| P1-7 | `module/task_queue.py:455-499` | `ack`/`retry`/`dead` 都是「先 `XACK`+`XDEL`，再 `ZADD`/`LPUSH`」。中间失败或进程被 kill → 任务既不在 stream、不在 delayed、也不在死信，**永久丢失**；日志仍会打「retry in Xs」。模块头宣称 at-least-once，此处实为 at-most-once | 代码确定 |
| P1-8 | `module/redis_cache.py:99-107` + `component/auth.py:126-130` | 冷却期内 `delete()` 是 `if not self._check(): return` 的静默 no-op。Redis 抖动时发起 logout/refresh 吊销 → 撤销被跳过；**Redis 恢复后旧 token 仍然有效**（TTL 7/30 天） | 代码确定 |
| P1-9 | `config.py:89,238,253` | `AUTH_STORE` / `RATE_LIMIT_STORE` / `SOCKETIO_ASYNC_MODE` 无白名单校验：拼错或带尾空格会**静默退化**为 memory（重启丢用户、多 worker 不一致、多实例限流失效），且无告警。与 `tcp_framing`（`config.py:98-102`）的「告警回退」风格不一致 | 代码确定 |

---

## P2 — 注释 / 文档与代码不符（本次审计的重点）

> 这一节几乎全是「注释说得比代码做得多」或「代码改了注释没跟」。项目文档与环境变量表本身极准，
> 问题集中在**行内注释**与**少量教程/部署段落**。

### P2-A 行内注释与实现相反

| 位置 | 注释原文 | 实际 |
|---|---|---|
| `component/__init__.py:7-9` | 「认证在指标之前（被 401 拒绝的请求也计入指标计时起点）」 | 注册序是 `限流 → 认证 → 指标`，而 Flask 的 before_request 在任一钩子返回非 None 时**短路**。429/401 时 `metrics_start_timer` 从未执行 → `start is None` → **只计数不计时**。理由与事实相反 |
| `component/metrics.py:12-13` | 「未安装 prometheus-client 时优雅降级（**/metrics 返回 503** 并告警一次）」 | `init_metrics` 在 `_METRICS_AVAILABLE=False` 时直接 `return`，路由不注册 → **404**；`metrics()` 里 `not _METRICS_AVAILABLE` 分支是死代码。banner 仍打印 `METRICS ON` |
| `component/__init__.py:11` | 「拦截器示例（**纯注释，无副作用**）」 | `interceptor.py:8` 有 `Logger.info('component interceptor loaded')` 导入期副作用；该文件 10-12 行全是注释，模块无任何功能 |
| `handler/__init__.py:37` | `from flask_server.module import tcp_server, udp_server  # noqa: F401 确保单例可用` | 本模块未使用这两个名字，注释声称的作用不成立 |
| `config.py:185` | 「设为空字符串则禁用文件日志（**仅控制台**）」 | 控制台由 `LOG_TO_CONSOLE` 独立决定，不会因为禁用文件日志而自动补上（见 P0-2） |
| `config.py:203-213` | 注释强调「避免按分号拆分破坏存储过程/字符串内的分号」 | 紧接着 `db_init_sql_list` 就是按分号拆分，且**生产代码无任何消费者**（仅测试引用） |
| `config.py:61-65` | `_ENV_PRESETS[*]['log_level']`（`docs/configuration.md:8-14` 还专门做了张「预设日志级别」表） | `log_level` 只由 `debug` 派生（`config.py:190`），**preset 里的值从未被读取**。`APP_ENV=production + DEBUG=true` 时日志级别会变成 DEBUG，与表格不符 |
| `model/po/user.py:6` | 「配置后**自动切换**为真实 ORM 模型」 | 只有在 `db` 就绪**之前**导入才成立；见 P1-3 |
| `module/sqlalchemy.py:25-26` | 「二次调用不得把它重置为 None，否则第二次创建的应用会丢掉 db」 | 反过来也成立：第二个 app 根本没被 `init_app`，**已实测**其 `/api/v1/health` 返回 `db:"error"` 并在每次探针打一条完整 traceback |
| `module/task_queue.py:348-353` | 「默认前缀 `tasks`」 | 默认 stream 是 `tasks:default`，key 是 `tasks:default:task:{id}` |
| `module/redis_cache.py:148-153` | 「损坏/非数字值：视为计数丢失，按 0 重计」 | 真实 Redis 上 `INCR` 非整数值抛 `ResponseError`，会先被 `except Exception` 捕获并**整体降级 30s**，这个分支只在假客户端下可达 |
| `util/banner.py:50-59` | docstring「**非生产环境**返回空列表」 | 只对 `'development'` 返回空；`staging`（已在预设里）会输出全部生产告警（**已实测** 3 条） |
| `util/async_task_util.py:95,143` | `on_error`「接收参数 `(extra_param, returncode, stderr)`」 | 实际传 `(extra_param, [returncode, stderr])` —— 一个列表 |
| `util/async_task_util.py:44` | `Raises: RuntimeError: 如果异步函数执行失败` | 异常原样透出，不包 RuntimeError |
| `util/async_task_util.py:23-46` | `do_run_func` 把 `func` 说成 coroutine | 实际是普通可调用对象（`asyncio.to_thread(func)`） |
| `util/async_task_util.py:116` | `Logger.warn(f'... limit={self._max_pending}')` | 真实上限是 `max_workers + max_pending`（第 111 行），排障会误判 |
| `module/tcp_server.py:33-35` | 「**单连接**最大重同步次数」 | 实现是「**连续**重同步，成功切帧即重置」（`:317-320`），语义不同 |
| `component/auth.py:327-332` | login docstring「成功返回 `(access, refresh)`，失败返回 `None`」 | 实际是 `((access, refresh), None)` / `(None, 'locked')` / `(None, 'invalid')` |
| `wsgi_gunicorn.py:49-56` | 「只在 **0 号 worker** 打一条告警」 | `getattr(worker,'age',1) == 0` 永不成立（gunicorn 首个 worker `age=1`），告警永不打印；测试用 `age=0` 把这个错误假设固化了 |
| `component/auth.py:437` / `wsgi.py:91` / `webui_controller.py:55` / `task_queue.py:565` | 一批「修复前……」「历史缺陷」叙述 | 内容本身自洽，但属于 issue tracker 式的历史包袱，建议收敛成「为什么这样写」 |

### P2-B 文档与代码/彼此不符

| 位置 | 文档原文 | 实际 |
|---|---|---|
| `docs/getting-started.md:42` | 「使用 SQLite（最简单）：`SQLITE_DB_PATH=storage/app.db`」，紧接着第 2 步 `class UserPO(db.Model)`、第 3 步 `flask db migrate` | `SQLITE_DB_PATH` 只喂 `module/sqlite.py` 的裸 sqlite3 封装；`sqlalchemy_uri` 仍为 None → `db=None` → `db.Model` 抛 AttributeError、Flask-Migrate 未注册。**照教程做必然卡在第 2 步**。应改为 `SQLALCHEMY_URI=sqlite:///storage/app.db` |
| `docs/getting-started.md:65` + `examples/model/user_declared.py:15` | PBKDF2 存储格式「约 **168** 字符」 | **已实测 104 字符**（32 hex 盐 + `$` + 6 位迭代数 + `$` + 64 hex = 104） |
| `docs/api-conventions.md:113-130` | `class UserPage(Page): items = ma.fields.List(...)` + `@blp.response(200, UserPage)` | **已实测** `flask_smorest.Page` 不是 Schema，是分页器：`Page.__init__(self, collection, page_params)`。这个示例注册即报错 |
| `docs/deployment.md:34` | 「每个 worker 都向 `LOG_FILE_PATH`（**默认 `server.log`**）写入」 | 未设置 `LOG_FILE_PATH` 时根本不写文件（见 P0-2） |
| `docs/deployment.md:95` | `docker run -p 5000:5000 --env-file .env flask-server` | `.env.example` 的 `SERVER_HOST=127.0.0.1` → 容器内只绑 loopback，宿主 `-p` 映射不可达；`--env-file` 还会把镜像的 `APP_ENV=production` 覆盖成 `development` + `DEBUG=true` |
| `docs/configuration.md:43` vs `docs/protocol-servers.md:152` | 前者「超过大小的数据报会被操作系统**静默截断**」 | 后者与 `udp_server.py:16-17` 都是「Linux 截断 / **Windows 丢弃**（WSAEMSGSIZE，handler 不回调）」。两份文档互斥，前者漏了 Windows |
| `docs/configuration.md:141` | 「文件存储于 `storage/` 目录」（日志段末） | 日志默认是**项目根 `server.log`**（`config.py:188-189`）；`storage/` 是上传文件根 |
| `docs/architecture.md:70` | `model/po/`「新文件是否自动生效：**✅**」 | `model/po/__init__.py` 是手写 `from .user import UserPO`，无 pkgutil 扫描；`getting-started.md:70-71` 自己也说「需在 `po/__init__.py` 中导出」。两份文档互斥 |
| `docs/architecture.md:278-279` | `SOCKETIO_ENABLED` / `SOCKETIO_ASYNC_MODE`「必须在 import 前由环境变量确定，**无法按 app 注入**」 | `app.py:282-298` 已按 `cfg.socketio_enabled` 初始化，仅 eventlet monkey patch 是导入期；`test_config_injection.py` 有用例、CHANGELOG 记录了这次修复。文档没跟上 |
| `docs/protocol-servers.md:157-159` | 「多 worker 会重复绑定同一端口导致冲突」 | `wsgi_gunicorn.py:52-60` 在 `worker_num > 1` 时直接跳过启动，不会冲突。`docs/deployment.md:41-44` 说法是对的，两份文档互斥 |
| `docs/api-conventions.md:25` | `4002` → 「HTTP 401」 | `auth_controller.py:53` 还有 `4002 + 503`（会话不可用） |
| `docs/architecture.md:280` | 「`LOG_LEVEL` 等不随 app 变化」 | 全仓库没有 `LOG_LEVEL` 这个变量 |
| `README.md:59` | 「实测 **91.3%**」 | 实测 91.04%（门槛 85% 无事） |
| `README.md:16` | 「CI **九流水线**」 | `.github/workflows/` 是 **2 个 workflow**（`ci.yml` 9 个 job + `release.yml` 1 个 job）。`docs/development.md` 与 `_e2e/e2e_misc.py:131,193` 把这个术语固化成了断言 |
| `README.md:9` | 「四种定界、**自动回发**」 | UDP 自动回发；TCP 由处理器自己 `conn.sendall` |
| `CHANGELOG.md:69,86` | 测试计数「50 例」「11 例」 | 实际 59 例、14 例 |
| `E2E_TEST_REPORT.md:414` | 声称 scaffold 已排除 `.coverage.*` | `scripts/scaffold.py:24` 只精确排除 `.coverage`，没有通配 |
| `PROJECT_EVALUATION.md` 全文 | 「Python 3.8+」「README 851 行」「6 个测试文件 25+ 用例」「API 限流未实现」「无内置认证」「约 20 个环境变量」「缺少 pyproject.toml」…… | 全部过期：README 现 99 行、582 用例、限流/认证/迁移脚本/`pyproject.toml` 都已具备。建议加一行「2026-07 冻结快照，不代表当前代码」的横幅 |

---

## P3 — 一致性与卫生（不逐条展开）

**同一 API 的两个后端语义分裂**（`module/task_queue.py`）
- `stats()`：内存返回 `{ready, delayed, dead, tracked}`，Redis 返回 `{ready, pending, delayed, dead}` —— 键集合不同
- `dead_letters()`：内存「最旧→最新」，Redis「最新→最旧」；`docs/task-queue.md:134` 的重放示例
  `dead_letters(limit=1)[0]` 在两个后端拿到的是相反的一端
- 日志绕过统一 `Logger`：`task_queue.py:93,463,476,499` 用 `logging.getLogger('flask_server')` 直连，
  在 text 格式下丢掉 `[rid:...]` 前缀

**示例与脚本**
- `examples/controller/file_controller.py:41-54`：`download_file` 缺 `@json_response`，**已实测**两个错误分支
  都返回 **500**（`The view function did not return a valid response ... GraceResult`）而非 400/404
- `examples/protocol/tcp_echo_handler.py`：文档给的 `python examples/protocol/tcp_echo_handler.py` 会因
  `sys.path[0]` 是 `examples/protocol` 而 `import flask_server` 失败
- `scripts/worker.py:65`：`_build_queue()` 在 try 之外，Redis 不可达时裸 traceback 退出
- `scripts/scaffold.py:21-26`：按文件系统复制而非 `git ls-files`，会把 `deployments/`（**50 MB**）、
  `.coverage.*`、根目录探针脚本一起复制；`EXCLUDE_FILES` 里有 `PROJECT_EVALUATION.md` 却没有同类的
  `E2E_TEST_REPORT.md`

**仓库卫生**
- `deployments/` **整目录未跟踪**（约 50 MB），无 `.gitignore` 规则，一次 `git add -A` 就会进库
- `.stignore` 已删除但未提交（`git status` 里的 ` D .stignore`）
- `.gitignore` 只忽略 `.coverage`，不忽略 `.coverage.*`
- 根目录有 451 KB `server.log`（虽已 ignore，但含远端 MySQL 主机/库名，密码已脱敏）
- `_e2e/` 6 个文件已跟踪，但**不在任何 CI 中运行**，需要真实 MySQL/Redis/Docker 与未声明的 `PyYAML`；
  `_e2e/` 也不在 `make lint` 范围内（15 处 ruff 问题）

**部署 / CI**
- `docker-compose.yml:36` 的 `stop_grace_period: 10s` < `wsgi.py` 的 `GRACEFUL_TIMEOUT = 15s`，
  Docker 会先 SIGKILL，优雅关闭失效
- 所有服务缺 `init: true`（waitress 作为 PID 1 不回收僵尸）
- 基础镜像 `python:3.12-slim` / `mysql:8.0` / `redis:7-alpine` 未固定 digest
- `.dockerignore` 漏 `.coverage*`、`test-results.xml`、`deployments/`、`_e2e/`、`.ruff_cache/`
- CI 没有 Redis service，所有 Redis 语义只靠 `FakeRedis`；`test-mysql` 只跑合成 `ItemPO`，不碰
  `UserPO`/`AuthService`；`ruff`/`pip-audit`/`gunicorn` 未锁版本；`release.yml` 无测试门禁
- `test_gunicorn.py` 的 `age=0` 固化了 `wsgi_gunicorn.py` 的错误假设

**测试质量**
- 污染：`test_logger.py:89-95` 每个用例删掉 `flask_server` 的**所有** handler 且不恢复；
  `test_wsgi.py:50-54` 真实调用 `_cleanup_resources()`，把全局 `AsyncTaskUtil.executor` 永久 shutdown
- 顺序耦合：`test_app.py:565` 的 `test_readyz_no_dependencies` 依赖全局 `db is None`，靠文件名字母序才通过
- `sleep` 代替同步：`test_udp_server.py:236-243`（并发上限）、`test_auth.py:245`（锁定 TTL）最易在 CI 抖动
- 弱断言：`test_metrics.py:91-99` 的 `assert list(registry.collect()) is not None` 恒真；
  `test_metrics.py:17-22` 名字说「不把原始路径当标签」却只断言输出里有指标名

**小问题**
- `component/metrics.py:98`：`mimetype=CONTENT_TYPE_LATEST` → **已实测** Content-Type 为
  `text/plain; version=1.0.0; charset=utf-8; charset=utf-8`（charset 重复）。应改用 `content_type=`
- `hello_controller.py:70-118`：DB 健康检查失败时 `Logger.warn(..., exc_info=True)`，DB 挂掉期间
  每个探针周期都打一条完整 traceback（我复现多实例场景时刷了一屏）
- `hello_controller.py:121-151`：readyz 内联复制了 `_live_db_check/_live_redis_check`，且失败分支**一行日志都不记**
- `webui_controller.py:87`：`os.path.exists` 在查缓存之前无条件执行，注释承诺的「减少磁盘 IO」基本没生效
- `auth_controller.py:104-106`：`uid is None` 分支不可达（`@login_required` 已保证）
- `config.py:220` / `module/sqlalchemy.py:35`：`S3:`、`S4:` 之类的失效工单标记
- 所有 controller 只有 `@blp.response(200, ...)`，无 `@blp.alt_response` → `/openapi.json` 只声明 200，
  而实际会返回 400/401/429/503
- `app.py:372-373`：`app_config` 在全部装配之后才合并，对 `Api`/DB/SocketIO/组件开关不生效
- `config.py:12-21`：`_parse_int` 用 `print()` 告警，而其它地方都走 `Logger`（此时日志系统尚未初始化，可接受，但风格不统一）

---

## 安全侧的两点提醒（非缺陷，但建议处理）

1. **`.env` 内有指向公网主机的真实凭据**（MySQL root 口令、Redis 口令、64 字符 `SECRET_KEY`）。
   已正确处理：被 `.gitignore`、`.dockerignore`、`scaffold.py` 三处排除，未进 Git、未进镜像。
   但仍是本地明文，且 `scripts/verify_real_env.py` 会拿它去连真实库。建议轮换，并改用本地实例或 secrets。
2. `TRUSTED_PROXIES` 支持 CIDR，`docker-compose.prod.yml:29` 信任整个 `172.16.0.0/12`。
   compose 注释已经承认这一点，`docs/configuration.md:72` 也写了警告——属于「知情且有文档」的取舍，
   但生产上建议缩到具体网关 IP。

---

## 建议修复顺序

1. **P0-2 / P0-3 / P0-5**（日志黑洞、退出码、删迁移）—— 改十几行即可，收益最大。
2. **P0-1**（head_tail 定界）—— 先定协议语义，再改实现 + 单测 + E2E + 文档，一次做对。
3. **P0-4**（存储根保护）+ P1-3（占位 UserPO）—— 两处「放行根/缓存模块」的边界问题。
4. **P1-1 / P1-5 / P1-6 / P1-9**（入口与配置校验）—— 把 `config.py` 里已有的「告警回退」惯例补齐到漏掉的那几项。
5. **P1-7 / P1-8**（Redis 多命令原子化、撤销不能被降级吞掉）—— 用 pipeline/Lua 一次改完。
6. **P2 批量修注释**：`component/`、`config.py`、`util/async_task_util.py`、`banner.py`、`tcp_server.py` 的
   行内注释 + `getting-started.md` / `api-conventions.md` / `deployment.md` 的三处教程级错误。
7. **收尾**：`deployments/` 的 Git 策略、`.stignore`、`.gitignore` 补 `.coverage.*`、`make lint` 纳入
   `scripts/` 与 `_e2e/`、`scripts/scaffold.py` 改按 `git ls-files` 复制、`PROJECT_EVALUATION.md` 加归档横幅。

---

## 附：本次未能验证的项

- Docker / gunicorn 多 worker / 真实 MySQL / 真实 Redis 的行为未实跑（本机 Docker daemon 未启动，
  Windows 不支持 gunicorn）；相关结论为源码推断，已在文中标注。
- `deployments/armv7` 的 ARMv7 运行时未在真机验证（仅做了 SHA256 与静态核对，28/28 匹配）。
- `head_tail` 的「正确语义」取决于协议设计意图，本报告只证明「当前实现不确定」，具体取舍需项目方确认。
