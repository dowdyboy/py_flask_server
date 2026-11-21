import os
import threading
from collections import OrderedDict
from typing import Optional

from flask import send_from_directory, abort, redirect, request, current_app
from ..config import config
from ..util import Logger

# 对静态界面的Controller，一般不用修改


Logger.info("webui_controller.py loaded")

# 静态文件存在性缓存：debug 模式下禁用（None），避免开发期缓存陈旧；生产模式启用以减少磁盘 IO。
# OrderedDict + 上限 + 锁：避免无界增长导致内存泄漏，多线程安全
# 显式注解：两个分支分别是 OrderedDict / None，不注解类型检查无法推断
path_exist_cache: Optional[OrderedDict] = OrderedDict() if not config.debug else None
_PATH_CACHE_MAX = 2048
_path_cache_lock = threading.Lock()


def _cache_get(path):
    """读取缓存并刷新访问顺序（LRU）"""
    with _path_cache_lock:
        if path in path_exist_cache:
            path_exist_cache.move_to_end(path)
            return path_exist_cache[path]
        return None


def _cache_set(path, exists):
    """写入缓存，超限时淘汰最久未访问的项"""
    with _path_cache_lock:
        path_exist_cache[path] = exists
        path_exist_cache.move_to_end(path)
        while len(path_exist_cache) > _PATH_CACHE_MAX:
            path_exist_cache.popitem(last=False)

# 静态资源扩展名集合：带这些扩展名且不存在的路径返回 404，而非回退 index.html
STATIC_EXT_SET = {
    '.js', '.css', '.json', '.map',
    '.png', '.jpg', '.jpeg', '.gif', '.ico', '.svg', '.webp', '.bmp',
    '.woff', '.woff2', '.ttf', '.eot', '.otf',
    '.html', '.htm', '.xml', '.txt',
}


def _is_path_safe(filename):
    """校验 filename 经 realpath 解析后仍在 webui_dir 内，防止 .. 路径探测。

    统一把反斜杠归一为正斜杠再校验：Windows 上反斜杠是分隔符，POSIX 上虽然只是
    普通字符，但让安全边界跨平台一致（`..\\..\\etc\\passwd` 在所有平台都按穿越处理）。
    """
    webui_real = os.path.realpath(config.webui_dir)
    normalized = filename.replace('\\', '/')
    filepath_real = os.path.realpath(os.path.join(config.webui_dir, normalized))
    return filepath_real.startswith(webui_real + os.sep) or filepath_real == webui_real


def _redirect_to_registered_route(path):
    """带尾斜杠的路径若对应已注册路由（如 /metrics/ → /metrics、/docs/ → /docs），
    返回 308 重定向，避免落入本 catch-all 被 SPA 回退吞掉（修复前 Prometheus 抓
    /metrics/ 拿到的是 200 index.html，解析指标失败且无感知）。
    """
    target = path.rstrip('/')
    if not target or target == path:
        return None
    from werkzeug.exceptions import NotFound
    from werkzeug.routing import RequestRedirect
    adapter = current_app.url_map.bind('localhost')
    try:
        rule, _ = adapter.match(target, 'GET', return_rule=True)
    except (NotFound, RequestRedirect):
        return None
    # 按 endpoint 判断而不是硬编码规则字符串 '/<path:filename>'：
    # 规则文本一旦调整（例如加前缀），硬编码判断会静默失效
    if rule.endpoint == 'webui':
        return None   # 目标仍是本 catch-all（SPA 路由带尾斜杠），不回退重定向
    return redirect(target, code=308)


def webui(filename):
    # 尾斜杠路径命中已注册路由时重定向到规范路径（见 _redirect_to_registered_route）
    _redirect = _redirect_to_registered_route(request.path)
    if _redirect is not None:
        return _redirect
    # API 路径不走 SPA 回退，直接 404（含精确 /api 与 /api/xxx 子路径；大小写不敏感）
    _filename_lower = filename.lower()
    if _filename_lower == 'api' or _filename_lower.startswith('api/'):
        abort(404)
    # 路径穿越防护：防止 .. 探测服务器任意文件是否存在
    if not _is_path_safe(filename):
        abort(404)

    filepath = os.path.join(config.webui_dir, filename)
    _, ext = os.path.splitext(filename)
    is_static_ext = ext.lower() in STATIC_EXT_SET

    # 先查缓存、再 stat：修复前无条件 os.path.exists(filepath) 在查缓存之前执行，
    # 命中也照样 stat 一次，"减少磁盘 IO"的注释并没有兑现。
    if path_exist_cache is not None:
        file_exists = _cache_get(filepath)
        if file_exists is None:
            file_exists = os.path.exists(filepath)
            _cache_set(filepath, file_exists)
    else:
        # debug 模式不做缓存，保证改动静态文件后立即生效
        file_exists = os.path.exists(filepath)

    if file_exists:
        return send_from_directory(config.webui_dir, filename)

    # 不存在：带静态资源扩展名直接 404（而不是回退 index.html，避免前端拿到 HTML 当 JS）
    if is_static_ext:
        abort(404)
    index_path = os.path.join(config.webui_dir, 'index.html')
    if os.path.exists(index_path):
        return send_from_directory(config.webui_dir, 'index.html')
    abort(404)


def register(app):
    """把静态站点路由挂到指定 app（两条规则共用 endpoint 'webui'）"""
    app.add_url_rule('/', 'webui', webui, defaults={'filename': 'index.html'}, methods=['GET'])
    app.add_url_rule('/<path:filename>', 'webui', webui, methods=['GET'])
