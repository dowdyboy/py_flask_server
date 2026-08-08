from marshmallow import Schema, fields, validate

# 认证模块 Schema
#
# 注意：校验器必须使用 validate.Length（内部抛 ValidationError），
# 不要用返回 bool 的 lambda——marshmallow 4 起仅将抛 ValidationError 视为校验失败，
# 返回 False 会被静默忽略（marshmallow 3 的旧行为）。


class AuthRegisterSchema(Schema):
    """注册入参"""
    username = fields.String(required=True, validate=validate.Length(min=3, max=64),
                             metadata={'description': '用户名（3-64 字符）'})
    password = fields.String(required=True, validate=validate.Length(min=6, max=128),
                             metadata={'description': '密码（6-128 字符）'})


class AuthLoginSchema(Schema):
    """登录入参"""
    username = fields.String(required=True, metadata={'description': '用户名'})
    # 与注册保持一致的密码长度上下限，防止超大密码拖慢 PBKDF2 校验（CPU DoS 面）
    password = fields.String(required=True, validate=validate.Length(min=6, max=128),
                             metadata={'description': '密码（6-128 字符）'})


class AuthRefreshSchema(Schema):
    """刷新令牌入参"""
    refresh_token = fields.String(required=True, metadata={'description': '登录返回的 refresh_token'})


class AuthUserResponseSchema(Schema):
    """用户信息响应"""
    uid = fields.String(metadata={'description': '用户ID'})
    username = fields.String(metadata={'description': '用户名'})
    create_time = fields.String(metadata={'description': '注册时间'})
