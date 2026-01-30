import uuid
import time
import threading


# 用于生成唯一ID的工具类


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
            raise ValueError(f"worker_id值越界")
        if datacenter_id > self.max_datacenter_id or datacenter_id < 0:
            raise ValueError(f"datacenter_id值越界")

        self.worker_id = worker_id
        self.datacenter_id = datacenter_id
        self.sequence = sequence

        # 上次生成ID的时间戳
        self.last_timestamp = -1

    def _gen_timestamp(self):
        """生成当前时间戳"""
        return int(time.time() * 1000)

    def next_id(self):
        """生成下一个ID"""
        with threading.Lock():
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


class KeyGenerator:

    snow_worker = SnowflakeIdWorker(worker_id=1, datacenter_id=1)

    @staticmethod
    def generate_uuid():
        """生成UUID主键"""
        return str(uuid.uuid4())

    @staticmethod
    def generate_snowflake_id():
        """
        雪花算法生成主键
        """
        return str(KeyGenerator.snow_worker.next_id())

