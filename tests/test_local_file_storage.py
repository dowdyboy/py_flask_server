import os
import pytest
from flask_server.module.local_file_storage import LocalFileStorage


@pytest.fixture
def storage(tmp_path):
    return LocalFileStorage(str(tmp_path))


def test_save_and_load(storage):
    storage.save('test.txt', b'hello')
    assert storage.load('test.txt') == b'hello'


def test_exists(storage):
    assert not storage.exists('nope.txt')
    storage.save('test.txt', b'data')
    assert storage.exists('test.txt')


def test_delete(storage):
    storage.save('test.txt', b'data')
    storage.delete('test.txt')
    assert not storage.exists('test.txt')


def test_subdir_save(storage):
    storage.save('sub/dir/test.txt', b'data')
    assert storage.load('sub/dir/test.txt') == b'data'


def test_path_traversal_blocked(storage):
    """路径穿越应被拦截"""
    with pytest.raises(ValueError, match='Path traversal'):
        storage.save('../../etc/passwd', b'malicious')


def test_path_traversal_load_blocked(storage):
    with pytest.raises(ValueError, match='Path traversal'):
        storage.load('../../etc/passwd')


def test_path_traversal_delete_blocked(storage):
    with pytest.raises(ValueError, match='Path traversal'):
        storage.delete('../../etc/passwd')


def test_exists_no_side_effect(storage):
    """exists() 不应创建目录（无副作用）"""
    assert not storage.exists('sub/dir/file.txt')
    assert not os.path.exists(os.path.join(str(storage.root_path), 'sub'))


def test_exists_middle_component_is_file(storage):
    """中间路径组件是文件时 exists 返回 False 而非抛异常"""
    storage.save('afile.txt', b'x')
    assert not storage.exists('afile.txt/nope.txt')


def test_copy_file(storage):
    storage.save('src.txt', b'data')
    storage.copy('src.txt', 'dst.txt')
    assert storage.load('dst.txt') == b'data'


def test_copy_directory(storage):
    storage.save('src/a.txt', b'x')
    storage.copy('src', 'dst')
    assert storage.load('dst/a.txt') == b'x'


def test_move(storage):
    storage.save('src.txt', b'data')
    storage.move('src.txt', 'dst.txt')
    assert not storage.exists('src.txt')
    assert storage.load('dst.txt') == b'data'


def test_save_raw_path_bypasses_root(storage, tmp_path):
    """save_raw_path 可写入根目录外（显式安全警告的方法，仅限内部可信调用）"""
    outside = tmp_path / 'outside.txt'
    storage.save_raw_path(str(outside), b'raw-data')
    assert outside.read_bytes() == b'raw-data'


def test_load_raw_path(storage, tmp_path):
    outside = tmp_path / 'outside.txt'
    outside.write_bytes(b'raw-data')
    assert storage.load_raw_path(str(outside)) == b'raw-data'


def test_save_file_storage_object(storage):
    """save 接受 werkzeug FileStorage（multipart 上传对象）"""
    from io import BytesIO
    from werkzeug.datastructures import FileStorage

    fs = FileStorage(stream=BytesIO(b'upload-content'), filename='a.txt')
    storage.save('upload.txt', fs)
    assert storage.load('upload.txt') == b'upload-content'


def test_save_raw_path_file_storage(storage, tmp_path):
    from io import BytesIO
    from werkzeug.datastructures import FileStorage

    fs = FileStorage(stream=BytesIO(b'raw-upload'), filename='b.txt')
    outside = tmp_path / 'outside_upload.txt'
    storage.save_raw_path(str(outside), fs)
    assert outside.read_bytes() == b'raw-upload'


def test_move_directory(storage):
    """move 目录（isdir 分支）"""
    storage.save('src/a.txt', b'x')
    storage.move('src', 'dst')
    assert not storage.exists('src/a.txt')
    assert storage.load('dst/a.txt') == b'x'


def test_delete_directory(storage):
    """delete 目录（rmtree 分支）"""
    storage.save('dir/a.txt', b'x')
    storage.delete('dir')
    assert not storage.exists('dir')
