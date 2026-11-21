"""统一业务错误码（单一事实来源）。

响应体里的 `code` 字段、controller 的 `GraceResult.business_error(...)` 与
`docs/api-conventions.md` 的错误码表都应引用这里，避免魔法数字散落各处。
新增错误码时同步更新文档与 `tests/test_api_contract.py`（如有断言）。

约定：
- `0` 成功；`-1` 未分类内部错误；`1001` 参数错误（400/422）；
- `4xxx` 认证/会话类；`5030` 依赖未就绪（就绪探针）。
"""

from enum import IntEnum


class ErrorCode(IntEnum):
    OK = 0
    PARAM_ERROR = 1001
    INTERNAL_ERROR = -1

    AUTH_INVALID = 4001          # 用户名已存在 / 用户名或密码错误
    UNAUTHORIZED = 4002          # 未登录、Token/refresh_token 无效或过期、会话不可读
    LOGIN_LOCKED = 4003          # 登录失败次数过多，已临时锁定

    DEPENDENCY_NOT_READY = 5030  # DB/Redis 故障，就绪探针返回 503


__all__ = ['ErrorCode']
