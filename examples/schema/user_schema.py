from marshmallow import Schema, fields

# 用户 CRUD 的 Schema 定义（flask-smorest 参数校验）
# 配合 examples/controller/user_crud_controller.py 使用


class UserCreateSchema(Schema):
    """创建用户入参"""
    username = fields.String(required=True, metadata={'description': '用户名'})
    password = fields.String(required=True, metadata={'description': '密码'})


class UserUpdateSchema(Schema):
    """更新用户入参"""
    username = fields.String(required=False, metadata={'description': '用户名'})
    password = fields.String(required=False, metadata={'description': '新密码'})


class UserQuerySchema(Schema):
    """查询用户列表的查询参数"""
    page = fields.Integer(required=False, load_default=1, metadata={'description': '页码'})
    per_page = fields.Integer(required=False, load_default=10, metadata={'description': '每页数量'})


class UserResponseSchema(Schema):
    """用户响应数据"""
    uid = fields.String(metadata={'description': '用户ID'})
    username = fields.String(metadata={'description': '用户名'})
    create_time = fields.String(metadata={'description': '创建时间'})
