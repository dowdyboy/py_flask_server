"""benchmark 脚本测试：参数解析与统计纯函数"""

import math
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'scripts'))

from benchmark import parse_args, summarize   # noqa: E402


def test_parse_args_defaults():
    args = parse_args([])
    assert args.url == 'http://127.0.0.1:5000'
    assert args.concurrency == 10
    assert args.requests == 1000
    assert args.duration == 0


def test_parse_args_custom():
    args = parse_args(['--url', 'http://x:5001', '--concurrency', '20',
                       '--requests', '500', '--duration', '5'])
    assert args.url == 'http://x:5001'
    assert args.concurrency == 20
    assert args.requests == 500
    assert args.duration == 5


def test_summarize_empty():
    r = summarize([], 0, 1.0)
    assert r['qps'] == 0.0
    assert r['requests'] == 0
    assert r['errors'] == 0


def test_summarize_computes_percentiles():
    # 1..100 ms 的延迟
    latencies = list(range(1, 101))
    r = summarize(latencies, 3, 10.0)
    assert r['requests'] == 100
    assert r['errors'] == 3
    assert r['avg_ms'] == 50.5
    assert r['median_ms'] == 50.5
    assert r['p95_ms'] == 95.0
    assert r['p99_ms'] == 99.0
    assert r['qps'] == 10.0


def _nearest_rank(values, p):
    """nearest-rank 百分位定义：ceil(n*p)-1，越界钳制到最后一个样本"""
    ordered = sorted(values)
    index = min(math.ceil(len(ordered) * p) - 1, len(ordered) - 1)
    return float(ordered[max(index, 0)])


def test_summarize_small_sample_no_index_error():
    """R5 回归：小样本（1-10 个）时 p95/p99 按 nearest-rank 取值且不越界

    旧实现用 int(n*p)-1：n=2 时 p95 退化为最小值、n=10 时取第 9 个，
    系统性低估尾部延迟。这里按 nearest-rank 语义断言精确值。
    """
    for n in (1, 2, 5, 10):
        samples = list(range(n))
        r = summarize(samples, 0, 1.0)
        assert r['p95_ms'] == _nearest_rank(samples, 0.95), n
        assert r['p99_ms'] == _nearest_rank(samples, 0.99), n

    # 固定值钉住 nearest-rank（旧公式 n=2 得 0、n=10 得 8，均为错误值）
    assert summarize([0, 1], 0, 1.0)['p95_ms'] == 1.0
    assert summarize([0, 1], 0, 1.0)['p99_ms'] == 1.0
    small = summarize(list(range(10)), 0, 1.0)
    assert small['p95_ms'] == 9.0
    assert small['p99_ms'] == 9.0
