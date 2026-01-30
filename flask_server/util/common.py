

# 简单通用工具类


class CommonUtil:

    @staticmethod
    def dict_map(obj: dict,
                 mapper: dict = None,
                 mapper_list: list = None,
                 only=True):
        
        
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
    def obj_to_dict(obj):
        """
        将自定义对象及其嵌套对象递归转换为字典。

        :param obj: 要转换的自定义对象
        :return: 转换后的字典
        """
        
        # 如果是None，直接返回None
        if obj is None:
            return None

        # 如果是基本数据类型，直接返回
        if isinstance(obj, (int, float, str, bool)):
            return obj

        # 如果是列表，递归转换列表中的每个元素
        if isinstance(obj, list):
            return [CommonUtil.obj_to_dict(item) for item in obj]

        # 如果是字典，递归转换字典中的每个值
        if isinstance(obj, dict):
            return {key: CommonUtil.obj_to_dict(value) for key, value in obj.items()}

        # 如果是自定义对象，获取对象的所有属性，递归转换
        if hasattr(obj, '__dict__'):
            result = {}
            for key, value in obj.__dict__.items():
                if key.startswith('_'):
                    continue  # 忽略私有属性
                result[key] = CommonUtil.obj_to_dict(value)
            return result

        # 如果是其他类型，无法处理，返回其字符串表示
        return str(obj)

    @staticmethod
    def get_real_ip(request):
        """
        从HTTP请求中获取客户端的真实IP地址
        
        Args:
            request (flask.Request): Flask请求对象，包含请求头信息
        
        Returns:
            str: 客户端的真实IP地址。优先从X-Forwarded-For头部获取(当存在代理时)，
                否则返回remote_addr
        
        Note:
            - X-Forwarded-For头部可能包含多个IP地址(逗号分隔)，通常第一个是客户端真实IP
            - 适用于代理服务器(如Nginx)转发的请求场景
        """

        # X-Forwarded-For可以包含多个IP地址，通常第一个是客户端的真实IP
        if 'X-Forwarded-For' in request.headers:
            user_ip = request.headers.get('X-Forwarded-For').split(',')[0]
        else:
            user_ip = request.remote_addr
        return user_ip



