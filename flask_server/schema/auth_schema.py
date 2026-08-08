from marshmallow import Schema, fields

# 认证模块 Schema


class AuthRegisterSchema(Schema):
    """注册入参"""
    username = fields.String(required=True, validate=lambda s: 3 <= len(s) <= 64,
                             metadata={'description': '用户名（3-64 字符）'})
    password = fields.String(required=True, validate=lambda s: 6 <= len(s) <= 128,
                             metadata={'description': '密码（6-128 字符）'})


class AuthLoginSchema(Schema):
    """登录入参"""
    username = fields.String(required=True, metadata={'description': '用户名'})
    password = fields.String(required=True, metadata={'description': '密码'})


class AuthRefreshSchema(Schema):
    """刷新令牌入参"""
    refresh_token = fields.String(required=True, metadata={'description': '登录返回的 refresh_token'})


class AuthUserResponseSchema(Schema):
    """用户信息响应"""
    uid = fields.String(metadata={'description': '用户ID'})
    username = fields.String(metadata={'description': '用户名'})
    create_time = fields.String(metadata={'description': '注册时间'})
