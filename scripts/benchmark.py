#!/usr/bin/env python
"""HTTP 性能基准脚本（简单并发压测）

用法：
    # 先启动服务：python server.py
    python scripts/benchmark.py                                          # 默认参数
    python scripts/benchmark.py --url http://127.0.0.1:5000 \
        --endpoints /api/v1/healthz,/hello \
        --concurrency 20 --requests 2000
    python scripts/benchmark.py --duration 10                            # 时间模式

输出：QPS、平均/中位数/P95/P99 延迟、错误数。
仅依赖 requests（已含在 requirements.txt）。
"""

import argparse
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

DEFAULT_ENDPOINTS = ['/api/v1/healthz', '/hello']


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description='HTTP 性能基准')
    parser.add_argument('--url', default='http://127.0.0.1:5000', help='服务地址')
    parser.add_argument('--endpoints', default=','.join(DEFAULT_ENDPOINTS),
                        help='压测路径，逗号分隔（默认 healthz,hello）')
    parser.add_argument('--concurrency', type=int, default=10, help='并发数（默认 10）')
    parser.add_argument('--requests', type=int, default=1000, help='总请求数（默认 1000）')
    parser.add_argument('--duration', type=int, default=0,
                        help='时间模式（秒）：按时长压测，忽略 --requests')
    return parser.parse_args(argv)


def summarize(latencies, errors, elapsed):
    total = len(latencies)
    if total == 0:
        return {'qps': 0.0, 'avg_ms': 0.0, 'median_ms': 0.0,
                'p95_ms': 0.0, 'p99_ms': 0.0, 'requests': 0, 'errors': errors}
    latencies.sort()
    # 小样本保护：p95/p99 索引可能越界（如仅 1 个样本时 int(0.95*1)-1 = -1）
    p95 = latencies[min(int(total * 0.95) - 1, total - 1)]
    p99 = latencies[min(int(total * 0.99) - 1, total - 1)]
    return {
        'qps': round(total / elapsed, 1) if elapsed > 0 else 0.0,
        'avg_ms': round(statistics.mean(latencies), 2),
        'median_ms': round(statistics.median(latencies), 2),
        'p95_ms': round(p95, 2),
        'p99_ms': round(p99, 2),
        'requests': total,
        'errors': errors,
    }


def run_benchmark(url, endpoints, concurrency, total_requests, duration):
    stop_event = threading.Event()
    latencies = []
    errors = [0]
    lock = threading.Lock()

    def worker():
        while not stop_event.is_set():
            for path in endpoints:
                target = f'{url}{path}'
                start = time.perf_counter()
                try:
                    resp = requests.get(target, timeout=10)
                    if resp.status_code >= 500:
                        with lock:
                            errors[0] += 1
                except Exception:
                    with lock:
                        errors[0] += 1
                with lock:
                    latencies.append((time.perf_counter() - start) * 1000)

    start_time = time.time()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(worker) for _ in range(concurrency)]
        if duration > 0:
            time.sleep(duration)
            stop_event.set()
        else:
            # 请求数模式：主线程等待完成量
            while len(latencies) < total_requests:
                if time.time() - start_time > 600:   # 10 分钟上限，防止卡死
                    break
                time.sleep(0.05)
            stop_event.set()
        for f in futures:
            f.result()
    elapsed = time.time() - start_time

    return summarize(latencies, errors[0], elapsed)


def main():
    args = parse_args()
    endpoints = [e.strip() for e in args.endpoints.split(',') if e.strip()]
    print(f'[benchmark] url={args.url} endpoints={endpoints} '
          f'concurrency={args.concurrency} requests={args.requests} '
          f'duration={args.duration}s')
    result = run_benchmark(args.url, endpoints, args.concurrency, args.requests, args.duration)
    print(f'[result] QPS={result["qps"]}  avg={result["avg_ms"]}ms  '
          f'p50={result["median_ms"]}ms  p95={result["p95_ms"]}ms  '
          f'p99={result["p99_ms"]}ms  请求数={result["requests"]}  错误={result["errors"]}')


if __name__ == '__main__':
    main()
