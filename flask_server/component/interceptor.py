from flask_server.util import Logger

# 拦截器定义，可以在请求处理前添加认证和授权检查等操作
# 写法参考flask，使用了flask的before_request装饰器
# 拦截器函数返回None时，请求继续执行，返回其他值时，请求被中断，返回值作为响应返回
# 完整鉴权拦截器样例参考 examples/component/interceptor_example.py

Logger.info('component interceptor loaded')

# @app.before_request
# def your_interceptor():
#     ...
