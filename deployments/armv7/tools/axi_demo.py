"""在板子上验证 axi.py：脉冲 GPIO -> DMA 读 1024 字节 -> 打印

对应 vitis/axi_dma_io/src/main.c 的循环体。
"""
import signal
import sys
import time

# 兜底：DMA 万一卡住，45 秒后自杀（ioctl 是阻塞的，靠信号打断）
def _alarm(signum, frame):
    print("!! 超时退出（DMA 可能卡住或 PL 没出数据）", flush=True)
    sys.exit(2)


signal.signal(signal.SIGALRM, _alarm)
signal.alarm(45)

sys.path.insert(0, "/opt/pyflask/app/tools")   # 部署位置
import axi  # noqa: E402

MAX_PKG_LEN = 0x100 * 4     # 1024 字节，和 Vitis/Linux demo 一致
GPIO_ID = 1023               # AXI GPIO，gpiochip1023


def main():
    print("=== 1. 打开 /dev/axidma ===")
    dma = axi.AxiDma()
    print(f"  通道: {dma.channels}")
    tx = dma.tx_channel()
    rx = dma.rx_channel()
    print(f"  tx_ch={tx}  rx_ch={rx}")
    if rx is None:
        print("!! 没有 RX 通道")
        return 1

    print("=== 2. 分配 DMA 缓冲 ===")
    buf = dma.malloc(MAX_PKG_LEN)
    print(f"  {MAX_PKG_LEN} 字节 OK")

    print("=== 3. 初始化 AXI GPIO ===")
    gpio = axi.AxiGpio(GPIO_ID, "out")
    gpio.write(0)
    print(f"  gpio{GPIO_ID} = {gpio.read()}")

    print("=== 4. 脉冲 GPIO 触发 PL，然后 DMA 读 ===")
    for i in range(3):
        t0 = time.time()
        gpio.pulse(20)
        dma.read(rx, buf, MAX_PKG_LEN, wait=True)
        dt = (time.time() - t0) * 1000
        data = buf.read(MAX_PKG_LEN)
        print(f"  [{i+1}] {dt:.1f} ms  前 32 字节: {data[:32].hex(' ')}")
        time.sleep(0.2)

    print("=== 5. 统计 ===")
    data = bytes(data)
    print(f"  最小值={min(data)} 最大值={max(data)} 均值={sum(data)/len(data):.1f}")
    print(f"  是否全 0: {all(b == 0 for b in data)}")

    dma.free(buf)
    dma.close()
    print("=== 完成 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
