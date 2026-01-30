import hashlib

# 数据加密工具类

class DataEncryptUtil:

    @staticmethod
    def sha1(text):
        """
        生成给定文本的SHA-1哈希值
        
        Args:
            text (str): 需要计算哈希值的原始文本字符串
        
        Returns:
            str: 返回40个字符的SHA-1哈希十六进制字符串
        """
        sha1_hash = hashlib.sha1()
        sha1_hash.update(text.encode('utf-8'))
        sha1_hex = sha1_hash.hexdigest()
        return sha1_hex


