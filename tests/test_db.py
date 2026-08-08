"""scripts/db.py 命令构造测试"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'scripts'))

from db import build_command   # noqa: E402

PY = sys.executable


def test_init_command():
    assert build_command('init') == [PY, '-m', 'flask', 'db', 'init']


def test_upgrade_command():
    assert build_command('upgrade') == [PY, '-m', 'flask', 'db', 'upgrade']


def test_migrate_command_single_word():
    assert build_command('migrate', 'init') == [PY, '-m', 'flask', 'db', 'migrate', '-m', 'init']


def test_migrate_command_multi_word_message():
    """含空格的消息必须整体作为 -m 单个参数（修复前会被拆成多个参数）"""
    cmd = build_command('migrate', 'create user table')
    assert cmd == [PY, '-m', 'flask', 'db', 'migrate', '-m', 'create user table']


def test_migrate_command_empty_message():
    assert build_command('migrate') == [PY, '-m', 'flask', 'db', 'migrate']
