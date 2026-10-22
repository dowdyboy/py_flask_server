"""任务自动发现

用法：在本目录下新建 .py 文件，用 `@task` 装饰器声明任务即可，
无需修改任何入口（worker 启动时会自动导入本包下的所有模块）：

    # flask_server/task/email.py
    from flask_server.module.task_queue import task

    @task('send_email', max_retries=5)
    def send_email(to, subject):
        ...

约定：

  - 任务参数必须可 JSON 序列化（队列里传输的是 JSON 信封）
  - handler 必须**幂等**：队列是 at-least-once 语义，崩溃恢复可能重复投递
  - 单个模块导入失败只记 ERROR 并跳过（不影响其它任务），worker 启动日志会列出
    已注册任务，便于发现"任务没生效"
"""

import importlib
import pkgutil

from ..module.task_queue import registry
from ..util import Logger


def auto_import_tasks(package_name='flask_server.task'):
    """导入任务包下的所有模块（模块内 @task 装饰器注册即生效）

    Returns:
        list[str]: 当前已注册的任务名（排序后）
    """
    package = importlib.import_module(package_name)
    for module_info in sorted(pkgutil.iter_modules(package.__path__), key=lambda m: m.name):
        if module_info.name.startswith('_'):
            continue
        try:
            importlib.import_module(f'{package_name}.{module_info.name}')
        except Exception as e:
            Logger.error(f'task module `{module_info.name}` import failed, skipped: {e}',
                         exc_info=True)
    return registry.names


__all__ = ['auto_import_tasks']
