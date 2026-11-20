"""文档一致性测试：把"配置键/版本/相对链接"从人工检查变成机器检查。

只使用标准库（pathlib/re），不引入新依赖；文件缺失时给出明确 skip 原因，
避免把"文档被重命名/删除"误报成测试基础设施故障。
"""

import re
from pathlib import Path
from urllib.parse import unquote

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PY = PROJECT_ROOT / 'flask_server' / 'config.py'
ENV_EXAMPLE = PROJECT_ROOT / '.env.example'
DOCS_DIR = PROJECT_ROOT / 'docs'
CONFIG_DOC = DOCS_DIR / 'configuration.md'
PYPROJECT = PROJECT_ROOT / 'pyproject.toml'
CHANGELOG = PROJECT_ROOT / 'CHANGELOG.md'

# 应用代码实际读取的环境变量之外，再由别处使用/读取的键：
#   - WORKER_NUM：wsgi_gunicorn.py 的 worker 数（config._parse_worker_num 间接读取）
#   - FLASK_APP：Flask CLI 使用（scripts/db.py 设置），应用代码不读取，文档表可省略
#   - PROMETHEUS_MULTIPROC_DIR / INIT_SQL_PATH：config.py 读取，但 .env.example 中保持注释
_EXTRA_ENV_NAMES = {'WORKER_NUM', 'FLASK_APP', 'PROMETHEUS_MULTIPROC_DIR', 'INIT_SQL_PATH'}

# .env.example 中有意保持注释的键（默认不写文件日志 / 不启用多进程指标）。
# 若这些键改为"活动配置"，默认行为会变化，必须同步更新本测试。
_ENV_EXAMPLE_COMMENTED = {'LOG_FILE_PATH', 'PROMETHEUS_MULTIPROC_DIR'}

# docs/configuration.md 环境变量表有意不收录的键（仅 Flask CLI 使用）
_DOCS_ALLOW_MISSING = {'FLASK_APP'}

# os.environ.get('X', ...) / os.environ['X'] / _parse_int('X', ...) / _parse_bytes_env('X', ...)
_ENV_READ_RE = re.compile(
    r"""os\.environ(?:\.get|\[)\s*\(?\s*['"]([A-Za-z_][A-Za-z0-9_]*)['"]"""
    r"""|_(?:parse_int|parse_bytes_env|parse_worker_num)\s*\(\s*['"]([A-Za-z_][A-Za-z0-9_]*)['"]"""
)
_ENV_LINE_RE = re.compile(r'^\s*(?P<comment>#\s*)?(?P<key>[A-Z_][A-Z0-9_]*)\s*=')
_ENV_DOC_ROW_RE = re.compile(r'^\|\s*`([A-Z_][A-Z0-9_]*)`\s*\|', re.M)
_VERSION_RE = re.compile(r'^version\s*=\s*["\']([^"\']+)["\']', re.M)
_MD_LINK_RE = re.compile(r'!?\[[^\]]*\]\(([^)\s]+)(?:\s+["\'][^"\']*["\'])?\)')
_FENCED_CODE_RE = re.compile(r'```.*?```', re.S)


def _read(path):
    if not path.is_file():
        pytest.skip(f'文档一致性检查所需文件不存在: {path}（重命名/删除后请同步更新测试）')
    return path.read_text(encoding='utf-8')


def _source_env_names():
    """从 config.py 实际读取 env 的调用点提取变量名，并补上别处使用的键"""
    names = set()
    for match in _ENV_READ_RE.finditer(_read(CONFIG_PY)):
        names.add(match.group(1) or match.group(2))
    assert names, '未能从 config.py 提取任何环境变量名（提取正则是否已失效？）'
    return names | _EXTRA_ENV_NAMES


def _env_example_keys():
    """返回 (.env.example 的活动键集合, 被注释掉的键集合)"""
    active, commented = set(), set()
    for line in _read(ENV_EXAMPLE).splitlines():
        match = _ENV_LINE_RE.match(line)
        if not match:
            continue
        target = commented if match.group('comment') else active
        target.add(match.group('key'))
    return active, commented


def _documented_env_names():
    return set(_ENV_DOC_ROW_RE.findall(_read(CONFIG_DOC)))


def _mask_fenced_code(text):
    """把 ```代码块替换为等量换行：链接检查忽略代码示例，同时保持行号不变"""
    return _FENCED_CODE_RE.sub(lambda match: '\n' * match.group(0).count('\n'), text)


def _markdown_files():
    if not DOCS_DIR.is_dir():
        pytest.skip(f'docs/ 目录不存在: {DOCS_DIR}')
    files = []
    readme = PROJECT_ROOT / 'README.md'
    if readme.is_file():
        files.append(readme)
    files.extend(sorted(DOCS_DIR.rglob('*.md')))
    if not files:
        pytest.skip('未找到任何 Markdown 文档')
    return files


def test_env_vars_match_env_example():
    """.env.example 的键集合必须与代码实际读取的 env 集合一致（允许显式例外）"""
    source = _source_env_names()
    active, commented = _env_example_keys()

    # 注释掉的键必须确实以注释形式存在（防止被删掉后只剩 allow-list 空转）
    missing_commented = _ENV_EXAMPLE_COMMENTED - commented
    assert not missing_commented, f'.env.example 缺少这些注释配置: {sorted(missing_commented)}'

    expected = source
    actual = active | _ENV_EXAMPLE_COMMENTED
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    assert not missing, f'.env.example 缺少代码会读取的环境变量: {missing}'
    assert not extra, f'.env.example 含有多余/未使用的环境变量: {extra}'


def test_env_vars_match_configuration_docs():
    """docs/configuration.md 环境变量表必须与代码实际读取的 env 集合一致"""
    source = _source_env_names()
    documented = _documented_env_names()

    missing = sorted(source - documented - _DOCS_ALLOW_MISSING)
    extra = sorted(documented - source)
    assert not missing, f'configuration.md 环境变量表缺少: {missing}'
    assert not extra, f'configuration.md 环境变量表含未使用的键: {extra}'
    assert _DOCS_ALLOW_MISSING <= source, 'allow-list 中的键已不再被代码使用，请清理'


def test_version_in_changelog():
    """pyproject.toml 的版本必须在 CHANGELOG 中有对应日期条目"""
    version_match = _VERSION_RE.search(_read(PYPROJECT))
    assert version_match, 'pyproject.toml 中未找到 version 字段'
    version = version_match.group(1)

    changelog = _read(CHANGELOG)
    heading = re.compile(
        rf'^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}\s*$', re.M)
    assert heading.search(changelog), (
        f'CHANGELOG.md 缺少 "## [{version}] - YYYY-MM-DD" 条目（当前版本未记录）')


def test_relative_markdown_links_resolve():
    """docs/ + README 中的相对 Markdown 链接必须指向存在的文件（防止重命名后死链）"""
    broken = []
    for md_file in _markdown_files():
        text = _mask_fenced_code(_read(md_file))
        for match in _MD_LINK_RE.finditer(text):
            target = match.group(1).strip()
            if target.startswith(('#', 'http://', 'https://', 'mailto:', 'tel:', 'data:')):
                continue
            # 去掉锚点/查询串，解码 %20 等转义
            relative = unquote(target.split('#', 1)[0].split('?', 1)[0])
            if not relative:
                continue
            resolved = (md_file.parent / relative).resolve()
            if not resolved.exists():
                line_no = text[:match.start()].count('\n') + 1
                broken.append(f'{md_file.relative_to(PROJECT_ROOT)}:{line_no} -> {target}')

    assert not broken, '以下相对 Markdown 链接指向不存在的文件:\n' + '\n'.join(broken)
