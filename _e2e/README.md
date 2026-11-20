# `_e2e` 真实环境端到端脚本

> **仅手动运行，未纳入 CI（需要真实 MySQL/Redis 与 Docker）。**
>
> 这里的东西不是单元测试的替代品：`tests/` 用 `FakeRedis`、SQLite、测试客户端把绝大多数
> 路径都覆盖了（`make test` 就跑这些）；`_e2e/` 只用**真实进程 + 真实中间件**验证
> "替身和真实实现的差异"——Streams 的 `XAUTOCLAIM`/`XPENDING`、多进程 token 共享、
> TCP/UDP 真实 socket 分帧、docker compose 编排这类东西。
> 所以 CI 不跑它们：GitHub 托管 runner 上要多起 MySQL + Redis + Docker 才能全绿，
> 收益和成本不成正比，而这些语义已经由 CI 的 `test-mysql` / `test-redis` job 覆盖主要部分。

---

## 1. 前置条件

| 条件 | 说明 |
|---|---|
| Python ≥ 3.10 + 依赖 | `pip install -r requirements-e2e.txt`（含运行时 + 开发依赖 + PyYAML） |
| MySQL | 项目根 `.env` 里 `SQLALCHEMY_URI=mysql+pymysql://user:pass@host:3306/db?charset=utf8mb4` |
| Redis | 项目根 `.env` 里 `REDIS_URL=redis://127.0.0.1:6379/0`（脚本会改用 DB 15，不污染业务库） |
| Docker | `e2e_misc.py` 调 `docker compose config` 校验编排，需要 docker CLI + compose 子命令 |
| 端口 | 脚本自己找空闲端口（仅 127.0.0.1），不占用固定端口 |

`.env` 不存在时先复制示例：

```bash
cp .env.example .env      # Windows: copy .env.example .env
```

> 脚本读取的是**进程环境变量优先，其次项目根 `.env`**（与 `flask_server.config` 的优先级一致）。
> 已 `export` 的同名变量不会被 `.env` 覆盖。

---

## 2. 脚本清单

| 脚本 | 覆盖内容 | 需要真实环境 |
|---|---|---|
| `e2e_http.py` | HTTP 基础端点 / OpenAPI / 指标 / 统一响应与错误码 / 安全头 / CORS / WebUI 回退 / 路径穿越 / 认证（登录、锁定、refresh 轮换、登出吊销）/ 限流（memory、redis、降级）/ SQLite 持久化 / MySQL 连通性 / 应用工厂与配置注入 | **MySQL + Redis** |
| `e2e_tasks.py` | 任务队列内存后端语义、worker 生命周期与重试/死信、Redis Streams 后端、`XAUTOCLAIM` 接管、worker CLI（真实子进程）、内嵌 worker、自动发现 | **Redis** |
| `e2e_protocols.py` | TCP 四种定界（line / raw / fixed / head_tail）、粘包拆包、超长断开、并发上限、空闲超时、handler 异常隔离、UDP 回显与截断 | 无（纯 loopback） |
| `e2e_misc.py` | 脚手架生成（生成项目能独立跑测试）、docker compose 编排（prod/dev、worker profile）、CI 流水线 job 集合、文档与配置一致性、开发入口与基准脚本 | **Docker** + PyYAML |
| `probe_logging.py` | 日志探针：JSON 格式行、文件 handler、`request_id` 关联（只打印结果，不做 PASS/FAIL 判定） | 无 |
| `common.py` | 公共库（`Results` 断言计数、`Server` 启动真实 `wsgi.py` 进程、`.env` 解析、Redis 清理、环境检查），**不单独运行** | — |

---

## 3. 运行方式

```bash
# 1) 装依赖（一次性）
pip install -r requirements-e2e.txt

# 2) 确认 .env 里 SQLALCHEMY_URI / REDIS_URL 已配置
# 3) 单个脚本
python _e2e/e2e_protocols.py
python _e2e/e2e_tasks.py
python _e2e/e2e_http.py
python _e2e/e2e_misc.py

# 4) 或一次跑完（先打印前置条件，再依次执行；任一脚本失败即中止）
make e2e
```

| 退出码 | 含义 |
|---|---|
| `0` | 全部检查通过 |
| `1` | 有用例失败（输出里 `FAIL` 行 + 末尾汇总） |
| `2` | 环境没准备好（缺 `SQLALCHEMY_URI` / `REDIS_URL` / `docker` / PyYAML），脚本会打印 `[ERROR] 需要 …` 后退出，不会甩 traceback |

每个脚本启动第一行会打印环境状态，便于判断跑的是哪套环境：

```
[ENV] e2e_http: SQLALCHEMY_URI=已配置, REDIS_URL=已配置
[ENV] e2e_protocols: 无必需的外部服务（纯 loopback 用例，不需要 MySQL/Redis/Docker）
[ERROR] e2e_tasks 需要真实环境，以下变量未配置：    # required 缺失 → 退出码 2
  - REDIS_URL: Redis 连接串（…）
[SKIP] <可选变量> 未配置：<用途>（对应用例会跳过）   # 只有 optional 变量才会打印这一行
```

---

## 4. 副作用与清理

- 日志与临时文件写在 `_e2e/logs/`、`_e2e/tmp/`（`.gitignore` 已忽略）。
- `e2e_http.py` / `e2e_tasks.py` 会**临时**写入 `flask_server/task/e2e_probe.py` 和
  `flask_server/handler/e2e_probe.py`（真实进程要能发现任务/handler），正常结束会在
  `finally` 里删除；如果中途强杀，手工删掉这两个文件即可。
- Redis 只操作 DB 15 里 `e2e:*`、`rate:10.*`、`auth:*` 这些前缀的键，结束时清理。
- MySQL 只做连通性/健康检查读取，不写业务表。
- SQLite 临时库建在 `_e2e/tmp/`，用完删除。

---

## 5. 常见问题

| 现象 | 原因 / 解决 |
|---|---|
| `[ERROR] e2e_http 需要真实环境…SQLALCHEMY_URI` | `.env` 没配或值被清空；填 MySQL 连接串后重跑 |
| `[ERROR] e2e_misc 需要命令 docker` | 没装 docker 或不在 PATH；或只跑其它脚本 |
| `ModuleNotFoundError: No module named 'yaml'` | `pip install -r requirements-e2e.txt` |
| `Server xxx failed to start` + 日志尾部 | 该脚本启动的 `wsgi.py` 进程起不来，看输出里的日志尾部（多为 `.env` 配置错误/端口占用） |
| 用例数与 `make test` 不一致 | 正常：`_e2e` 是独立脚本，不走 pytest，也不进覆盖率统计 |

与 CI 的关系、发布流程、代码风格等见 [开发与 FAQ](../docs/development.md)。
