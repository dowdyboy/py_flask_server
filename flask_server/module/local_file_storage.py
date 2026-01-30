from werkzeug.datastructures.file_storage import FileStorage
import os
import shutil
from ..config import config

# 本地文件存储模块，用于存储文件到本地文件系统

class LocalFileStorage:
    
    def __init__(self, root_path):
        super().__init__()
        self.root_path = root_path

    # 基于配置的根目录，生成最终的路径
    def _gen_final_path(self, path):
        final_path = os.path.join(self.root_path, path)
        if not os.path.isdir(os.path.dirname(final_path)):
            os.makedirs(
                os.path.dirname(final_path),
                exist_ok=True,
            )
        return final_path

    # 存储到所配置的目录中
    def save(self, path, obj):
        final_path = self._gen_final_path(path)
        if isinstance(obj, FileStorage):
            obj.save(str(final_path))
            return
        with open(final_path, 'wb') as file:
            file.write(obj)

    # 可以存储到任意路径
    def save_raw_path(self, path, obj):
        if isinstance(obj, FileStorage):
            obj.save(str(path))
            return
        with open(path, 'wb') as file:
            file.write(obj)

    # 从所配置的目录中读取
    def load(self, path):
        final_path = self._gen_final_path(path)
        with open(final_path, 'rb') as file:
            return file.read()

    # 可以从任意路径读取
    def load_raw_path(self, path):
        with open(path, 'rb') as file:
            return file.read()

    # 复制文件或目录
    def copy(self, src_path, dst_path):
        src_path = self._gen_final_path(src_path)
        dst_path = self._gen_final_path(dst_path)
        if not os.path.exists(src_path):
            raise FileNotFoundError(f"{src_path} not exist")
        if os.path.isdir(src_path):
            shutil.copytree(str(src_path), str(dst_path))
        else:
            shutil.copy(str(src_path), str(dst_path))

    # 移动文件或目录
    def move(self, src_path, dst_path):
        src_path = self._gen_final_path(src_path)
        dst_path = self._gen_final_path(dst_path)
        if not os.path.exists(src_path):
            raise FileNotFoundError(f"{src_path} not exist")
        shutil.move(str(src_path), str(dst_path))

    # 删除文件或目录
    def delete(self, path):
        path = self._gen_final_path(path)
        if not os.path.exists(path):
            raise FileNotFoundError(f"{path} not exist")
        if os.path.isdir(path):
            shutil.rmtree(str(path))
        else:
            os.remove(str(path))

    # 判断文件或目录是否存在
    def exists(self, path):
        path = self._gen_final_path(path)
        return os.path.exists(path)


local_file_storage = LocalFileStorage(os.path.join(config.project_dir, config.file_saved_path))

