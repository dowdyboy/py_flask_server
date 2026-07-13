import random
import string
import secrets


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
        """
        生成指定长度的随机字符串

        deprecated: 非密码学安全，token/密钥场景请使用 secrets_token
        """
        letters = string.ascii_letters
        return ''.join(random.choice(letters) for _ in range(length))

    @staticmethod
    def random_float(min_val=0.0, max_val=1.0):
        """生成指定范围内的随机浮点数"""
        return random.uniform(min_val, max_val)

    @staticmethod
    def secrets_token(length=32):
        """
        生成密码学安全的随机令牌字符串

        Args:
            length (int): 令牌长度，默认32

        Returns:
            str: 由字母和数字组成的随机字符串
        """
        alphabet = string.ascii_letters + string.digits
        return ''.join(secrets.choice(alphabet) for _ in range(length))
