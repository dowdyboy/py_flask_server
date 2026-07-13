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


def test_get_real_ip_forwarded():
    req = _FakeReq(_FakeHeaders({'X-Forwarded-For': '1.2.3.4, 5.6.7.8'}))
    assert CommonUtil.get_real_ip(req) == '1.2.3.4'


def test_get_real_ip_remote():
    req = _FakeReq(_FakeHeaders({}))
    assert CommonUtil.get_real_ip(req) == '9.9.9.9'
