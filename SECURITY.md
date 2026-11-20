# 安全策略

本项目是 Flask 服务端脚手架模板，常被直接用于内网/生产部署。安全问题的反馈请走**私密渠道**，
不要开公开 Issue。

## 支持的版本

按语义化版本发布，只对下面这些版本提供安全修复：

| 版本 | 支持状态 |
|---|---|
| `0.6.x`（当前 `main`） | ✅ 支持 |
| `0.5.x` 及更早 | ❌ 不再维护，请先升级到 `0.6.x` |

依赖层面的已知漏洞由上游修复，本项目通过在 `requirements.txt` / `constraints.txt` 中提升版本下界跟进；
CI 的 `security` job 用 `pip-audit -r requirements.txt` 持续检查。

## 报告漏洞

**不要用公开 Issue / PR / Discussion 报告安全问题**——公开的复现步骤会直接变成攻击手册。

| 渠道 | 说明 |
|---|---|
| **GitHub 私密安全公告**（首选） | 仓库 → `Security` → `Advisories` → `Report a vulnerability`。只有维护者可见，修复后可一键发布公告。 |
| 邮件 | `security@example.com`（**占位地址**，正式部署前请替换为真实安全邮箱）。标题加 `[SECURITY]` 前缀；敏感内容建议先用 PGP 加密或邮件里约定密钥。 |

报告里请尽量包含：

- 受影响的版本 / 提交号、部署方式（Docker、waitress、gunicorn、`deployments/armv7` 板端包等）；
- 最小复现步骤或 PoC（可参考 `tests/` 的写法，但**不要**使用生产环境的真实凭据与真实数据）；
- 影响评估：能读到什么、能改什么、是否需要认证、默认配置是否受影响；
- 可选：修复建议。

响应节奏：**3 个工作日内**确认收到，**7 个工作日内**给出初步结论与修复计划。
修复发布后会在 `CHANGELOG.md` 与 GitHub 安全公告里致谢（可要求匿名）。

## 不要做的事

- 不要为安全问题开公开 Issue / PR / Discussion，也不要在社交平台公布细节；
- 不要在生产数据上验证漏洞，不要用真实用户数据做 PoC；
- 不要在报告、日志、截图里粘贴完整的 `.env`、密钥、数据库/Redis 连接串、access token——
  这本身就是一次凭据泄露。

## `.env` 与凭据

本项目用项目根 `.env` 承载真实配置（`SECRET_KEY`、`SQLALCHEMY_URI`、`REDIS_URL` 等）。
**`.env` 一直持有真实凭据，永远不要提交到仓库。**

- `.gitignore` 已忽略 `.env` / `*.env`，仓库里只保留占位值的 `.env.example`；
- 提交前自查：

  ```bash
  git status --short | grep -E '\.env$'      # 应该没有输出
  git log --all -- .env                      # 确认真实凭据没进过历史
  ```

- 已经误提交：**只删文件不够**（历史里还在）。必须轮换被泄露的凭据，必要时用
  `git filter-repo` 清理历史并通知所有协作者重新克隆；
- CI 日志、Issue、PR、截图里同样不要出现 `.env` 内容、连接串、token。

## 轮换凭据

发现泄露（或按运维周期）时按下面的顺序操作。

### `SECRET_KEY`

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

1. 写进部署环境的 `.env`（不要提交、不要打进镜像）；
2. 重启服务进程（`/etc/init.d/pyflask restart`、`docker compose up -d` 或重新部署）；
3. **影响**：`SECRET_KEY` 用于会话/CSRF 签名，轮换后**所有已签发会话立即失效**，
   用户需要重新登录；依赖该密钥签名的 token 一并失效。

### 数据库口令

1. 先在数据库侧改口令（`ALTER USER ... IDENTIFIED BY '...'` 或云控制台），确认新口令可登录；
2. 同步更新 `.env` 的 `SQLALCHEMY_URI`（口令含 `@`、`:`、`/` 等字符时要做 URL 编码）；
3. 重启应用，看 `/api/v1/readyz` 与 `/var/log/pyflask.log` 确认连接恢复；
4. 确认没有其它服务/脚本还在用旧口令。

### Redis 口令

1. 在 Redis 侧改 `requirepass`（或 ACL 用户口令），先确认主从/持久化配置同步；
2. 更新 `.env` 的 `REDIS_URL`（`redis://:<新口令>@host:6379/0`）；
3. 重启应用；多实例部署要**逐个**更新并重启，否则新旧口令并存期间会持续报连接失败；
4. 限流/缓存/token 都存在 Redis 里，改口令本身不丢数据，但连接失败期间相关能力会降级
   （框架会自动回退内存实现，`/api/v1/readyz` 会变 503）。

> 轮换后建议跑一遍 `python scripts/verify_real_env.py`（或至少
> `curl http://127.0.0.1:5000/api/v1/health`）确认全链路可用。

## 安全相关配置建议

- `SECRET_KEY` 必须改成随机值；非 `development` 环境用默认值会告警；
- 生产环境开 `AUTH_ENABLED=true`，`TRUSTED_PROXIES` 限定为实际反向代理地址
  （否则客户端可伪造 `X-Forwarded-For` 绕过限流/封锁）；
- 对外暴露时前置 HTTPS 反向代理，并开 `HSTS_ENABLED=true`；
- 需要限流时开 `RATE_LIMIT_ENABLED=true`，多进程部署把 `RATE_LIMIT_STORE` 设为 `redis`；
- `APP_ENV=production` 时错误响应不含堆栈（框架已处理），不要把 `DEBUG=true` 带到生产。
