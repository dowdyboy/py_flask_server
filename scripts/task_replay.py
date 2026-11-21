#!/usr/bin/env python
"""死信查看与重放（任务队列运维工具）

用法：
    python scripts/task_replay.py --list                 # 列出死信（默认 20 条）
    python scripts/task_replay.py --list 50              # 列出最近 50 条
    python scripts/task_replay.py --replay 0             # 重放最近一条（下标见 --list）
    python scripts/task_replay.py --replay-all           # 重放全部死信（逐条，先入队后移除）
    python scripts/task_replay.py --replay 0 --dry-run   # 只看会重放什么，不改队列
    python scripts/task_replay.py --queue tasks:email    # 指定 stream

说明：
    - 需要配置 REDIS_URL；内存后端的死信只存在于当前进程，CLI 里看到的永远是空的。
    - 重放 = 用原 name/args/kwargs 重新入队一个新任务，然后从死信列表移除旧记录。
      入队成功、移除前进程崩溃会产生一次重复执行（at-least-once，handler 需幂等）。
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask_server import config                                # noqa: E402
from flask_server.module.task_queue import (                   # noqa: E402
    MemoryTaskQueue, RedisTaskQueue, TaskNotFound, TaskQueueUnavailable,
)


def _build_queue(stream):
    if config.redis_url:
        return RedisTaskQueue(config.redis_url, stream=stream)
    print('[WARN] 未配置 REDIS_URL：使用进程内内存队列，CLI 里看不到业务进程的死信。')
    return MemoryTaskQueue()


def _print_record(index, record):
    payload = json.dumps(record, ensure_ascii=False, default=str)
    if len(payload) > 300:
        payload = payload[:300] + '...'
    print(f'[{index}] {payload}')


def _list(queue, limit):
    records = queue.dead_letters(limit=limit)
    if not records:
        print('死信为空。')
        return 0
    print(f'死信（最新在前，共显示 {len(records)} 条）：')
    for index, record in enumerate(records):
        _print_record(index, record)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='任务死信查看/重放')
    parser.add_argument('--queue', default=None, help=f'队列名/stream（默认 {config.task_queue_stream}）')
    parser.add_argument('--list', dest='list_limit', type=int, nargs='?', const=20, default=None,
                        help='列出死信（默认 20 条）')
    parser.add_argument('--replay', type=int, default=None, metavar='INDEX',
                        help='重放指定下标的死信（下标见 --list）')
    parser.add_argument('--replay-all', action='store_true', help='重放全部死信')
    parser.add_argument('--dry-run', action='store_true', help='只打印不执行')
    args = parser.parse_args(argv)

    stream = args.queue or config.task_queue_stream
    queue = _build_queue(stream)
    try:
        if args.replay is not None:
            records = queue.dead_letters(limit=args.replay + 1)
            if args.replay >= len(records):
                raise TaskNotFound(f'dead letter index {args.replay}')
            record = records[args.replay]
            if args.dry_run:
                print('将重放：')
                _print_record(args.replay, record)
                return 0
            task_id = queue.replay_dead(args.replay)
            print(f'已重放入队：index={args.replay} task_id={task_id}')
            return 0

        if args.replay_all:
            replayed = 0
            while queue.dead_letters(limit=1):
                record = queue.dead_letters(limit=1)[0]
                if args.dry_run:
                    _print_record(0, record)
                    # dry-run 时无法推进队列，最多打印 --list 默认数量
                    if replayed >= 20:
                        print('（dry-run 最多展示 20 条）')
                        break
                    replayed += 1
                    continue
                task_id = queue.replay_dead(0)
                replayed += 1
                print(f'已重放 {replayed} 条，最新 task_id={task_id}')
            print(f'完成：重放 {replayed} 条。')
            return 0

        return _list(queue, args.list_limit or 20)
    except TaskNotFound as e:
        print(f'[ERROR] {e}')
        return 1
    except ValueError as e:
        print(f'[ERROR] 该死信无法重放（记录损坏/缺少任务名）：{e}')
        return 1
    except TaskQueueUnavailable as e:
        print(f'[ERROR] 队列不可用：{e}')
        return 2
    finally:
        queue.close()


if __name__ == '__main__':
    sys.exit(main())
