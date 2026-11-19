"""AXI DMA / AXI GPIO 的纯 Python 实现（ctypes 直接对接 /dev/axidma）

对应 C 版本：
    MyAxiDma dma;  dma.allocBuffer(n); dma.transfer(ch, buf, n, true);
    MyAxiGpio gpio("1023", "out"); gpio.write(1);

运行环境：Zynq-7020 + PetaLinux 2020.2
前置条件：
    insmod /lib/modules/$(uname -r)/extra/axidma.ko     # 生成 /dev/axidma
    设备树里有 xlnx,axidma-chrdev 和 xlnx,axi-dma 节点

用法：
    with axi.AxiDma() as dma:
        buf = dma.malloc(1024)
        gpio = axi.AxiGpio(1023, "out")
        gpio.pulse(20)
        dma.read(dma.rx_channel(), buf, 1024)
        data = buf.read()          # bytes
"""

import ctypes
import fcntl
import mmap
import os
import time

AXIDMA_DEV_PATH = "/dev/axidma"

# ---- enum axidma_dir ----
AXIDMA_WRITE = 0   # memory -> device (MM2S)
AXIDMA_READ = 1    # device -> memory (S2MM)

# ---- enum axidma_type ----
AXIDMA_DMA = 0
AXIDMA_VDMA = 1

_IOCTL_MAGIC = "W"
_IOC_WRITE = 1
_IOC_READ = 2


def _ioc(direction, type_char, nr, size):
    return (direction << 30) | (size << 16) | (ord(type_char) << 8) | nr


# ---------------------------------------------------------------------------
# C 结构体（armv7 32 位：指针 4 字节，size_t 4 字节，bool 1 字节）
# ---------------------------------------------------------------------------
class AxidmaVideoFrame(ctypes.Structure):
    _fields_ = [("height", ctypes.c_int),
                ("width", ctypes.c_int),
                ("depth", ctypes.c_int)]


class AxidmaChan(ctypes.Structure):
    _fields_ = [("dir", ctypes.c_int),
                ("type", ctypes.c_int),
                ("channel_id", ctypes.c_int),
                ("name", ctypes.c_char_p),
                ("chan", ctypes.c_void_p)]


class AxidmaNumChannels(ctypes.Structure):
    _fields_ = [("num_channels", ctypes.c_int),
                ("num_dma_tx_channels", ctypes.c_int),
                ("num_dma_rx_channels", ctypes.c_int),
                ("num_vdma_tx_channels", ctypes.c_int),
                ("num_vdma_rx_channels", ctypes.c_int)]


class AxidmaChannelInfo(ctypes.Structure):
    _fields_ = [("channels", ctypes.POINTER(AxidmaChan))]


class _FrameUnion(ctypes.Union):
    _fields_ = [("frame", AxidmaVideoFrame)]


class AxidmaTransaction(ctypes.Structure):
    _fields_ = [("wait", ctypes.c_bool),
                ("channel_id", ctypes.c_int),
                ("buf", ctypes.c_void_p),
                ("buf_len", ctypes.c_size_t),
                ("u", _FrameUnion)]


AXIDMA_GET_NUM_DMA_CHANNELS = _ioc(_IOC_WRITE, _IOCTL_MAGIC, 0, ctypes.sizeof(AxidmaNumChannels))
AXIDMA_GET_DMA_CHANNELS = _ioc(_IOC_READ, _IOCTL_MAGIC, 1, ctypes.sizeof(AxidmaChannelInfo))
AXIDMA_STOP_DMA_CHANNEL = _ioc(_IOC_READ, _IOCTL_MAGIC, 9, ctypes.sizeof(AxidmaChan))
AXIDMA_DMA_READ = _ioc(_IOC_READ, _IOCTL_MAGIC, 4, ctypes.sizeof(AxidmaTransaction))
AXIDMA_DMA_WRITE = _ioc(_IOC_READ, _IOCTL_MAGIC, 5, ctypes.sizeof(AxidmaTransaction))


class AxiDmaError(RuntimeError):
    pass


class DmaBuffer:
    """驱动程序通过 mmap 分配的物理连续缓冲（dma_alloc_coherent）"""

    def __init__(self, mm):
        self._mm = mm
        # 取用户虚拟地址给 ioctl 用（必须保留 _view，否则 mm 无法 close）
        self._view = ctypes.c_char.from_buffer(mm)
        self.addr = ctypes.addressof(self._view)
        self.size = len(mm)

    def write(self, data):
        n = min(len(data), self.size)
        self._mm.seek(0)
        self._mm.write(bytes(data[:n]))
        return n

    def read(self, n=None):
        self._mm.seek(0)
        return self._mm.read(self.size if n is None else n)

    def __len__(self):
        return self.size

    def close(self):
        try:
            del self._view
        except Exception:
            pass
        try:
            self._mm.close()
        except Exception:
            pass


class AxiDma:
    def __init__(self, path=AXIDMA_DEV_PATH, exclusive=True):
        flags = os.O_RDWR | (os.O_EXCL if exclusive else 0)
        self.fd = os.open(path, flags)
        self._bufs = []
        self._channels = self._query_channels()

    def _query_channels(self):
        num = AxidmaNumChannels()
        fcntl.ioctl(self.fd, AXIDMA_GET_NUM_DMA_CHANNELS, num, True)
        total = num.num_dma_tx_channels + num.num_dma_rx_channels
        if total == 0:
            return []
        arr = (AxidmaChan * total)()
        info = AxidmaChannelInfo(ctypes.cast(arr, ctypes.POINTER(AxidmaChan)))
        fcntl.ioctl(self.fd, AXIDMA_GET_DMA_CHANNELS, info, True)
        return [{"dir": arr[i].dir, "type": arr[i].type, "channel_id": arr[i].channel_id}
                for i in range(total)]

    def _transfer(self, cmd, channel_id, buf, length, wait):
        trans = AxidmaTransaction()
        trans.wait = wait
        trans.channel_id = channel_id
        trans.buf = buf.addr if isinstance(buf, DmaBuffer) else int(buf)
        trans.buf_len = length
        fcntl.ioctl(self.fd, cmd, trans, True)

    @property
    def channels(self):
        return self._channels

    def find(self, direction):
        for ch in self._channels:
            if ch["dir"] == direction:
                return ch["channel_id"]
        return None

    def tx_channel(self):
        return self.find(AXIDMA_WRITE)

    def rx_channel(self):
        return self.find(AXIDMA_READ)

    def malloc(self, size):
        mm = mmap.mmap(self.fd, size, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)
        buf = DmaBuffer(mm)
        self._bufs.append(buf)
        return buf

    def free(self, buf):
        if buf in self._bufs:
            self._bufs.remove(buf)
        buf.close()

    def read(self, channel_id, buf, length, wait=True):
        """device -> memory（S2MM）"""
        self._transfer(AXIDMA_DMA_READ, channel_id, buf, length, wait)

    def write(self, channel_id, buf, length, wait=True):
        """memory -> device（MM2S）"""
        self._transfer(AXIDMA_DMA_WRITE, channel_id, buf, length, wait)

    def close(self):
        for b in list(self._bufs):
            b.close()
        self._bufs.clear()
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class AxiGpio:
    """AXI GPIO（走 /sys/class/gpio，和 C 版 MyAxiGpio 一致）"""

    SYSFS = "/sys/class/gpio"

    def __init__(self, gpio_id, direction="out"):
        self.id = str(gpio_id)
        self.dir_path = f"{self.SYSFS}/gpio{self.id}"
        if not os.path.exists(self.dir_path):
            self._sysfs_write(f"{self.SYSFS}/export", self.id)
            for _ in range(100):
                if os.path.exists(self.dir_path):
                    break
                time.sleep(0.01)
        self._sysfs_write(f"{self.dir_path}/direction", direction)

    @staticmethod
    def _sysfs_write(path, value):
        with open(path, "w") as f:
            f.write(f"{value}\n")

    def write(self, value):
        self._sysfs_write(f"{self.dir_path}/value", int(value))

    def read(self):
        with open(f"{self.dir_path}/value") as f:
            return int(f.read().strip())

    def pulse(self, us=10):
        self.write(1)
        time.sleep(us / 1_000_000)
        self.write(0)
