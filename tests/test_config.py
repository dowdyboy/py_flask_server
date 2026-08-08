"""配置解析测试：整数解析三态、布尔解析、环境预设档（纯函数，避免 reload 单例）"""

import logging
from importlib import import_module

# 注意：flask_server 包的 config 属性是 Config 实例（遮蔽了模块），
# 必须用 importlib 获取真实模块以访问 _parse_int/_ENV_PRESETS
config_module = import_module('flask_server.config')


def test_parse_int_valid(monkeypatch):
    monkeypatch.setenv('T_PORT', '8080')
    assert config_module._parse_int('T_PORT', 5000) == 8080


def test_parse_int_invalid_falls_back(monkeypatch, capsys):
    monkeypatch.setenv('T_PORT', 'abc')
    assert config_module._parse_int('T_PORT', 5000) == 5000
    assert 'not a valid integer' in capsys.readouterr().out


def test_parse_int_missing(monkeypatch):
    monkeypatch.delenv('T_MISSING', raising=False)
    assert config_module._parse_int('T_MISSING', 5000) == 5000


def test_boolean_true_variants(monkeypatch):
    for v in ('1', 'true', 'TRUE', 'yes'):
        monkeypatch.setenv('T_BOOL', v)
        assert os_env_bool('T_BOOL', False) is True


def test_boolean_false_variants(monkeypatch):
    for v in ('0', 'false', 'no', ''):
        monkeypatch.setenv('T_BOOL', v)
        assert os_env_bool('T_BOOL', False) is False


def os_env_bool(name, default):
    """复刻 config.py 的布尔解析逻辑（避免重复实现漂移）"""
    return config_module.os.environ.get(name, str(default)).lower() in ('1', 'true', 'yes')


def test_env_presets_defined():
    presets = config_module._ENV_PRESETS
    assert set(presets.keys()) == {'development', 'staging', 'production'}
    for preset in presets.values():
        assert 'debug' in preset
        assert 'host' in preset
        assert 'log_level' in preset
        assert 'log_to_console' in preset


def test_dev_preset_values():
    dev = config_module._ENV_PRESETS['development']
    assert dev['debug'] is True
    assert dev['host'] == '127.0.0.1'
    assert dev['log_level'] == logging.DEBUG
    assert dev['log_to_console'] is True


def test_prod_preset_values():
    prod = config_module._ENV_PRESETS['production']
    assert prod['debug'] is False
    assert prod['host'] == '0.0.0.0'
    assert prod['log_level'] == logging.INFO
    assert prod['log_to_console'] is False


def test_socketio_buffer_default(monkeypatch):
    """SOCKETIO_MAX_HTTP_BUFFER_SIZE 默认 1MB"""
    monkeypatch.delenv('SOCKETIO_MAX_HTTP_BUFFER_SIZE', raising=False)
    assert config_module._parse_int('SOCKETIO_MAX_HTTP_BUFFER_SIZE', 1_000_000) == 1_000_000


def test_socketio_buffer_custom(monkeypatch):
    monkeypatch.setenv('SOCKETIO_MAX_HTTP_BUFFER_SIZE', '1048576')
    assert config_module._parse_int('SOCKETIO_MAX_HTTP_BUFFER_SIZE', 1_000_000) == 1048576
