# 文件上传/下载端点示例（教学参考，不在工程运行路径内）
#
# 展示了 multipart 上传 → LocalFileStorage 持久化 → 下载 → 删除 的完整闭环。
# 接入工程：将本文件放入 flask_server/controller/（自动注册），或复制代码到你的 controller。
#
# 依赖：flask_server/module.local_file_storage（框架内置）

import os
import uuid

from flask import request, send_file
from flask_server.app import app, json_response
from flask_server.module import local_file_storage
from flask_server.util import GraceResult, Logger

# 上传文件保存到 storage/uploads/<uid>_<filename>（自动建目录）
UPLOAD_DIR = 'uploads'


def _save_upload(file_storage):
    """保存上传文件，返回存储的相对路径"""
    uid = uuid.uuid4().hex
    filename = os.path.basename(file_storage.filename or 'file')
    rel_path = f'{UPLOAD_DIR}/{uid}_{filename}'
    local_file_storage.save(rel_path, file_storage)
    return rel_path


@app.route('/api/example/file/upload', methods=['POST'])
@json_response
def upload_file():
    """上传文件（multipart，字段名 file）"""
    file_storage = request.files.get('file')
    if file_storage is None:
        return GraceResult.param_error('缺少 file 字段')
    rel_path = _save_upload(file_storage)
    Logger.info(f'file uploaded: {rel_path}')
    return GraceResult.ok({'path': rel_path})


@app.route('/api/example/file/download', methods=['GET'])
def download_file():
    """下载文件（参数 path = 上传时返回的相对路径）"""
    path = request.params.get('path')
    if not path:
        return GraceResult.param_error('缺少 path 参数'), 400
    # 经 local_file_storage 校验最终路径在存储根目录内（拦截 .. 路径穿越）
    try:
        file_path = local_file_storage._gen_final_path(path, create_dirs=False)
    except ValueError:
        return GraceResult.param_error('非法路径'), 400
    if not os.path.exists(file_path):
        return GraceResult.business_error(4004, '文件不存在'), 404
    return send_file(file_path, as_attachment=True)


@app.route('/api/example/file/delete', methods=['POST'])
@json_response
def delete_file():
    """删除文件（参数 path = 上传时返回的相对路径）"""
    path = request.payload.get('path')
    if not path:
        return GraceResult.param_error('缺少 path 参数')
    try:
        local_file_storage.delete(path)
    except FileNotFoundError:
        return GraceResult.business_error(4004, '文件不存在'), 404
    return GraceResult.ok()
