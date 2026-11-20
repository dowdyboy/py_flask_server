## 变更说明

<!-- 这个 PR 解决了什么问题？为什么这样做？（关联 Issue 写 Closes #123） -->

## 变更类型

- [ ] `fix` 缺陷修复
- [ ] `feat` 新功能
- [ ] `docs` 文档
- [ ] `chore` 构建 / 依赖 / 工具链
- [ ] 其它（说明）：

## 自检清单

- [ ] 新增 / 修改的行为有对应测试（`tests/`），或已说明为什么不需要
- [ ] `make test` 通过（覆盖率 ≥ 85%，由 CI 的 `--cov-fail-under=85` 强制）
- [ ] `make lint` 通过（ruff，检查范围与 CI `lint` job 一致）
- [ ] 改了配置项：`.env.example` 与 `docs/configuration.md` 已同步更新
- [ ] 用户可见的变更已写进 `CHANGELOG.md` 的 `## [Unreleased]` 下
- [ ] 受影响文档已更新（`README.md` / `docs/` / `_e2e/README.md` 等）
- [ ] 没有提交 `.env`、真实凭据、`server.log`、`__pycache__`、`_e2e/logs/` 等本地产物
- [ ] 与安全相关：已按 `SECURITY.md` 走私密渠道，没有在公开 PR / Issue 里贴细节

## 验证方式

<!-- 实际跑了哪些命令 / 请求？粘贴关键输出（去掉凭据）。例如：
make test
make lint
python _e2e/e2e_protocols.py   （改动涉及协议层时）
curl -s http://127.0.0.1:5000/api/v1/health
-->

## 备注

<!-- 需要 reviewer 特别注意的地方、后续计划、兼容性影响等 -->
