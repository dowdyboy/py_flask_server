"""scaffold 脚手架脚本测试"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'scripts'))

from scaffold import copy_template   # noqa: E402


def test_copy_template_excludes_git_and_caches(tmp_path):
    dst = str(tmp_path / 'proj')
    copy_template(PROJECT_ROOT, dst)
    assert os.path.exists(os.path.join(dst, 'README.md'))
    assert os.path.exists(os.path.join(dst, 'flask_server', 'app.py'))
    # 排除项
    assert not os.path.exists(os.path.join(dst, '.git'))
    assert not os.path.exists(os.path.join(dst, '.pytest_cache'))
    assert not os.path.exists(os.path.join(dst, '.venv'))
    assert not os.path.exists(os.path.join(dst, 'server.log'))
    # .env 排除、.env.example 保留
    assert not os.path.exists(os.path.join(dst, '.env'))
    assert os.path.exists(os.path.join(dst, '.env.example'))


def test_copy_template_replaces_license_author(tmp_path):
    dst = str(tmp_path / 'proj2')
    copy_template(PROJECT_ROOT, dst, author='Jane Doe')
    with open(os.path.join(dst, 'LICENSE'), 'r', encoding='utf-8') as f:
        content = f.read()
    assert 'Jane Doe' in content


def test_copy_template_keeps_examples_and_docs(tmp_path):
    dst = str(tmp_path / 'proj3')
    copy_template(PROJECT_ROOT, dst)
    assert os.path.exists(os.path.join(dst, 'examples', 'api.http'))
    assert os.path.exists(os.path.join(dst, 'docs', 'getting-started.md'))
    assert os.path.exists(os.path.join(dst, 'scripts', 'scaffold.py'))
