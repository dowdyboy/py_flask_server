"""分页工具：把 SQLAlchemy Query 或普通列表统一成一致的响应结构。

与 flask-smorest 的 `Page`（分页器参数对象）不同，这里是**响应体**：调用方拿到的
`Page` 可直接 `to_dict()` 返回，字段固定为
`{items, page, per_page, total, pages}`，前端/客户端不必按后端来源分支。

用法：

    from flask_server.util import paginate
    from flask_server.schema.common import PageSchema

    @blp.route('/items')
    class ItemList(MethodView):
        @blp.arguments(PaginationQuerySchema, location='query')
        @blp.response(200, PageSchema)
        def get(self, args):
            return paginate(ItemPO.query, page=args['page'], per_page=args['per_page']).to_dict()

SQLAlchemy Query 走 `query.paginate(...)`（Flask-SQLAlchemy 内置），
普通列表/生成器按切片分页；两者都返回同一个 `Page`。
"""

from dataclasses import dataclass
from typing import Any, Iterable, List

DEFAULT_PAGE = 1
DEFAULT_PER_PAGE = 20
DEFAULT_MAX_PER_PAGE = 100


def _normalize_page(page: Any, per_page: Any, max_per_page: int):
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = DEFAULT_PAGE
    try:
        per_page = int(per_page)
    except (TypeError, ValueError):
        per_page = DEFAULT_PER_PAGE
    page = max(1, page)
    per_page = max(1, min(per_page, max_per_page))
    return page, per_page


@dataclass
class Page:
    """一页数据（与具体数据源无关的统一结构）"""

    items: List[Any]
    page: int
    per_page: int
    total: int

    @property
    def pages(self) -> int:
        if self.per_page <= 0:
            return 0
        return (self.total + self.per_page - 1) // self.per_page

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    def to_dict(self) -> dict:
        return {
            'items': list(self.items),
            'page': self.page,
            'per_page': self.per_page,
            'total': self.total,
            'pages': self.pages,
        }


def paginate(source: Iterable, page: Any = DEFAULT_PAGE, per_page: Any = DEFAULT_PER_PAGE,
             max_per_page: int = DEFAULT_MAX_PER_PAGE) -> Page:
    """对 Query 或可迭代对象分页，返回 `Page`。

    - `source` 有 `paginate` 方法（Flask-SQLAlchemy Query）→ 用其原生分页（避免全表加载）；
    - 其它可迭代对象 → 转 list 后切片（列表场景通常数据量小；大列表请用 Query）。
    """
    page, per_page = _normalize_page(page, per_page, max_per_page)

    native = getattr(source, 'paginate', None)
    if callable(native):
        pagination = native(page=page, per_page=per_page, error_out=False)
        return Page(
            items=list(getattr(pagination, 'items', []) or []),
            page=int(getattr(pagination, 'page', page) or page),
            per_page=int(getattr(pagination, 'per_page', per_page) or per_page),
            total=int(getattr(pagination, 'total', 0) or 0),
        )

    items = list(source or [])
    total = len(items)
    start = (page - 1) * per_page
    return Page(items=items[start:start + per_page], page=page, per_page=per_page, total=total)


__all__ = ['Page', 'paginate', 'DEFAULT_PAGE', 'DEFAULT_PER_PAGE', 'DEFAULT_MAX_PER_PAGE']
