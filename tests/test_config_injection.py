"""配置注入测试：Config 覆盖、create_app(framework_config=...)、配置代理解析优先级"""

import pytest


def _app(**overrides):
    from flask_server.app import create_app
    from flask_server.config import Config

    return create_app({'TESTING': True}, framework_config=Config(overrides))


# ------------------------- Config 对象本身 -------------------------


def test_config_overrides_applied():
    """Config(overrides) 覆盖环境变量派生出的字段"""
    from flask_server.config import Config

    cfg = Config({'api_version': 'v9', 'auth_enabled': True, 'cors_origins': ['https://a.b']})
    assert cfg.api_version == 'v9'
    assert cfg.auth_enabled is True
    assert cfg.cors_origins == ['https://a.b']
    # 未覆盖的字段仍来自环境
    assert cfg.project_dir is not None


def test_config_to_dict_and_repr():
    from flask_server.config import Config

    cfg = Config({'api_version': 'v8'})
    data = cfg.to_dict()
    assert data['api_version'] == 'v8'
    assert all(not k.startswith('_') for k in data)
    assert 'v8' in repr(cfg)


# ------------------------- 注入生效（创建期） -------------------------


def test_create_app_uses_injected_config_at_creation():
    """创建期装配（OpenAPI 版本、密钥、CORS）读注入的配置"""
    from flask_server.app import create_app
    from flask_server.config import Config

    cfg = Config({'api_version': 'v9', 'api_title': 'Injected API', 'secret_key': 'injected-key-123'})
    app = create_app({'TESTING': True}, framework_config=cfg)

    assert app.framework_config is cfg
    assert app.config['API_VERSION'] == 'v9'
    assert app.config['API_TITLE'] == 'Injected API'
    assert app.config['SECRET_KEY'] == 'injected-key-123'
    spec = app.test_client().get('/openapi.json').get_json()
    assert spec['info']['version'] == 'v9'
    assert spec['info']['title'] == 'Injected API'


def test_create_app_exposes_config_on_extensions():
    """配置对象挂在 app.extensions，便于其它扩展/测试按 app 取用"""
    from flask_server.config import CONFIG_EXT_KEY

    app = _app(api_version='v7')
    assert app.extensions[CONFIG_EXT_KEY] is app.framework_config


# ------------------------- 注入生效（请求期） -------------------------


def test_request_time_config_resolves_to_app_config():
    """请求内的 `config.xxx` 解析到该 app 注入的配置（不是进程默认）"""
    app = _app(api_version='v9')
    body = app.test_client().get('/api/v1/health').get_json()
    assert body['data']['version'] == 'v9'


def test_default_app_keeps_default_config():
    """默认 app 仍读进程默认配置（注入不影响它）"""
    from flask_server import app as default_app

    body = default_app.test_client().get('/api/v1/health').get_json()
    assert body['data']['version'] != 'v9'


def test_config_outside_app_context_falls_back_to_default():
    """app 上下文之外（后台线程/脚本）仍是进程默认配置"""
    from flask_server.config import config, default_config

    _app(api_version='v9')          # 创建但不进入它的上下文
    assert config.api_version == default_config.api_version


def test_per_app_auth_switch_is_isolated():
    """同一个开关（AUTH_ENABLED）可以按 app 不同：这是注入的核心价值"""
    app_on = _app(auth_enabled=True)
    app_off = _app(auth_enabled=False)

    on_hooks = [f.__name__ for f in app_on.before_request_funcs.get(None, [])]
    off_hooks = [f.__name__ for f in app_off.before_request_funcs.get(None, [])]
    assert 'auth_interceptor' in on_hooks
    assert 'auth_interceptor' not in off_hooks
    # 进程默认配置（AUTH_ENABLED=false）没有被改写
    from flask_server.config import default_config
    assert default_config.auth_enabled is False


# ------------------------- 向后兼容 -------------------------


def test_monkeypatching_proxy_still_hits_default_config(monkeypatch):
    """既有写法 monkeypatch.setattr(config, 'x', y) 仍作用于进程默认配置"""
    from flask_server import app as default_app
    from flask_server.config import config, default_config

    monkeypatch.setattr(config, 'api_version', 'vx')
    assert default_config.api_version == 'vx'
    assert default_app.test_client().get('/api/v1/health').get_json()['data']['version'] == 'vx'


def test_proxy_write_inside_app_context_targets_app_config():
    """在某个 app 上下文内写入则落到该 app 的配置（隔离性更强，且不污染默认配置）"""
    from flask_server.config import CONFIG_EXT_KEY, config, default_config

    app = _app(api_version='v1')
    with app.app_context():
        config.api_version = 'v-override'
        assert app.extensions[CONFIG_EXT_KEY].api_version == 'v-override'
    assert default_config.api_version != 'v-override'


def test_proxy_private_attribute_raises():
    """代理对双下划线属性直接抛 AttributeError（避免 pickle/copy 探针误解析）"""
    from flask_server.config import config

    with pytest.raises(AttributeError):
        config.__some_deepcopy_probe__
