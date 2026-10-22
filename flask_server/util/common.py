

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

        X-Forwarded-For 解析规则（从右往左，跳过可信代理）：
            主流反向代理（nginx `$proxy_add_x_forwarded_for`、Traefik 等）采用**追加**
            语义，头形如 `客户端伪造值, ..., 上游代理逐跳追加`。因此**最右项**才是最近
            一跳代理真正看到的地址，**最左项由客户端提供、可任意伪造**。
            历史实现取最左非空项，攻击者每个请求换一个假 XFF 即可绕过基于 IP 的限流、
            污染审计日志；现改为从右向左取第一个"非可信代理且为合法 IP"的条目。
            （仅当 XFF 全部为可信代理时回退到最左项；解析不出合法 IP 时回退 remote_addr）
        """
        if trusted_proxies is None:
            from ..config import config
            trusted_proxies = config.trusted_proxies
        if not _is_trusted_addr(request.remote_addr, trusted_proxies) \
                or 'X-Forwarded-For' not in request.headers:
            return request.remote_addr
        xff = request.headers.get('X-Forwarded-For') or ''
        hops = [p.strip() for p in xff.split(',') if p.strip()]
        if not hops:
            return request.remote_addr
        for hop in reversed(hops):
            if _is_trusted_addr(hop, trusted_proxies):
                continue   # 内层可信代理追加的条目，继续向左找真实客户端
            if _is_ip_literal(hop):
                return hop
            # 非法 IP 字面量（脚本/扫描器注入的垃圾值）：跳过，不参与判定
        # XFF 全部为可信代理（纯内网多级代理）或全部非法：回退最左/remote_addr
        return hops[0] if all(_is_trusted_addr(h, trusted_proxies) for h in hops) else request.remote_addr


def _is_ip_literal(value) -> bool:
    """是否为合法的 IP 字面量（IPv4/IPv6）；用于过滤 X-Forwarded-For 中的垃圾值"""
    if not value:
        return False
    import ipaddress
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


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



