import os
import socket
import threading
import time
import uuid
import zlib
import secrets

from .logger import Logger

# 用于生成唯一ID的工具类
#
# 三种 ID 形态，按场景选择：
#   - generate_uuid()      UUID4：128 位全随机，无排序性，零配置、零冲突
#   - generate_uuid7()     UUIDv7（RFC 9562）：时间有序 + 随机后缀，容器化多副本首选
#   - generate_snowflake_id()  雪花 ID：64 位整数（字符串形式），需分配 worker_id
#
# 雪花 ID 的机器号分配（历史坑）：
#   原实现用 `os.getpid() % 32` 派生 —— 容器/ K8s 里主进程 PID 恒为 1，
#   于是**所有副本的 worker_id 都是 1**，同毫秒发号即冲突。现改为
#   `(crc32(hostname) + pid) % 32`：容器 hostname 默认是唯一容器 ID（K8s 为 Pod 名），
#   多副本可区分；单机多进程再叠加 PID。仍非严格保证，**生产务必显式配置
#   SNOWFLAKE_WORKER_ID（0-31）与 SNOWFLAKE_DATACENTER_ID（0-31）**，
#   对冲突零容忍的场景请直接用 generate_uuid7()。


class SnowflakeIdWorker:

    def __init__(self, worker_id, datacenter_id, sequence=0):
        # 时间起始标记点（epoch），用于用当前时间戳减去这个值，得到一个正整数
        self.twepoch = 1288834974657

        # 机器标识位数
        self.worker_id_bits = 5
        # 数据中心标识位数
        self.datacenter_id_bits = 5

        # 最大支持机器节点数0-31，一共32个
        self.max_worker_id = -1 ^ (-1 << self.worker_id_bits)
        # 最大支持数据中心节点数0-31，一共32个
        self.max_datacenter_id = -1 ^ (-1 << self.datacenter_id_bits)

        # 序列号12位
        self.sequence_bits = 12

        # 机器ID偏左移12位
        self.worker_id_shift = self.sequence_bits
        # 数据中心ID左移17位
        self.datacenter_id_shift = self.sequence_bits + self.worker_id_bits
        # 时间毫秒左移22位
        self.timestamp_left_shift = self.sequence_bits + self.worker_id_bits + self.datacenter_id_bits

        self.sequence_mask = -1 ^ (-1 << self.sequence_bits)  # 4095

        # 初始化机器ID和数据中心ID
        if worker_id > self.max_worker_id or worker_id < 0:
            raise ValueError("worker_id值越界")
        if datacenter_id > self.max_datacenter_id or datacenter_id < 0:
            raise ValueError("datacenter_id值越界")

        self.worker_id = worker_id
        self.datacenter_id = datacenter_id
        self.sequence = sequence

        # 上次生成ID的时间戳
        self.last_timestamp = -1

        # 线程锁：实例属性，确保同一实例的多线程调用互斥
        self._lock = threading.Lock()

    def _gen_timestamp(self):
        """生成当前时间戳"""
        return int(time.time() * 1000)

    def next_id(self):
        """生成下一个ID"""
        with self._lock:
            timestamp = self._gen_timestamp()
            # 如果当前时间小于上一次ID生成的时间戳，说明系统时钟回退过，此时应抛出异常
            if timestamp < self.last_timestamp:
                raise Exception(f"Clock moved backwards. Refusing to generate id for {self.last_timestamp - timestamp}ms")

            if timestamp == self.last_timestamp:
                # 如果是同一时间生成的，则进行毫秒内序列
                self.sequence = (self.sequence + 1) & self.sequence_mask
                # 溢出处理
                if self.sequence == 0:
                    # 阻塞到下一毫秒,获得新的时间戳
                    timestamp = self._til_next_millis(self.last_timestamp)
            else:
                # 时间戳改变，毫秒内序列重置
                self.sequence = 0

            self.last_timestamp = timestamp

            # ID偏移组合生成最终的ID，并返回ID
            new_id = ((timestamp - self.twepoch) << self.timestamp_left_shift) | (
                    self.datacenter_id << self.datacenter_id_shift) | (self.worker_id << self.worker_id_shift) | self.sequence
            return new_id

    def _til_next_millis(self, last_timestamp):
        """阻塞到下一毫秒"""
        timestamp = self._gen_timestamp()
        while timestamp <= last_timestamp:
            timestamp = self._gen_timestamp()
        return timestamp

# 使用示例
# worker = SnowflakeIdWorker(worker_id=1, datacenter_id=1)
# print(worker.next_id())


def _derive_worker_id(pid=None):
    """按 (主机名, PID) 派生 0-31 的 worker_id（未显式配置时的兜底）。

    加入主机名是为了容器场景：容器内主进程 PID 恒为 1，仅用 PID 会让所有副本
    得到同一个 worker_id；容器 hostname 默认是唯一容器 ID / Pod 名，可区分副本。
    """
    if pid is None:
        pid = os.getpid()
    try:
        host = socket.gethostname() or ''
    except OSError:
        host = ''
    base = zlib.crc32(host.encode('utf-8', 'replace')) if host else 0
    return (base + pid) % 32


class KeyGenerator:

    # 雪花 worker 惰性初始化：导入期不读配置/不连任何依赖，便于测试替换配置
    _worker = None
    _worker_lock = threading.Lock()

    @staticmethod
    def _resolve_worker_id():
        from ..config import config
        if config.snowflake_worker_id is not None:
            return config.snowflake_worker_id
        return _derive_worker_id()

    @classmethod
    def _get_worker(cls):
        """获取（首次调用时创建）雪花 ID worker（线程安全）"""
        if cls._worker is not None:
            return cls._worker
        with cls._worker_lock:
            if cls._worker is None:
                from ..config import config
                datacenter_id = getattr(config, 'snowflake_datacenter_id', 1)
                worker_id = cls._resolve_worker_id()
                cls._worker = SnowflakeIdWorker(worker_id=worker_id, datacenter_id=datacenter_id)
                Logger.info(f'SnowflakeIdWorker initialized: worker_id={worker_id}, '
                            f'datacenter_id={datacenter_id}')
        return cls._worker

    @staticmethod
    def generate_uuid():
        """生成UUID主键"""
        return str(uuid.uuid4())

    @staticmethod
    def generate_uuid7():
        """生成 UUIDv7（RFC 9562，时间有序的 UUID）。

        布局：48 位毫秒时间戳 | 4 位版本(7) | 12 位随机 | 2 位变体(10) | 62 位随机。
        相比 UUID4 具备天然的时间有序性（作主键时 B-Tree 插入更局部化，索引页分裂更少），
        相比雪花 ID 不需要分配 worker_id，容器化多副本部署不会撞号。
        """
        unix_ms = int(time.time() * 1000) & ((1 << 48) - 1)
        rand_a = secrets.randbits(12)
        rand_b = secrets.randbits(62)
        value = (unix_ms << 80) | (0x7 << 76) | (rand_a << 64) | (0x2 << 62) | rand_b
        return str(uuid.UUID(int=value))

    @staticmethod
    def generate_snowflake_id():
        """
        雪花算法生成主键
        """
        return str(KeyGenerator._get_worker().next_id())
