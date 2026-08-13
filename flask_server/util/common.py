

from typing import Optional


# 简单通用工具类


class CommonUtil:

    @staticmethod
    def mask_uri(uri: Optional[str]) -> Optional[str]:
        """
        脱敏连接字符串中的密码部分，避免明文密码写入日志

        支持两种常见格式：
            scheme://user:password@host
            scheme://:password@host     (空用户名)
        无密码的 URI 原样返回

        注意：密码本身可含 '@'（如 p@ssw0rd），用贪婪匹配脱敏到凭据分隔符
        （user:pass 与 host 之间的最后一个 '@'）；'@' 出现在 path/query 中时
        不受影响（密码段只到第一个 / ? # 为止）。

        Args:
            uri (str): 原始连接字符串，如 mysql+pymysql://user:pass@host/db 或 redis://:pass@host:6379/0

        Returns:
            str: 密码替换为 *** 的脱敏字符串；uri 为 None 时返回 None
        """
        if uri is None:
            return None
        import re
        return re.sub(r'(://[^:/@]*:)[^/?#]*(@)', r'\1***\2', uri)

    @staticmethod
    def dict_map(obj: dict,
                 mapper: Optional[dict] = None,
                 mapper_list: Optional[list] = None,
                 only: bool = True) -> dict:


        """
        根据映射规则转换字典的键名

        Args:
            obj (dict): 原始字典对象
            mapper (dict, optional): 键名映射规则字典，格式为 {原键名: 新键名}。默认为None
            mapper_list (list, optional): 需要保留的键名列表，会自动转换为 {原键名: 原键名} 的映射。默认为None
            only (bool, optional): 是否仅保留映射规则中指定的键。True时只保留映射键，False时保留所有键。默认为True

        Returns:
            dict: 转换后的新字典对象，包含映射后的键名和原值
        """
        if mapper is None and mapper_list is None:
            return obj
        if mapper is None:
            mapper = dict()
        if mapper_list is not None:
            # 拷贝后再增补，避免修改调用方传入的 mapper
            mapper = dict(mapper)
            for k in mapper_list:
                mapper[k] = k
        new_obj = dict()
        for k in obj.keys():
            v = obj[k]
            if only and k not in mapper.keys():
                continue
            if k in mapper.keys():
                new_obj[mapper[k]] = v
            else:
                new_obj[k] = v
        return new_obj

    @staticmethod
    def obj_to_dict(obj, _seen=None):
        """
        将自定义对象及其嵌套对象递归转换为字典。
        包含循环引用检测，防止自引用对象导致 RecursionError。

        :param obj: 要转换的自定义对象
        :param _seen: 内部使用的已访问对象集合，防止循环引用
        :return: 转换后的字典
        """
        # 如果是None，直接返回None
        if obj is None:
            return None

        # 如果是基本数据类型，直接返回
        if isinstance(obj, (int, float, str, bool)):
            return obj

        # datetime/date 转 ISO 8601 字符串（比 str() 的本地格式更规范）
        import datetime as _dt
        if isinstance(obj, (_dt.datetime, _dt.date)):
            return obj.isoformat()

        # 如果是列表，递归转换列表中的每个元素
        if isinstance(obj, list):
            if _seen is None:
                _seen = set()
            obj_id = id(obj)
            if obj_id in _seen:
                return None
            _seen.add(obj_id)
            result = [CommonUtil.obj_to_dict(item, _seen) for item in obj]
            _seen.discard(obj_id)
            return result

        # 如果是字典，递归转换字典中的每个值
        if isinstance(obj, dict):
            if _seen is None:
                _seen = set()
            obj_id = id(obj)
            if obj_id in _seen:
                return None
            _seen.add(obj_id)
            result = {key: CommonUtil.obj_to_dict(value, _seen) for key, value in obj.items()}
            _seen.discard(obj_id)
            return result

        # 如果是自定义对象，获取对象的所有属性，递归转换
        if hasattr(obj, '__dict__'):
            if _seen is None:
                _seen = set()
            obj_id = id(obj)
            if obj_id in _seen:
                return None
            _seen.add(obj_id)
            result = {}
            for key, value in obj.__dict__.items():
                if key.startswith('_'):
                    continue  # 忽略私有属性
                result[key] = CommonUtil.obj_to_dict(value, _seen)
            _seen.discard(obj_id)
            return result

        # 如果是其他类型，无法处理，返回其字符串表示
        return str(obj)

    @staticmethod
    def get_real_ip(request, trusted_proxies: Optional[list] = None) -> str:
        """
        从HTTP请求中获取客户端的真实IP地址

        Args:
            request (flask.Request): Flask请求对象，包含请求头信息
            trusted_proxies (list, optional): 可信代理IP列表。仅在请求来自可信代理时
                才信任 X-Forwarded-For，防止客户端伪造IP。默认为 None，
                此时使用配置的 TRUSTED_PROXIES（默认 127.0.0.1,::1）。
                支持精确 IP 与 CIDR 前缀（如 172.16.0.0/12，覆盖 Docker 网关网段）

        Returns:
            str: 客户端的真实IP地址
        """
        if trusted_proxies is None:
            from ..config import config
            trusted_proxies = config.trusted_proxies
        # 仅当请求确实来自可信代理时，才信任 X-Forwarded-For（取第一个非空条目，
        # 防 `, 1.2.3.4` 这类空首项把 IP 归一为空串）
        if _is_trusted_addr(request.remote_addr, trusted_proxies) and 'X-Forwarded-For' in request.headers:
            xff = request.headers.get('X-Forwarded-For')
            user_ip = next((p.strip() for p in xff.split(',') if p.strip()), None)
            if user_ip is None:
                user_ip = request.remote_addr
        else:
            user_ip = request.remote_addr
        return user_ip


def _is_trusted_addr(remote_addr, trusted_proxies) -> bool:
    """判断来源地址是否在可信代理列表中（支持精确 IP 与 CIDR 前缀；非法条目忽略）"""
    if not remote_addr:
        return False
    import ipaddress
    try:
        remote = ipaddress.ip_address(remote_addr)
    except ValueError:
        # 非 IP（如 unix socket 占位符），按精确字符串匹配兜底
        return remote_addr in (trusted_proxies or [])
    for item in trusted_proxies or []:
        try:
            if '/' in item:
                if remote in ipaddress.ip_network(item, strict=False):
                    return True
            elif remote == ipaddress.ip_address(item):
                return True
        except ValueError:
            continue   # 忽略配置中的非法条目
    return False



