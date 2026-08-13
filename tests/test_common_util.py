from flask_server.util.common import CommonUtil


def test_obj_to_dict_none():
    assert CommonUtil.obj_to_dict(None) is None


def test_obj_to_dict_primitives():
    assert CommonUtil.obj_to_dict(1) == 1
    assert CommonUtil.obj_to_dict('s') == 's'
    assert CommonUtil.obj_to_dict(3.14) == 3.14
    assert CommonUtil.obj_to_dict(True) is True


def test_obj_to_dict_list():
    assert CommonUtil.obj_to_dict([1, 2, 3]) == [1, 2, 3]


def test_obj_to_dict_dict():
    assert CommonUtil.obj_to_dict({'a': 1, 'b': {'c': 2}}) == {'a': 1, 'b': {'c': 2}}


def test_obj_to_dict_object():
    class Foo:
        def __init__(self):
            self.a = 1
            self._hidden = 2
    f = Foo()
    d = CommonUtil.obj_to_dict(f)
    assert d == {'a': 1}


def test_obj_to_dict_circular_reference():
    """自引用对象不应导致 RecursionError"""
    class Node:
        def __init__(self):
            self.value = 1
            self.next = None
    n = Node()
    n.next = n   # 循环引用
    d = CommonUtil.obj_to_dict(n)
    assert d['value'] == 1
    # next 指向自身（已访问），应返回 None 而非无限递归
    assert d['next'] is None


def test_obj_to_dict_nested_circular():
    """间接循环引用"""
    class A:
        pass

    class B:
        pass

    a = A()
    b = B()
    a.b = b
    b.a = a   # a -> b -> a 循环
    d = CommonUtil.obj_to_dict(a)
    assert d['b']['a'] is None   # 回到 a 时被截断


def test_obj_to_dict_list_self_reference():
    """列表自引用不应导致 RecursionError（_seen 截断为 None）"""
    lst = [1]
    lst.append(lst)
    d = CommonUtil.obj_to_dict(lst)
    assert d[0] == 1
    assert d[1] is None


def test_obj_to_dict_dict_cycle():
    """字典循环引用（dict _seen 分支）"""
    d = {'k': 1}
    d['self'] = d
    out = CommonUtil.obj_to_dict(d)
    assert out['k'] == 1
    assert out['self'] is None


def test_dict_map_no_mapper():
    obj = {'a': 1, 'b': 2}
    assert CommonUtil.dict_map(obj) == obj


def test_dict_map_mapper_list_only():
    obj = {'a': 1, 'b': 2, 'c': 3}
    out = CommonUtil.dict_map(obj, mapper_list=['a', 'b'])
    assert out == {'a': 1, 'b': 2}


def test_dict_map_rename():
    obj = {'a': 1, 'b': 2}
    out = CommonUtil.dict_map(obj, mapper={'a': 'x'})
    assert out == {'x': 1}


def test_dict_map_not_only():
    obj = {'a': 1, 'b': 2}
    out = CommonUtil.dict_map(obj, mapper={'a': 'x'}, only=False)
    assert out == {'x': 1, 'b': 2}


class _FakeHeaders:
    def __init__(self, data=None):
        self._data = data or {}

    def get(self, k):
        return self._data.get(k)

    def __contains__(self, k):
        return k in self._data


class _FakeReq:
    def __init__(self, headers=None, remote_addr='9.9.9.9'):
        self.headers = headers
        self.remote_addr = remote_addr


def test_get_real_ip_forwarded_from_trusted_proxy():
    """来自可信代理时信任 X-Forwarded-For"""
    req = _FakeReq(_FakeHeaders({'X-Forwarded-For': '1.2.3.4, 5.6.7.8'}), remote_addr='127.0.0.1')
    assert CommonUtil.get_real_ip(req) == '1.2.3.4'


def test_get_real_ip_forwarded_ignored_from_untrusted():
    """非可信来源携带 X-Forwarded-For 时应忽略，防止伪造IP"""
    req = _FakeReq(_FakeHeaders({'X-Forwarded-For': '1.2.3.4'}))
    assert CommonUtil.get_real_ip(req) == '9.9.9.9'


def test_get_real_ip_skips_empty_xff_entries():
    """X-Forwarded-For 首项为空（`, 1.2.3.4`）时应取第一个非空条目，而非空串"""
    req = _FakeReq(_FakeHeaders({'X-Forwarded-For': ' , 1.2.3.4'}), remote_addr='127.0.0.1')
    assert CommonUtil.get_real_ip(req) == '1.2.3.4'


def test_get_real_ip_xff_all_empty_falls_back_to_remote():
    """X-Forwarded-For 全部为空时回退到 remote_addr"""
    req = _FakeReq(_FakeHeaders({'X-Forwarded-For': ' , '}), remote_addr='127.0.0.1')
    assert CommonUtil.get_real_ip(req) == '127.0.0.1'


def test_get_real_ip_remote():
    req = _FakeReq(_FakeHeaders({}))
    assert CommonUtil.get_real_ip(req) == '9.9.9.9'


def test_get_real_ip_cidr_trusted():
    """CIDR 前缀匹配的可信代理也应信任 X-Forwarded-For（Docker 网关网段场景）"""
    req = _FakeReq(_FakeHeaders({'X-Forwarded-For': '1.2.3.4'}), remote_addr='172.17.0.1')
    assert CommonUtil.get_real_ip(req, ['127.0.0.1', '172.16.0.0/12']) == '1.2.3.4'


def test_get_real_ip_cidr_untrusted():
    """CIDR 网段之外的来源仍不信任 X-Forwarded-For"""
    req = _FakeReq(_FakeHeaders({'X-Forwarded-For': '1.2.3.4'}), remote_addr='10.0.0.1')
    assert CommonUtil.get_real_ip(req, ['127.0.0.1', '172.16.0.0/12']) == '10.0.0.1'


def test_get_real_ip_invalid_proxy_entry_ignored():
    """可信代理列表中的非法条目应被忽略，不影响其他条目"""
    req = _FakeReq(_FakeHeaders({'X-Forwarded-For': '1.2.3.4'}), remote_addr='127.0.0.1')
    assert CommonUtil.get_real_ip(req, ['not-an-ip', '127.0.0.1']) == '1.2.3.4'


def test_mask_uri_basic():
    assert CommonUtil.mask_uri(
        'mysql+pymysql://user:secret@host:3306/db'
    ) == 'mysql+pymysql://user:***@host:3306/db'


def test_mask_uri_empty_username():
    """redis://:password@host 空用户名格式也应脱敏"""
    assert CommonUtil.mask_uri(
        'redis://:secret@host:6379/0'
    ) == 'redis://:***@host:6379/0'


def test_mask_uri_none():
    assert CommonUtil.mask_uri(None) is None


def test_mask_uri_no_password():
    """无密码的 URI 原样返回"""
    assert CommonUtil.mask_uri('redis://host:6379/0') == 'redis://host:6379/0'


def test_mask_uri_password_with_at():
    """密码含 @（如 p@ssw0rd）时应完整脱敏到凭据分隔符（修复前泄漏 @ 后残段）"""
    assert CommonUtil.mask_uri(
        'mysql+pymysql://user:p@ss@host:3306/db'
    ) == 'mysql+pymysql://user:***@host:3306/db'


def test_mask_uri_at_in_query_untouched():
    """@ 出现在 query 中时（不在密码段内）不应影响脱敏结果"""
    assert CommonUtil.mask_uri(
        'mysql+pymysql://user:pass@host/db?email=a@b.com'
    ) == 'mysql+pymysql://user:***@host/db?email=a@b.com'


def test_dict_map_no_mutation_of_mapper():
    """传入 mapper_list 时不应修改调用方传入的 mapper"""
    mapper = {'a': 'x'}
    CommonUtil.dict_map({'a': 1, 'b': 2}, mapper=mapper, mapper_list=['b'])
    assert mapper == {'a': 'x'}


def test_obj_to_dict_datetime_iso():
    """datetime 应转为 ISO 8601 字符串（而非 str() 的本地格式）"""
    from datetime import datetime
    d = CommonUtil.obj_to_dict(datetime(2024, 6, 30, 12, 0, 0))
    assert isinstance(d, str)
    assert d == '2024-06-30T12:00:00'


def test_obj_to_dict_unsupported_fallback():
    """无法处理的对象（如 bytes）回退为字符串"""
    d = CommonUtil.obj_to_dict(b'\x01\x02')
    assert isinstance(d, str)


def test_dict_map_only_false_with_mapper_list():
    """only=False + mapper_list：保留所有键，mapper_list 键名不变"""
    obj = {'a': 1, 'b': 2, 'c': 3}
    out = CommonUtil.dict_map(obj, mapper={'a': 'x'}, mapper_list=['b'], only=False)
    assert out == {'x': 1, 'b': 2, 'c': 3}
