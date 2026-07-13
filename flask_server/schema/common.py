from marshmallow import Schema, fields

# 通用 Schema 定义，用于 flask-smorest 参数校验与 API 文档


class GraceResultSchema(Schema):
    """统一响应格式 Schema"""
    code = fields.Integer(metadata={'description': '业务状态码，0 表示成功'})
    msg = fields.String(metadata={'description': '状态描述'})
    data = fields.Raw(metadata={'description': '响应数据'}, allow_none=True)


class EchoSchema(Schema):
    """Echo 入参 Schema（校验示例）"""
    message = fields.String(required=True, metadata={'description': '要回显的消息'})
