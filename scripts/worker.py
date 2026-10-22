#!/usr/bin/env python
"""任务队列消费者（独立进程）

用法：
    python scripts/worker.py                        # 默认并发 4
    python scripts/worker.py --concurrency 8        # 指定并发
    python scripts/worker.py --queue tasks:email    # 指定队列（不同业务用不同 stream）
    python scripts/worker.py --once                 # 处理完当前可立即取到的任务即退出

部署建议：
    - 与 Web 进程**分开**起（worker 能被独立扩容/重启，不占 Web 线程）
    - 多实例直接起多个进程即可：consumer group 会保证同一任务只被一个 worker 取走
    - 必须配置 REDIS_URL，否则每个进程只消费自己进程内入队的任务（内存后端，
      `--once` 在这种模式下基本取不到任务——单进程联调请用 TASK_WORKER_EMBEDDED=true）
    - 容器里用 `stop_grace_period` 给足时间：收到 SIGTERM 后不再取新任务，等在途任务完成
"""

import argparse
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask_server import app, config                      # noqa: E402
from flask_server.module.task_queue import (             # noqa: E402
    MemoryTaskQueue, RedisTaskQueue, TaskQueueUnavailable,
)
from flask_server.module.task_worker import TaskWorker   # noqa: E402
from flask_server.task import auto_import_tasks          # noqa: E402
from flask_server.util import Logger                     # noqa: E402


def _build_queue(stream):
    """按配置/参数构建队列：有 REDIS_URL 用 Redis，否则进程内内存"""
    if config.redis_url:
        return RedisTaskQueue(config.redis_url, stream=stream)
    Logger.warn('REDIS_URL 未配置：使用进程内内存队列（仅单进程可用，进程退出任务即丢）。'
                '生产环境请配置 REDIS_URL。')
    return MemoryTaskQueue()


def main(argv=None):
    parser = argparse.ArgumentParser(description='任务队列消费者')
    parser.add_argument('--concurrency', type=int, default=None,
                        help=f'并发消费线程数（默认 {config.task_worker_concurrency}）')
    parser.add_argument('--queue', default=None,
                        help=f'队列名/stream（默认 {config.task_queue_stream}）')
    parser.add_argument('--once', action='store_true',
                        help='只处理当前可立即取到的任务后退出（联调/定时触发用）')
    args = parser.parse_args(argv)

    stream = args.queue or config.task_queue_stream
    concurrency = args.concurrency or config.task_worker_concurrency

    # 1) 发现任务（flask_server/task/ 下建文件即注册）
    names = auto_import_tasks()
    if not names:
        Logger.error('未注册任何任务：请在 flask_server/task/ 下用 @task 声明任务')
        print('没有可用任务，退出。')
        return 1
    print(f'已注册任务（{len(names)}）：' + ', '.join(names))

    # 2) 构建队列与 worker
    queue = _build_queue(stream)
    worker = TaskWorker(app=app, queue=queue, concurrency=concurrency)
    print(f'队列后端：{type(queue).__name__}（stream={stream}）')
    print(f'并发：{concurrency}  环境：{config.app_env}')

    # 3) 信号：优雅停止（容器 stop / Ctrl+C）
    def _handle(signum, frame):
        Logger.info(f'worker 收到信号 {signum}，准备停止（等在途任务完成）')
        worker.stop()
        queue.close()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)

    # 4) 运行
    if args.once:
        try:
            worker.start()
            while True:
                reserved = queue.reserve(timeout=0.3)
                if reserved is None:
                    break
                worker._handle(reserved)      # noqa: SLF001 - 单次模式复用同一执行路径
        except TaskQueueUnavailable as e:
            Logger.error(f'队列不可用: {e}')
            return 1
        finally:
            worker.stop()
        return 0

    try:
        worker.run_forever()
    except TaskQueueUnavailable as e:
        Logger.error(f'队列不可用，退出: {e}')
        return 1
    finally:
        queue.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
