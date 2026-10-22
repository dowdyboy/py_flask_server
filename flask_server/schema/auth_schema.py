from marshmallow import Schema, fields, validate

# 认证模块 Schema
#
# 注意：校验器必须使用 validate.Length（内部抛 ValidationError），
# 不要用返回 bool 的 lambda——marshmallow 4 起仅将抛 ValidationError 视为校验失败，
# 返回 False 会被静默忽略（marshmallow 3 的旧行为）。

# 注册用户名字符集白名单：字母/数字/下划线/点/连字符/@
# 安全意义：username 会被写进日志（AuthService register/login）与缓存键
# （auth:login_fail:{username}:{ip}）。若不限字符集，注册一个含 "\n" 的用户名即可
# 伪造日志行（log forging），含 ":" 可让缓存键结构歧义。此处从输入端收紧。
_USERNAME_PATTERN = r'^[A-Za-z0-9_.@-]+$'
_USERNAME_VALIDATORS = [
    validate.Length(min=3, max=64),
    validate.Regexp(_USERNAME_PATTERN, error='用户名只能包含字母、数字、下划线、点、@ 和连字符'),
]

# 登录用户名校验：**故意放宽**为"仅禁止控制字符"。
# 白名单只约束新注册；存量用户（中文名、"张 三"、老系统迁移过来的邮箱别名等）若被
# 登录校验拒掉就是一次破坏性变更——升级后这些人直接 422 登不进来。
# 日志注入面已由 Logger 出口统一转义 CR/LF 兜住，这里只需挡住控制字符
# （含 \n \r \t \x00，防止污染日志行与缓存键）。
_LOGIN_USERNAME_VALIDATORS = [
    validate.Length(min=3, max=64),
    validate.Regexp(r'^[^\x00-\x1f\x7f]+$', error='用户名不能包含控制字符'),
]


class AuthRegisterSchema(Schema):
    """注册入参"""
    username = fields.String(required=True, validate=_USERNAME_VALIDATORS,
                             metadata={'description': '用户名（3-64 字符，字母/数字/_ . @ -）'})
    password = fields.String(required=True, validate=validate.Length(min=6, max=128),
                             metadata={'description': '密码（6-128 字符）'})


class AuthLoginSchema(Schema):
    """登录入参"""
    # 长度限制与注册一致（防超大 username 撑爆缓存 key 与日志行），
    # 字符集只禁控制字符：保证存量用户名（中文/空格等）仍可登录
    username = fields.String(required=True, validate=_LOGIN_USERNAME_VALIDATORS,
                             metadata={'description': '用户名（3-64 字符，不能含控制字符）'})
    # 与注册保持一致的密码长度上下限，防止超大密码拖慢 PBKDF2 校验（CPU DoS 面）
    password = fields.String(required=True, validate=validate.Length(min=6, max=128),
                             metadata={'description': '密码（6-128 字符）'})


class AuthRefreshSchema(Schema):
    """刷新令牌入参"""
    # 限长：防止超大 token 造成超大缓存 key 查询
    refresh_token = fields.String(required=True, validate=validate.Length(max=256),
                                  metadata={'description': '登录返回的 refresh_token'})


class AuthUserResponseSchema(Schema):
    """用户信息响应"""
    uid = fields.String(metadata={'description': '用户ID'})
    username = fields.String(metadata={'description': '用户名'})
    create_time = fields.String(metadata={'description': '注册时间'})
