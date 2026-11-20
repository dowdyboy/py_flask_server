from werkzeug.datastructures.file_storage import FileStorage
import os
import shutil
from ..config import config

# 本地文件存储模块，用于存储文件到本地文件系统

class LocalFileStorage:

    def __init__(self, root_path):
        super().__init__()
        self.root_path = root_path

    # 基于配置的根目录，生成最终的路径，并校验防止路径穿越
    def _gen_final_path(self, path, create_dirs=True):
        final_path = os.path.realpath(os.path.join(self.root_path, path))
        root_real = os.path.realpath(self.root_path)
        # 校验最终路径在 root_path 内，防止 .. 路径穿越
        if not final_path.startswith(root_real + os.sep) and final_path != root_real:
            raise ValueError(f'Path traversal detected: {path} escapes root {self.root_path}')
        if create_dirs and not os.path.isdir(os.path.dirname(final_path)):
            os.makedirs(
                os.path.dirname(final_path),
                exist_ok=True,
            )
        return final_path

    def _gen_mutation_path(self, path):
        """变更类操作（save/copy/move/delete）的路径校验：额外拒绝"存储根自身"。

        修复前 _gen_final_path 显式放行了根目录（`final_path != root_real` 为合法），
        于是 delete('.') / delete('') 会把存储根交给 shutil.rmtree —— 一次输入就能删空
        整个 storage。任何变更类操作针对根都是误用，这里一律拒绝。
        """
        final_path = self._gen_final_path(path)
        if final_path == os.path.realpath(self.root_path):
            raise ValueError(
                f'refusing to operate on the storage root itself: {path!r}')
        return final_path

    # 公开的路径解析接口：将存储相对路径解析为受校验的绝对路径（供外部按路径取用文件）
    def resolve_path(self, path, create_dirs=False):
        """解析相对路径为绝对路径（校验在存储根目录内，防路径穿越）。

        Args:
            path (str): 存储根目录内的相对路径
            create_dirs (bool): 是否自动创建父目录（默认 False，无副作用）

        Returns:
            str: 校验通过的绝对路径

        Raises:
            ValueError: 路径穿越（逃逸存储根目录）
        """
        return self._gen_final_path(path, create_dirs=create_dirs)

    # 存储到所配置的目录中
    def save(self, path, obj):
        final_path = self._gen_mutation_path(path)
        if isinstance(obj, FileStorage):
            obj.save(str(final_path))
            return
        with open(final_path, 'wb') as file:
            file.write(obj)

    # 可以存储到任意路径
    # ⚠️ 安全警告：此方法绕过路径校验，可读写任意路径。
    # 切勿将用户输入直接传递给此方法，否则会导致任意文件读写漏洞。
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
    # ⚠️ 安全警告：此方法绕过路径校验，可读取任意路径文件。
    # 切勿将用户输入直接传递给此方法，否则会导致敏感信息泄露。
    def load_raw_path(self, path):
        with open(path, 'rb') as file:
            return file.read()

    # 复制文件或目录
    def copy(self, src_path, dst_path):
        src_path = self._gen_mutation_path(src_path)
        dst_path = self._gen_mutation_path(dst_path)
        if not os.path.exists(src_path):
            raise FileNotFoundError(f"{src_path} not exist")
        # 与 shutil.copy 的文件语义对齐：目标是**已存在的目录**时，把源拷进该目录，
        # 而不是抛 FileExistsError（修复前目录走 copytree、文件走 copy，同一 API 两种语义）
        if os.path.isdir(dst_path):
            dst_path = os.path.join(dst_path, os.path.basename(src_path.rstrip(os.sep)))
        if os.path.isdir(src_path):
            # symlinks=True：树内的符号链接按链接本身复制，不解引用。
            # 解引用会把"指向存储根之外"的目标内容复制进 storage，绕过本方法开头的
            # 根边界校验（_gen_mutation_path 只看顶层路径，不看目录树内部）。
            shutil.copytree(str(src_path), str(dst_path), symlinks=True)
        else:
            shutil.copy(str(src_path), str(dst_path))

    # 移动文件或目录
    def move(self, src_path, dst_path):
        src_path = self._gen_mutation_path(src_path)
        dst_path = self._gen_mutation_path(dst_path)
        if not os.path.exists(src_path):
            raise FileNotFoundError(f"{src_path} not exist")
        shutil.move(str(src_path), str(dst_path))

    # 删除文件或目录
    def delete(self, path):
        path = self._gen_mutation_path(path)
        if not os.path.exists(path):
            raise FileNotFoundError(f"{path} not exist")
        if os.path.isdir(path):
            shutil.rmtree(str(path))
        else:
            os.remove(str(path))

    # 判断文件或目录是否存在（无副作用：不创建任何目录）
    def exists(self, path):
        path = self._gen_final_path(path, create_dirs=False)
        return os.path.exists(path)


local_file_storage = LocalFileStorage(config.file_saved_path)

