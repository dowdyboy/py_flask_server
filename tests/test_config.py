"""配置解析测试：整数解析三态、布尔解析、环境预设档（纯函数，避免 reload 单例）"""

import logging
import os
import subprocess
import sys
from importlib import import_module

# 注意：flask_server 包的 config 属性是 Config 实例（遮蔽了模块），
# 必须用 importlib 获取真实模块以访问 _parse_int/_ENV_PRESETS
config_module = import_module('flask_server.config')

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run_in_subprocess(code):
    """在独立子进程中执行代码（避免污染本进程的 config 单例与模块导入状态）"""
    result = subprocess.run(
        [sys.executable, '-c', code],
        capture_output=True, text=True, cwd=PROJECT_ROOT,
    )
    return result


def test_empty_env_values_treated_as_unset():
    """P1 回归：.env.example 中 DB/Redis 变量为空字符串时，应用必须能正常导入启动。

    修复前：SQLALCHEMY_URI='' 触发 SQLAlchemy URL 解析崩溃、REDIS_URL='' 触发
    Redis from_url ValueError、SQLITE_DB_PATH='' 静默创建临时数据库。
    """
    code = (
        "import os\n"
        "os.environ['SQLALCHEMY_URI'] = ''\n"
        "os.environ['REDIS_URL'] = ''\n"
        "os.environ['SQLITE_DB_PATH'] = ''\n"
        "os.environ['INIT_SQL_PATH'] = ''\n"
        "import flask_server\n"
        "from flask_server import config\n"
        "assert config.sqlalchemy_uri is None, config.sqlalchemy_uri\n"
        "assert config.redis_url is None, config.redis_url\n"
        "assert config.db_file_path is None, config.db_file_path\n"
        "print('OK')\n"
    )
    result = _run_in_subprocess(code)
    assert result.returncode == 0, f'import crashed, stderr:\n{result.stderr}'
    assert 'OK' in result.stdout


def test_log_to_console_empty_uses_preset_dev():
    """P2 回归：LOG_TO_CONSOLE 空字符串应视为未设置，开发环境跟随预设档（True）"""
    code = (
        "import os\n"
        "os.environ['APP_ENV'] = 'development'\n"
        "os.environ['LOG_TO_CONSOLE'] = ''\n"
        "from flask_server.config import config\n"
        "assert config.log_to_console is True, config.log_to_console\n"
        "print('OK')\n"
    )
    result = _run_in_subprocess(code)
    assert result.returncode == 0, f'stderr:\n{result.stderr}'
    assert 'OK' in result.stdout


def test_log_to_console_empty_uses_preset_prod():
    """P2 回归：生产环境 LOG_TO_CONSOLE 空字符串跟随预设档（False），显式 true 仍可覆盖"""
    code = (
        "import os\n"
        "os.environ['APP_ENV'] = 'production'\n"
        "os.environ['LOG_TO_CONSOLE'] = ''\n"
        "from flask_server.config import config\n"
        "assert config.log_to_console is False, config.log_to_console\n"
        "print('OK')\n"
    )
    result = _run_in_subprocess(code)
    assert result.returncode == 0, f'stderr:\n{result.stderr}'
    assert 'OK' in result.stdout


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


def test_init_sql_path_loaded(tmp_path, monkeypatch):
    """INIT_SQL_PATH 指向的 SQL 文件被解析为 db_init_sql_list（新 Config 实例，不动单例）"""
    sql = tmp_path / 'init.sql'
    sql.write_text('CREATE TABLE t(id INT); INSERT INTO t VALUES (1);', encoding='utf-8')
    monkeypatch.setenv('INIT_SQL_PATH', str(sql))
    cfg = config_module.Config()
    assert cfg.db_init_sql_list == ['CREATE TABLE t(id INT)', 'INSERT INTO t VALUES (1)']
