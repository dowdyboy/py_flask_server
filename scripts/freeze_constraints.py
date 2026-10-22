#!/usr/bin/env python
"""从**当前已安装环境**导出运行时依赖的完整锁定版本（constraints.txt）。

为什么需要：`requirements.txt` 只写了下界（`Flask>=3.1.2,<4.0`），CI 与生产每次装出来的
版本可能不同（`flask-smorest 0.42` 与 `0.47` 行为就有差异），线上问题难以复现。

用法：
    # 在一个干净环境里装好运行时依赖（不要装 dev 依赖，避免把 pytest 等锁进去）
    pip install -r requirements.txt
    python scripts/freeze_constraints.py            # 生成/覆盖 constraints.txt

    # 之后用它锁定安装（CI 与生产）
    pip install -r requirements.txt -c constraints.txt

只会收录 requirements.txt 中直接依赖及其传递依赖的闭包，不会把环境里其它包写进去。
"""

import argparse
import os
import sys
from importlib.metadata import PackageNotFoundError, version

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEADER = """\
# 运行时依赖锁定（constraints）：只锁定版本，不改变依赖关系
# 生成方式：python scripts/freeze_constraints.py（需先 pip install -r requirements.txt）
# 使用方式：pip install -r requirements.txt -c constraints.txt
# 升级依赖时：改 requirements.txt → 重装 → 重新生成本文件 → 跑全量测试
# 注：平台相关的传递依赖（如 Windows 才用的 colorama）会一并锁定，
#     其它平台安装时不会被用到，对安装结果无影响。
"""


def _requirement_name(spec):
    """从 PEP 508 需求串里取包名（去掉 extras / 版本区间 / marker）"""
    from packaging.requirements import Requirement
    return Requirement(spec).name


def _direct_requirements():
    """读取 requirements.txt 中的直接依赖声明（跳过注释/空行）"""
    path = os.path.join(PROJECT_ROOT, 'requirements.txt')
    with open(path, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f
                if line.strip() and not line.strip().startswith('#')]


def _runtime_closure(direct_specs):
    """计算依赖闭包（只保留当前平台上真正生效的依赖）

    返回 {规范化包名: 展示用包名}：包名大小写在不同依赖声明里可能不一致
    （`Flask` / `flask`、`MarkupSafe` / `markupsafe`），需按 PEP 503 规范化去重，
    否则会生成两行重复的约束。
    """
    from importlib.metadata import requires

    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    names = {}          # canonical -> 展示名

    def _add(name):
        key = canonicalize_name(name)
        names.setdefault(key, name)
        return key

    for spec in direct_specs:
        _add(_requirement_name(spec))

    pending = list(names)
    processed = set()
    while pending:
        key = pending.pop()
        if key in processed:
            continue
        processed.add(key)
        display = names[key]
        try:
            deps = requires(display) or []
        except PackageNotFoundError:
            print(f'[WARN] 未安装：{display}（跳过其传递依赖）', file=sys.stderr)
            continue
        for raw in deps:
            try:
                req = Requirement(raw)
            except Exception:      # 非法/带环境标记的需求串，跳过
                continue
            if req.marker is not None and not req.marker.evaluate():
                continue           # 当前平台/版本不适用
            dep_key = _add(req.name)
            if dep_key not in processed:
                pending.append(dep_key)
    return names


def main():
    parser = argparse.ArgumentParser(description='导出运行时依赖锁定文件')
    parser.add_argument('--output', default=os.path.join(PROJECT_ROOT, 'constraints.txt'))
    args = parser.parse_args()

    direct = _direct_requirements()
    names = _runtime_closure(direct)

    lines = []
    for key in sorted(names, key=str.lower):
        display = names[key]
        try:
            lines.append(f'{display}=={version(display)}')
        except PackageNotFoundError:
            print(f'[WARN] 未安装，未锁定：{display}', file=sys.stderr)

    with open(args.output, 'w', encoding='utf-8', newline='\n') as f:
        f.write(HEADER)
        f.write('\n'.join(lines))
        f.write('\n')

    print(f'[OK] 已写入 {args.output}（{len(lines)} 个包）')


if __name__ == '__main__':
    main()
