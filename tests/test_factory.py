"""应用工厂测试：create_app() 的可重复创建、装配完整性、与模块级 app 的隔离"""

import pytest


def _new_app(**cfg):
    from flask_server.app import create_app
    return create_app({'TESTING': True, **cfg})


def test_module_level_app_exists():
    """向后兼容：`from flask_server import app` 仍是已装配好的实例"""
    from flask_server import app
    from flask import Flask

    assert isinstance(app, Flask)
    assert app.api is not None
    assert app.api is not None


def test_create_app_returns_independent_instance():
    """create_app() 产出新实例，不影响模块级默认 app"""
    from flask_server import app as default_app

    new_app = _new_app()
    assert new_app is not default_app
    # 扩展对象是与该 app 绑定的独立实例
    assert new_app.api is not default_app.api
    assert new_app.extensions.get('flask-smorest') is not None


def test_create_app_registers_all_route_kinds():
    """三条来源的路由都要挂上：Blueprint、@app.route 风格钩子、catch-all 静态站点"""
    app = _new_app()
    rules = {str(r) for r in app.url_map.iter_rules()}

    assert '/hello' in rules                  # 模块级 register(app) 钩子
    assert '/' in rules                       # webui catch-all（默认 index.html）
    assert '/<path:filename>' in rules        # webui catch-all
    assert '/docs' in rules                   # flask-smorest 文档
    assert '/openapi.json' in rules
    assert '/metrics' in rules                # 指标端点
    assert '/api/v1/healthz' in rules         # Blueprint 路由
    assert '/api/v1/auth/login' in rules


def test_create_app_handles_requests():
    """/hello 走统一 JSON 响应（装饰器在工厂创建的新 app 上同样生效）"""
    app = _new_app()
    resp = app.test_client().get('/hello')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['code'] == 0
    assert body['data'] == {'message': 'Hello, World!'}
    # 安全响应头与 request_id 钩子也已注册
    assert resp.headers.get('X-Content-Type-Options') == 'nosniff'
    assert resp.headers.get('X-Request-Id')


def test_create_app_registers_hooks_in_order():
    """钩子注册顺序必须与历史行为一致（限流在认证之前、指标最后）

    注：Flask-CORS 会在工厂里先注册自己的 after_request，属预期；这里只校验本框架的钩子
    相对顺序，且 after_request 的实际执行顺序是注册顺序的**反向**。
    """
    app = _new_app()
    before_names = [f.__name__ for f in app.before_request_funcs.get(None, [])]
    assert before_names == [
        'init_request_info', 'parse_request_param', 'parse_request_json',
        'parse_request_form_data', 'rate_limit_check', 'metrics_start_timer',
    ]
    after_names = [f.__name__ for f in app.after_request_funcs.get(None, [])]
    framework_hooks = [n for n in after_names if n != 'cors_after_request']
    assert framework_hooks == ['set_request_id_header', 'set_security_headers', 'metrics_record']


def test_create_app_registers_error_handlers():
    """四类异常处理器都注册到新 app 上（含 404 → 统一 GraceResult）"""
    app = _new_app()
    client = app.test_client()

    # 注意：任意路径都会被 webui 的 SPA catch-all 接住（回退 index.html），
    # 因此用 /api/ 前缀触发真正的 404 分支
    resp = client.get('/api/v1/definitely-not-exists')
    assert resp.status_code == 404
    assert resp.get_json()['code'] == -1

    resp = client.post('/api/v1/echo', json={})
    assert resp.status_code == 422
    assert resp.get_json()['code'] == 1001


def test_create_app_accepts_flask_config():
    """app_config 参数合并进 Flask 配置"""
    app = _new_app(MY_FLAG='x')
    assert app.config['TESTING'] is True
    assert app.config['MY_FLAG'] == 'x'


def test_create_app_is_repeatable():
    """可重复调用：两次创建互不影响（同进程多实例）"""
    app1 = _new_app()
    app2 = _new_app()
    assert app1 is not app2
    assert app1.test_client().get('/hello').status_code == 200
    assert app2.test_client().get('/hello').status_code == 200
    # 两次创建装配出相同的路由集合（蓝图 + register(app) 钩子 + 静态站点）
    assert {str(r) for r in app1.url_map.iter_rules()} == {str(r) for r in app2.url_map.iter_rules()}


def test_create_app_does_not_duplicate_routes_on_repeat():
    """同一 app 重复 register_controllers 不重复注册（幂等标记落在 app.extensions）"""
    from flask_server.controller import register_controllers

    app = _new_app()
    rules_before = len(list(app.url_map.iter_rules()))
    register_controllers(app, app.api)
    assert len(list(app.url_map.iter_rules())) == rules_before
    assert app.extensions['flask_server.controllers_registered'] is True


def test_init_sqlalchemy_keeps_db_on_second_call():
    """create_app() 二次调用不得把已初始化的 db 重置为 None"""
    import importlib

    from flask_server import config

    sa = importlib.import_module('flask_server.module.sqlalchemy')
    if config.sqlalchemy_uri is not None:
        pytest.skip('测试环境已配置真实数据库，跳过单例复用断言')

    sentinel = object()
    original = sa.db
    sa.db = sentinel
    try:
        sa.init_SQLAlchemy(_new_app())
        assert sa.db is sentinel, '已初始化的 db 不应被二次初始化重置'
    finally:
        sa.db = original
