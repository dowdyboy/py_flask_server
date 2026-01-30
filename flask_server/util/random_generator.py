import random
import string


# 随机数生成工具类


class RandomGenerator:

    def __init__(self):
        pass

    @staticmethod
    def random_integer(min_val=0, max_val=100):
        """生成指定范围内的随机整数"""
        return random.randint(min_val, max_val)

    @staticmethod
    def random_string(length=10):
        """生成指定长度的随机字符串"""
        letters = string.ascii_letters
        return ''.join(random.choice(letters) for i in range(length))

    @staticmethod
    def random_float(min_val=0.0, max_val=1.0):
        """生成指定范围内的随机浮点数"""
        return random.uniform(min_val, max_val)
