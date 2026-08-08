"""启动 banner 与生产自检测试"""

from flask_server import config
from flask_server.util.banner import build_banner, check_production_config


def test_build_banner_contains_key_info():
    banner = '\n'.join(build_banner())
    assert 'APP_ENV' in banner
    assert 'LISTEN' in banner
    assert '/docs' in banner
    assert 'DATABASE' in banner


def test_build_banner_reflects_config(monkeypatch):
    monkeypatch.setattr(config, 'auth_enabled', True)
    monkeypatch.setattr(config, 'rate_limit_enabled', True)
    banner = '\n'.join(build_banner())
    assert 'AUTH' in banner
    assert 'ON' in banner
    assert 'RATE LIMIT' in banner


def test_check_production_config_dev_no_warnings():
    """development 环境不产生生产检查警告"""
    assert check_production_config() == []


def test_check_production_config_warns_on_defaults(monkeypatch):
    monkeypatch.setattr(config, 'app_env', 'production')
    monkeypatch.setattr(config, 'secret_key_is_default', True)
    monkeypatch.setattr(config, 'cors_origins', '*')
    monkeypatch.setattr(config, 'debug', False)
    monkeypatch.setattr(config, 'host', '0.0.0.0')
    warnings = check_production_config()
    assert any('SECRET_KEY' in w for w in warnings)
    assert any('CORS' in w for w in warnings)


def test_check_production_config_clean(monkeypatch):
    monkeypatch.setattr(config, 'app_env', 'production')
    monkeypatch.setattr(config, 'secret_key_is_default', False)
    monkeypatch.setattr(config, 'cors_origins', ['https://app.example.com'])
    monkeypatch.setattr(config, 'debug', False)
    monkeypatch.setattr(config, 'host', '0.0.0.0')
    monkeypatch.delenv('WORKER_NUM', raising=False)
    assert check_production_config() == []


def test_check_production_config_warns_multi_worker_without_redis(monkeypatch):
    """多 worker 未配 Redis 时告警（memory_cache 进程内不一致）"""
    monkeypatch.setattr(config, 'app_env', 'production')
    monkeypatch.setattr(config, 'secret_key_is_default', False)
    monkeypatch.setattr(config, 'cors_origins', ['https://app.example.com'])
    monkeypatch.setattr(config, 'debug', False)
    monkeypatch.setattr(config, 'host', '0.0.0.0')
    monkeypatch.setattr(config, 'redis_url', None)
    monkeypatch.setenv('WORKER_NUM', '4')
    warnings = check_production_config()
    assert any('REDIS_URL' in w for w in warnings)


def test_check_production_config_multi_worker_with_redis_ok(monkeypatch):
    """多 worker 已配 Redis 时不告警"""
    monkeypatch.setattr(config, 'app_env', 'production')
    monkeypatch.setattr(config, 'secret_key_is_default', False)
    monkeypatch.setattr(config, 'cors_origins', ['https://app.example.com'])
    monkeypatch.setattr(config, 'debug', False)
    monkeypatch.setattr(config, 'host', '0.0.0.0')
    monkeypatch.setattr(config, 'redis_url', 'redis://localhost:6379/0')
    monkeypatch.setenv('WORKER_NUM', '4')
    assert check_production_config() == []
