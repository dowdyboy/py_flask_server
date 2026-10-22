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
    # 内部评估报告不随模板分发
    assert not os.path.exists(os.path.join(dst, 'PROJECT_EVALUATION.md'))


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


def _run_main(args, cwd):
    """在指定 cwd 下调用 scaffold.main()（捕获 SystemExit）"""
    from scaffold import main

    old_cwd = os.getcwd()
    old_argv = sys.argv
    os.chdir(cwd)
    sys.argv = ['scaffold.py'] + args
    try:
        main()
    except SystemExit as e:
        return e.code
    finally:
        os.chdir(old_cwd)
        sys.argv = old_argv
    return None


def test_main_rejects_target_inside_template(tmp_path):
    """目标目录位于模板内部时必须拒绝（否则 copytree 边复制边递归复制自身）"""
    code = _run_main(['nested_project'], PROJECT_ROOT)
    assert code == 1
    assert not os.path.exists(os.path.join(PROJECT_ROOT, 'nested_project')), \
        '不应在模板目录内留下半成品'


def test_main_accepts_sibling_directory(tmp_path):
    """模板之外的目标目录正常生成"""
    code = _run_main([str(tmp_path / 'sibling_proj')], PROJECT_ROOT)
    assert code is None
    assert os.path.exists(os.path.join(str(tmp_path / 'sibling_proj'), 'README.md'))


def test_main_rejects_non_empty_target(tmp_path):
    """已存在且非空的目标目录必须拒绝（避免覆盖用户文件）"""
    target = tmp_path / 'occupied'
    target.mkdir()
    (target / 'keep.txt').write_text('do not overwrite', encoding='utf-8')
    code = _run_main([str(target)], PROJECT_ROOT)
    assert code == 1
    assert (target / 'keep.txt').read_text(encoding='utf-8') == 'do not overwrite'
