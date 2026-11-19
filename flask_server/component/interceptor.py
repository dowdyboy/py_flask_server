# 拦截器示例（占位文件，导入本模块没有任何副作用）。
#
# 拦截器就是 Flask 的 before_request 钩子：函数返回 None 时请求继续执行，
# 返回其它值时请求被中断，返回值作为响应。
#
# 真实可用的鉴权拦截器见 component/auth.py 的 auth_interceptor()（由 create_app →
# init_auth 注册）；更简化的教学样例见 examples/component/interceptor_example.py。
#
# 如果你的项目需要额外的请求前检查（签名校验、灰度分流等），在这里定义并调用
# `app.before_request(your_interceptor)` 完成注册——注意注册顺序即执行顺序，
# 见 component/__init__.py 的说明。

# @app.before_request
# def your_interceptor():
#     ...
