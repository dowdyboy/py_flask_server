from datetime import datetime
from flask_server.module import sqlalchemy, sqlalchemy_trans
from flask_server.util import DataEncryptUtil, RandomGenerator, Logger, CommonUtil

# 用户 CRUD 业务服务（flask-smorest 风格示例的 Service 层）
# 配合 examples/model/user_declared.py 的声明式 UserPO 使用
# 接入工程前需：
#   1. 配置 SQLALCHEMY_URI
#   2. 将 user_declared.py 的 UserPO 放入 flask_server/model/po/ 并在 __init__.py 导出
#   3. 将本文件放入 flask_server/service/ 并在 __init__.py 导出


# 延迟导入 UserPO，避免未配置 DB 时 import 报错
# UserPO 声明式定义见 examples/model/user_declared.py
def _get_user_po():
    from examples.model.user_declared import UserPO
    return UserPO


class UserCrudService:

    @staticmethod
    @sqlalchemy_trans
    def create(username, password):
        """创建用户"""
        UserPO = _get_user_po()
        uid = RandomGenerator.secrets_token(16)
        user = UserPO()
        user.uid = uid
        user.username = username
        user.passwd = DataEncryptUtil.pbkdf2_hmac(password)
        user.create_time = datetime.now()
        sqlalchemy().session.add(user)
        Logger.info(f'UserCrudService create: uid={uid}, username={username}')
        return uid

    @staticmethod
    def get_by_uid(uid):
        """根据 uid 查询单个用户"""
        UserPO = _get_user_po()
        user = UserPO.query.filter(UserPO.uid == uid).first()
        if user is None:
            return None
        return CommonUtil.obj_to_dict(user)

    @staticmethod
    def list(page=1, per_page=10):
        """分页查询用户列表"""
        UserPO = _get_user_po()
        pagination = UserPO.query.paginate(page=page, per_page=per_page, error_out=False)
        users = []
        for u in pagination.items:
            d = CommonUtil.obj_to_dict(u)
            # datetime 经 obj_to_dict 后变为 str，无需再 strftime
            users.append(d)
        return {
            'users': users,
            'total': pagination.total,
            'page': page,
            'per_page': per_page,
        }

    @staticmethod
    @sqlalchemy_trans
    def update_by_uid(uid, username=None, password=None):
        """更新用户信息"""
        UserPO = _get_user_po()
        user = UserPO.query.filter(UserPO.uid == uid).first()
        if user is None:
            return False
        if username is not None:
            user.username = username
        if password is not None:
            user.passwd = DataEncryptUtil.pbkdf2_hmac(password)
        return True

    @staticmethod
    @sqlalchemy_trans
    def delete_by_uid(uid):
        """删除用户"""
        UserPO = _get_user_po()
        user = UserPO.query.filter(UserPO.uid == uid).first()
        if user is None:
            return False
        sqlalchemy().session.delete(user)
        return True
