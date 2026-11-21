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


class PaginationQuerySchema(Schema):
    """分页 query 参数（配合 flask_server.util.paginate 使用）"""
    page = fields.Integer(load_default=1, validate=lambda v: v >= 1,
                          metadata={'description': '页码（从 1 开始）'})
    per_page = fields.Integer(load_default=20, validate=lambda v: 1 <= v <= 100,
                              metadata={'description': '每页条数（1-100）'})


class PageSchema(Schema):
    """分页响应：{items, page, per_page, total, pages}"""
    items = fields.List(fields.Dict(), metadata={'description': '当前页数据'})
    page = fields.Integer(metadata={'description': '当前页码'})
    per_page = fields.Integer(metadata={'description': '每页条数'})
    total = fields.Integer(metadata={'description': '总条数'})
    pages = fields.Integer(metadata={'description': '总页数'})

