"""分页工具（flask_server.util.pagination）测试。"""

from types import SimpleNamespace

from flask_server.util.pagination import Page, paginate


def test_page_dict_shape_and_pages():
    page = Page(items=[1, 2], page=2, per_page=2, total=5)
    assert page.pages == 3
    assert page.has_prev is True
    assert page.has_next is True
    assert page.to_dict() == {
        'items': [1, 2], 'page': 2, 'per_page': 2, 'total': 5, 'pages': 3,
    }


def test_paginate_list_slices_and_reports_total():
    data = list(range(45))
    page = paginate(data, page=2, per_page=10)
    assert page.items == list(range(10, 20))
    assert page.total == 45
    assert page.pages == 5
    assert page.to_dict()['items'] == list(range(10, 20))


def test_paginate_normalizes_invalid_args():
    """page/per_page 非法值不应 500：钳到合法范围（客户端参数错误交给 schema 更好）"""
    assert paginate([1, 2, 3], page=0, per_page=0).to_dict()['page'] == 1
    p = paginate(list(range(500)), page=1, per_page=10_000)
    assert p.per_page == 100
    assert len(p.items) == 100


def test_paginate_empty_and_out_of_range():
    assert paginate([], page=3, per_page=10).to_dict()['items'] == []
    p = paginate(list(range(5)), page=9, per_page=10)
    assert p.items == []
    assert p.total == 5
    assert p.pages == 1


def test_paginate_uses_native_query_paginate():
    """SQLAlchemy Query 有 paginate()：必须走原生分页（不能先全量 list）"""
    calls = {}

    class FakeQuery:
        def paginate(self, page, per_page, error_out):
            calls.update(page=page, per_page=per_page, error_out=error_out)
            return SimpleNamespace(items=['a', 'b'], page=page, per_page=per_page, total=7)

    result = paginate(FakeQuery(), page=2, per_page=2)
    assert calls == {'page': 2, 'per_page': 2, 'error_out': False}
    assert result.items == ['a', 'b']
    assert result.total == 7
    assert result.pages == 4
