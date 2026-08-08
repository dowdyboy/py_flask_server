from flask.views import MethodView
from flask_smorest import Blueprint
from flask_server.util import GraceResult, Logger
from flask_server.schema import GraceResultSchema
from examples.schema.user_schema import (
    UserCreateSchema, UserUpdateSchema, UserQuerySchema,
)
from examples.service.user_crud_service import UserCrudService

# 用户 CRUD 接口（flask-smorest Blueprint 风格，推荐写法）
# 完整展示了：参数校验 + API 文档 + Service 调用 + 统一响应
# 接入工程前需：
#   1. 将 user_schema.py 放入 flask_server/schema/ 并在 __init__.py 导出
#   2. 将 user_crud_service.py 放入 flask_server/service/ 并在 __init__.py 导出
#   3. 将本文件放入 flask_server/controller/
#   4. 在 controller/__init__.py 中注册：
#        from .user_crud_controller import blp as user_crud_blp
#        api.register_blueprint(user_crud_blp)

Logger.info("user_crud_controller.py loaded")

blp = Blueprint('user_crud', 'user_crud', url_prefix='/api/v1/users',
                description='用户管理接口（CRUD 示例）')


@blp.route('/')
class UserListCreateView(MethodView):
    @blp.arguments(UserQuerySchema, location='query')
    @blp.response(200, GraceResultSchema)
    def get(self, query):
        """获取用户列表（分页）"""
        result = UserCrudService.list(page=query.get('page', 1), per_page=query.get('per_page', 10))
        return GraceResult.ok(result)

    @blp.arguments(UserCreateSchema)
    @blp.response(201, GraceResultSchema)
    def post(self, data):
        """创建用户"""
        uid = UserCrudService.create(data['username'], data['password'])
        return GraceResult.ok({'uid': uid}), 201


@blp.route('/<string:uid>')
class UserDetailView(MethodView):
    @blp.response(200, GraceResultSchema)
    def get(self, uid):
        """获取用户详情"""
        user = UserCrudService.get_by_uid(uid)
        if user is None:
            return GraceResult.business_error(4004, '用户不存在'), 404
        return GraceResult.ok(user)

    @blp.arguments(UserUpdateSchema)
    @blp.response(200, GraceResultSchema)
    def put(self, data, uid):
        """更新用户信息"""
        success = UserCrudService.update_by_uid(uid, data.get('username'), data.get('password'))
        if not success:
            return GraceResult.business_error(4004, '用户不存在'), 404
        return GraceResult.ok()

    @blp.response(200, GraceResultSchema)
    def delete(self, uid):
        """删除用户"""
        success = UserCrudService.delete_by_uid(uid)
        if not success:
            return GraceResult.business_error(4004, '用户不存在'), 404
        return GraceResult.ok()
