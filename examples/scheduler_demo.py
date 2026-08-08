# APScheduler 定时任务示例（教学参考，不在工程运行路径内）
#
# 启用步骤：
#   1. pip install APScheduler
#   2. 将下方代码合并到 flask_server/app.py（或独立模块并在 __init__.py 导入）
#   3. 注意事项：
#      - gunicorn 多 worker 下每个进程都会运行调度器 → 任务会重复执行；
#        生产环境建议单 worker 运行调度器，或用分布式锁（如 Redis SETNX）保证单次执行
#
# 接入工程：将下方代码放入 flask_server/module/scheduler.py，
# 并在 flask_server/__init__.py 中 `from .module.scheduler import start_scheduler` 调用。

from apscheduler.schedulers.background import BackgroundScheduler
from flask_server.util import Logger


def _daily_cleanup_job():
    """示例任务：每天清理过期缓存键（memory_cache 已有后台清理线程，此处演示自定义任务）"""
    Logger.info('scheduler: daily cleanup job running')
    # 在这里写你的业务逻辑，如：
    #   - 清理过期临时文件
    #   - 归档旧日志
    #   - 汇总统计数据


_scheduler = None


def start_scheduler():
    """启动后台调度器（幂等）"""
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(timezone='Asia/Shanghai')
    _scheduler.add_job(_daily_cleanup_job, 'cron', hour=3, minute=0,
                       id='daily_cleanup', replace_existing=True)
    # 其他示例：
    # _scheduler.add_job(job_fn, 'interval', minutes=5, id='interval_job')
    _scheduler.start()
    Logger.info('scheduler started (APScheduler)')


def stop_scheduler():
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
