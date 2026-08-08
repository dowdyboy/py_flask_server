#!/usr/bin/env python
"""一键生成新项目脚手架

用法：
    python scripts/scaffold.py my_new_project                 # 生成到当前目录
    python scripts/scaffold.py my_new_project --author "John"  # 指定 LICENSE 署名
    python scripts/scaffold.py --help

生成内容：模板源码 + 配置 + 文档（不含 .git/缓存/日志/数据文件），
自动将 LICENSE 署名替换为 --author（默认 "Your Name"）。
"""

import argparse
import os
import re
import shutil
import sys
from datetime import date

# 复制时排除的目录/文件
ALWAYS_EXCLUDE_DIRS = {'.git', '__pycache__', '.pytest_cache', '.venv', 'venv',
                       'htmlcov', '.idea', '.vscode', '.opencode'}
EXCLUDE_FILES = {'.coverage', '.DS_Store', 'Thumbs.db'}


def copy_template(src_dir, dst_dir, author='Your Name', exclude_dirs=None):
    """复制脚手架模板到新目录，返回复制文件数。

    排除：版本控制、缓存、日志、数据库文件、本地环境配置。
    替换：LICENSE 中的署名（Copyright (c) <year> <author>）。
    """
    exclude_dirs = set(exclude_dirs or []) | ALWAYS_EXCLUDE_DIRS

    def _ignore(current, names):
        ignored = set()
        for name in names:
            full = os.path.join(current, name)
            if name in exclude_dirs:
                ignored.add(name)
            elif name in EXCLUDE_FILES:
                ignored.add(name)
            elif os.path.isfile(full) and name == '.env':
                ignored.add(name)
            elif name.endswith(('.log', '.db', '.sqlite', '.sqlite3', '.pyc', '.pyo')):
                ignored.add(name)
            # storage/ 目录下仅保留占位文件，不复制用户数据
            elif os.path.basename(current) == 'storage' and name != '.gitkeep':
                ignored.add(name)
        return ignored

    os.makedirs(dst_dir, exist_ok=True)
    shutil.copytree(src_dir, dst_dir, ignore=_ignore, dirs_exist_ok=True)

    # 替换 LICENSE 署名
    license_path = os.path.join(dst_dir, 'LICENSE')
    if os.path.exists(license_path):
        with open(license_path, 'r', encoding='utf-8') as f:
            content = f.read()
        new_content = re.sub(
            r'Copyright \(c\) \d{4}[^,\n]*',
            f'Copyright (c) {date.today().year} {author}',
            content,
        )
        with open(license_path, 'w', encoding='utf-8') as f:
            f.write(new_content)

    # 统计复制文件数
    count = 0
    for _, _, files in os.walk(dst_dir):
        count += len(files)
    return count


def main():
    parser = argparse.ArgumentParser(description='生成 Flask Server 新项目脚手架')
    parser.add_argument('project', help='新项目目录名')
    parser.add_argument('--author', default='Your Name', help='LICENSE 署名（默认 Your Name）')
    args = parser.parse_args()

    src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dst_dir = os.path.abspath(args.project)
    if os.path.exists(dst_dir) and os.listdir(dst_dir):
        print(f'[ERROR] 目标目录 {dst_dir} 已存在且非空')
        sys.exit(1)

    count = copy_template(src_dir, dst_dir, author=args.author)
    print(f'[OK] 脚手架已生成: {dst_dir}（{count} 个文件）')
    print()
    print('下一步：')
    print(f'  cd {args.project}')
    print('  python -m venv .venv && .venv\\Scripts\\activate   # Windows')
    print('  # source .venv/bin/activate                        # Linux/macOS')
    print('  pip install -r requirements.txt -r requirements-dev.txt')
    print('  cp .env.example .env')
    print('  python server.py                                   # http://127.0.0.1:5000/docs')


if __name__ == '__main__':
    main()
