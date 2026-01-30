import datetime
import time

# 日期时间处理工具类

class DateTimeUtil:

    @staticmethod
    def get_current_timestamp():
        """
        获取当前的时间戳（毫秒）
        """
        return int(time.time() * 1000)

    @staticmethod
    def format_timestamp(timestamp, format_string='%Y-%m-%d %H:%M:%S'):
        """
        将时间戳格式化为指定格式的字符串
        :param timestamp: 毫秒级时间戳
        :param format_string: 格式化字符串，默认为 '%Y-%m-%d %H:%M:%S'
        :return: 格式化后的时间字符串
        """
        return datetime.datetime.fromtimestamp(timestamp / 1000).strftime(format_string)

    @staticmethod
    def parse_string_to_timestamp(date_string, format_string='%Y-%m-%d %H:%M:%S'):
        """
        将符合特定格式的字符串解析为时间戳（毫秒）
        :param date_string: 需要解析的时间字符串
        :param format_string: 解析时间字符串的格式，默认为 '%Y-%m-%d %H:%M:%S'
        :return: 毫秒级时间戳
        """
        return int(time.mktime(time.strptime(date_string, format_string))) * 1000

    @staticmethod
    def add_time_delta(timestamp, days=0, seconds=0, microseconds=0, milliseconds=0, minutes=0, hours=0, weeks=0):
        """
        在给定的时间戳上添加一个时间增量
        :param timestamp: 基础时间戳（毫秒）
        :param days: 天数
        :param seconds: 秒数
        :param microseconds: 微秒数
        :param milliseconds: 毫秒数
        :param minutes: 分钟数
        :param hours: 小时数
        :param weeks: 周数
        :return: 新的时间戳（毫秒）
        """
        delta = datetime.timedelta(days=days, seconds=seconds, microseconds=microseconds, milliseconds=milliseconds,
                                   minutes=minutes, hours=hours, weeks=weeks)
        return int((datetime.datetime.fromtimestamp(timestamp / 1000) + delta).timestamp()) * 1000

# # 使用示例
# if __name__ == "__main__":
#     # 获取当前时间戳
#     current_timestamp = DateTimeUtil.get_current_timestamp()
#     print(f"当前时间戳（毫秒）: {current_timestamp}")
#
#     # 格式化时间戳
#     formatted_time = DateTimeUtil.format_timestamp(current_timestamp)
#     print(f"格式化后的时间: {formatted_time}")
#
#     # 解析字符串到时间戳
#     date_string = "2024-06-03 12:00:00"
#     parsed_timestamp = DateTimeUtil.parse_string_to_timestamp(date_string)
#     print(f"解析字符串得到的时间戳（毫秒）: {parsed_timestamp}")
#
#     # 在时间戳上添加时间增量
#     new_timestamp = DateTimeUtil.add_time_delta(current_timestamp, days=1, hours=2)
#     print(f"添加时间增量后的时间戳（毫秒）: {new_timestamp}")
