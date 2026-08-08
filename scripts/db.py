#!/usr/bin/env python
"""数据库迁移一键脚本（封装 flask db，自动设置 FLASK_APP）

用法：
    python scripts/db.py init                         # 初始化迁移目录（首次）
    python scripts/db.py migrate "create users"       # 生成迁移脚本
    python scripts/db.py upgrade                      # 执行迁移
    python scripts/db.py history                      # 查看历史
"""

import argparse
import os
import subprocess
import sys

FLASK_APP = 'flask_server.app:app'

MIGRATE_COMMANDS = {
    'init': 'init',
    'migrate': 'migrate -m {message}',
    'upgrade': 'upgrade',
    'downgrade': 'downgrade',
    'history': 'history',
    'current': 'current',
}


def build_command(name, message=''):
    """构造 flask db 命令参数列表（可单测）

    使用 `python -m flask` 而非裸 `flask` 命令：
    虚拟环境 Scripts 目录可能不在 PATH，`python -m` 方式跨平台可靠。
    migrate 消息含空格时必须整体作为 -m 的单个参数，
    不能按空白拆分（否则 'create user table' 会变成三个独立参数）。
    """
    if name == 'migrate':
        cmd = [sys.executable, '-m', 'flask', 'db', 'migrate']
        if message:
            cmd += ['-m', message]
        return cmd
    return [sys.executable, '-m', 'flask', 'db'] + MIGRATE_COMMANDS[name].split()


def run(name, message=''):
    if name not in MIGRATE_COMMANDS:
        print(f'[ERROR] 未知命令: {name}（可选: {" / ".join(MIGRATE_COMMANDS)}）')
        sys.exit(1)
    env = dict(os.environ)
    env['FLASK_APP'] = FLASK_APP
    cmd = build_command(name, message)
    print(f'[db] $ {" ".join(cmd)}')
    return subprocess.call(cmd, env=env)


def main():
    parser = argparse.ArgumentParser(description='数据库迁移一键脚本')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('init', help='初始化迁移目录（首次）')
    sub.add_parser('upgrade', help='执行迁移')
    sub.add_parser('downgrade', help='回滚一个版本')
    sub.add_parser('history', help='查看迁移历史')
    sub.add_parser('current', help='查看当前版本')
    migrate_p = sub.add_parser('migrate', help='生成迁移脚本（需消息）')
    migrate_p.add_argument('message', help='迁移说明，如 "create users table"')
    args = parser.parse_args()
    sys.exit(run(args.command, getattr(args, 'message', '')))


if __name__ == '__main__':
    main()
