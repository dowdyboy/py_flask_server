from flask.views import MethodView
from flask import request
from flask_smorest import Blueprint
from flask_server import config
from flask_server.util import GraceResult, CommonUtil, Logger
from flask_server.schema import GraceResultSchema
from flask_server.schema.auth_schema import (
    AuthRegisterSchema, AuthLoginSchema, AuthRefreshSchema,
)
from flask_server.component.auth import AuthService, login_required

# 认证接口（注册/登录/登出/刷新令牌/当前用户）
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
        """登录，返回 access token + refresh token"""
        # 传入真实来源 IP：防爆破锁定按 (用户名, IP) 隔离，避免他人故意打错密码
        # 把受害者账号锁死（锁定型 DoS）
        tokens, err = AuthService.login(
            data['username'], data['password'],
            client_ip=CommonUtil.get_real_ip(request, config.trusted_proxies),
        )
        if err == 'locked':
            return GraceResult.business_error(4003, '失败次数过多，请稍后再试'), 429
        if tokens is None:
            return GraceResult.business_error(4001, '用户名或密码错误'), 401
        access, refresh = tokens
        user = AuthService.get_user_by_token(access)
        if user is None:
            # 令牌刚写入却读不出来（缓存降级/写入失败）：不能对 None 取属性 → 500
            Logger.error(f'login succeeded but session unreadable for username={data["username"]}')
            return GraceResult.business_error(4002, '会话不可用，请重试'), 503
        return GraceResult.ok({
            'token': access,
            'refresh_token': refresh,
            'uid': user.uid,
            'username': user.username,
        })


@blp.route('/refresh')
class RefreshView(MethodView):
    @blp.arguments(AuthRefreshSchema)
    @blp.response(200, GraceResultSchema)
    def post(self, data):
        """用 refresh_token 换取新令牌（旧 refresh 作废，单次使用）"""
        tokens = AuthService.refresh_access_token(data['refresh_token'])
        if tokens is None:
            return GraceResult.business_error(4002, 'refresh_token 无效或已过期'), 401
        access, new_refresh = tokens
        user = AuthService.get_user_by_token(access)
        if user is None:
            # 用户已被删除/存储被重置：refresh_access_token 已拦截，这里兜底竞态
            return GraceResult.business_error(4002, 'refresh_token 无效或已过期'), 401
        return GraceResult.ok({
            'token': access,
            'refresh_token': new_refresh,
            'uid': user.uid,
            'username': user.username,
        })


@blp.route('/logout')
class LogoutView(MethodView):
    @blp.response(200, GraceResultSchema)
    def post(self):
        """登出（使当前 access token 失效；body 可选携带 refresh_token 一并吊销，
        否则 refresh token 到期自然失效）"""
        # 使用 request.payload（框架已归一为非 dict JSON → {}），body 可不传保持向后兼容
        payload = request.payload if isinstance(request.payload, dict) else {}
        AuthService.logout(request.headers.get('X-AUTH-TOKEN'),
                           refresh_token=payload.get('refresh_token'))
        return GraceResult.ok()


@blp.route('/me')
class MeView(MethodView):
    @blp.response(200, GraceResultSchema)
    @login_required
    def get(self):
        """当前登录用户信息（需要登录）"""
        # login_required 已校验 token 并把 uid 写入 request.info，这里直接按 uid 查询
        uid = request.info.get('uid')
        if uid is None:
            return GraceResult.business_error(4002, '未登录或 Token 已过期'), 401
        user = AuthService.get_user_by_uid(uid)
        if user is None:
            return GraceResult.business_error(4002, '未登录或 Token 已过期'), 401
        # 注意：只返回安全字段，绝不泄露 passwd 哈希
        return GraceResult.ok(CommonUtil.dict_map(
            CommonUtil.obj_to_dict(user), mapper_list=['uid', 'username', 'create_time']))
