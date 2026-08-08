"""benchmark 脚本测试：参数解析与统计纯函数"""

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


def test_summarize_small_sample_no_index_error():
    """R5 回归：小样本（1-10 个）时 p95/p99 索引不越界"""
    for n in (1, 2, 5, 10):
        r = summarize(list(range(n)), 0, 1.0)
        assert r['p95_ms'] >= 0
        assert r['p99_ms'] >= 0
