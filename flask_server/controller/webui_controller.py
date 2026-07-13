import os
from flask import send_from_directory, abort
from flask_server import app, config
from flask_server.util import Logger

# 对静态界面的Controller，一般不用修改


Logger.info("webui_controller.py loaded")

# 静态文件存在性缓存：debug 模式下禁用，避免开发期缓存陈旧；生产模式启用以减少磁盘 IO
path_exist_cache = dict() if not config.debug else None

# 静态资源扩展名集合：带这些扩展名且不存在的路径返回 404，而非回退 index.html
STATIC_EXT_SET = {
    '.js', '.css', '.json', '.map',
    '.png', '.jpg', '.jpeg', '.gif', '.ico', '.svg', '.webp', '.bmp',
    '.woff', '.woff2', '.ttf', '.eot', '.otf',
    '.html', '.htm', '.xml', '.txt',
}


def _is_path_safe(filename):
    """校验 filename 经 realpath 解析后仍在 webui_dir 内，防止 .. 路径探测"""
    webui_real = os.path.realpath(config.webui_dir)
    filepath_real = os.path.realpath(os.path.join(config.webui_dir, filename))
    return filepath_real.startswith(webui_real + os.sep) or filepath_real == webui_real


@app.route('/', methods=['GET'], defaults={'filename': 'index.html'})
@app.route('/<path:filename>', methods=['GET'])
def webui(filename):
    # API 路径不走 SPA 回退，直接 404
    if filename.startswith('api/'):
        abort(404)
    # 路径穿越防护：防止 .. 探测服务器任意文件是否存在
    if not _is_path_safe(filename):
        abort(404)

    filepath = os.path.join(config.webui_dir, filename)
    file_exists = os.path.exists(filepath)

    # 带静态资源扩展名且不存在的路径返回 404（而非回退 index.html）
    _, ext = os.path.splitext(filename)
    if ext.lower() in STATIC_EXT_SET and not file_exists:
        abort(404)

    if path_exist_cache is not None and filepath in path_exist_cache.keys():
        if path_exist_cache[filepath]:
            return send_from_directory(config.webui_dir, filename)
        else:
            index_path = os.path.join(config.webui_dir, 'index.html')
            if os.path.exists(index_path):
                return send_from_directory(config.webui_dir, 'index.html')
            abort(404)
    else:
        if file_exists:
            if path_exist_cache is not None:
                path_exist_cache[filepath] = True
            return send_from_directory(config.webui_dir, filename)
        else:
            if path_exist_cache is not None:
                path_exist_cache[filepath] = False
            index_path = os.path.join(config.webui_dir, 'index.html')
            if os.path.exists(index_path):
                return send_from_directory(config.webui_dir, 'index.html')
            abort(404)
