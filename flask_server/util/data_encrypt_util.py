import hashlib
import os

# 数据加密工具类

class DataEncryptUtil:

    @staticmethod
    def sha1(text):
        """
        生成给定文本的SHA-1哈希值

        deprecated: SHA-1 已被证明不安全，建议使用 sha256 或 pbkdf2_hmac

        Args:
            text (str): 需要计算哈希值的原始文本字符串

        Returns:
            str: 返回40个字符的SHA-1哈希十六进制字符串
        """
        sha1_hash = hashlib.sha1()
        sha1_hash.update(text.encode('utf-8'))
        sha1_hex = sha1_hash.hexdigest()
        return sha1_hex

    @staticmethod
    def sha256(text):
        """
        生成给定文本的SHA-256哈希值

        Args:
            text (str): 需要计算哈希值的原始文本字符串

        Returns:
            str: 返回64个字符的SHA-256哈希十六进制字符串
        """
        sha256_hash = hashlib.sha256()
        sha256_hash.update(text.encode('utf-8'))
        return sha256_hash.hexdigest()

    @staticmethod
    def pbkdf2_hmac(text, salt=None, iterations=100000):
        """
        使用PBKDF2-HMAC-SHA256派生密钥，适用于密码存储场景

        Args:
            text (str): 原始密码/文本
            salt (str, optional): 盐值。为None时自动生成16字节随机盐
            iterations (int): 迭代次数，默认100000

        Returns:
            str: "salt$iterations$hash" 格式的字符串，salt 为16字节十六进制，hash 为64字节十六进制
        """
        if salt is None:
            salt = os.urandom(16).hex()
        derived = hashlib.pbkdf2_hmac(
            'sha256', text.encode('utf-8'), bytes.fromhex(salt), iterations
        )
        return f'{salt}${iterations}${derived.hex()}'

    @staticmethod
    def verify_pbkdf2(text, stored):
        """
        校验密码是否与 pbkdf2_hmac 生成的 "salt$iterations$hash" 匹配

        Args:
            text (str): 待校验的原始密码
            stored (str): pbkdf2_hmac 返回的 "salt$iterations$hash" 字符串

        Returns:
            bool: 是否匹配
        """
        try:
            parts = stored.split('$')
            if len(parts) == 3:
                salt, iterations, hash_hex = parts
                iterations = int(iterations)
            elif len(parts) == 2:
                # 兼容旧格式 "salt$hash"（无 iterations，使用默认值）
                salt, hash_hex = parts
                iterations = 100000
            else:
                return False
            # 盐必须是合法 hex；损坏/遗留数据直接视为校验失败，不抛异常（否则登录 500）
            salt_bytes = bytes.fromhex(salt)
            # iterations 上限防御：防止 DB 中存储的哈希被写入超大迭代数（CPU DoS）
            if iterations < 1 or iterations > 1_000_000:
                return False
        except (ValueError, AttributeError, TypeError):
            return False
        derived = hashlib.pbkdf2_hmac(
            'sha256', text.encode('utf-8'), salt_bytes, iterations
        )
        return derived.hex() == hash_hex
