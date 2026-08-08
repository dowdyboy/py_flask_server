from flask.views import MethodView
from flask import request
from flask_smorest import Blueprint
from flask_server.util import GraceResult, CommonUtil
from flask_server.schema import GraceResultSchema
from flask_server.schema.auth_schema import (
    AuthRegisterSchema, AuthLoginSchema,
)
from flask_server.component.auth import AuthService, login_required

# 认证接口（注册/登录/登出/当前用户）
# 默认使用内存用户存储（AUTH_STORE=memory）；配置 AUTH_STORE=sqlalchemy + DB 后持久化
# AUTH_ENABLED=true 时本模块外的 /api/ 路径需要 X-AUTH-TOKEN

blp = Blueprint('auth', 'auth', url_prefix='/api/v1/auth', description='认证接口')


@blp.route('/register')
class RegisterView(MethodView):
    @blp.arguments(AuthRegisterSchema)
    @blp.response(200, GraceResultSchema)
    def post(self, data):
        """注册用户"""
        try:
            uid = AuthService.register(data['username'], data['password'])
        except ValueError:
            return GraceResult.business_error(4001, '用户名已存在'), 400
        return GraceResult.ok({'uid': uid})


@blp.route('/login')
class LoginView(MethodView):
    @blp.arguments(AuthLoginSchema)
    @blp.response(200, GraceResultSchema)
    def post(self, data):
        """登录，返回 token"""
        token = AuthService.login(data['username'], data['password'])
        if token is None:
            return GraceResult.business_error(4001, '用户名或密码错误'), 401
        user = AuthService.get_user_by_token(token)
        return GraceResult.ok({
            'token': token,
            'uid': user.uid,
            'username': user.username,
        })


@blp.route('/logout')
class LogoutView(MethodView):
    @blp.response(200, GraceResultSchema)
    def post(self):
        """登出（需要登录）"""
        AuthService.logout(request.headers.get('X-AUTH-TOKEN'))
        return GraceResult.ok()


@blp.route('/me')
class MeView(MethodView):
    @blp.response(200, GraceResultSchema)
    @login_required
    def get(self):
        """当前登录用户信息（需要登录）"""
        user = AuthService.get_user_by_token(request.headers.get('X-AUTH-TOKEN'))
        if user is None:
            return GraceResult.business_error(4002, '未登录或 Token 已过期'), 401
        # 注意：只返回安全字段，绝不泄露 passwd 哈希
        return GraceResult.ok(CommonUtil.dict_map(
            CommonUtil.obj_to_dict(user), mapper_list=['uid', 'username', 'create_time']))
