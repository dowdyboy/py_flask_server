from flask_server.util.date_time_util import DateTimeUtil


def test_get_current_timestamp():
    ts = DateTimeUtil.get_current_timestamp()
    assert isinstance(ts, int)
    assert ts > 0


def test_format_timestamp():
    # 1719705600000 毫秒 = 2024-06-30 08:00:00 UTC；按本地时区格式化结构应合理
    ts = 1719705600000
    s = DateTimeUtil.format_timestamp(ts)
    assert isinstance(s, str)
    assert len(s) == 19


def test_format_timestamp_custom_format():
    ts = 1719705600000
    s = DateTimeUtil.format_timestamp(ts, '%Y/%m/%d')
    assert len(s) == 10


def test_parse_string_to_timestamp():
    ts = DateTimeUtil.parse_string_to_timestamp('2024-06-30 12:00:00')
    assert isinstance(ts, int)
    assert ts > 0


def test_add_time_delta():
    ts = DateTimeUtil.parse_string_to_timestamp('2024-06-30 12:00:00')
    new_ts = DateTimeUtil.add_time_delta(ts, days=1)
    assert new_ts - ts == 24 * 60 * 60 * 1000


def test_add_time_delta_hours():
    ts = DateTimeUtil.parse_string_to_timestamp('2024-06-30 12:00:00')
    new_ts = DateTimeUtil.add_time_delta(ts, hours=2)
    assert new_ts - ts == 2 * 60 * 60 * 1000


def test_utc_now_str():
    s = DateTimeUtil.utc_now_str()
    assert isinstance(s, str)
    assert len(s) == 19


def test_format_timestamp_utc():
    # 1719705600000 ms = 2024-06-30 00:00:00 UTC（固定值，不依赖本地时区）
    s = DateTimeUtil.format_timestamp_utc(1719705600000)
    assert s == '2024-06-30 00:00:00'
