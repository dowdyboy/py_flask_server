# 导出所有PersistentObject，用于对MySQL数据库进行映射
# 用户/文章等模型样例可参考 examples/model/

from .user import UserPO

__all__ = [
    'UserPO',
]
