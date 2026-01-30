# 先加载webui的controller
from .webui_controller import *
# 后面加载的url覆盖前面的
from .hello_controller import *
# from .user_controller import *
# from .article_controller import *

# 导出所有controller
# 所有定义的controller都需要被导出，否则不会被加载
# 导出方法如上
# 文件夹内article_controller.py等是使用样例

__all__ = [

]
