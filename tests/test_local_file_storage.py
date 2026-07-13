import os
import tempfile
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
