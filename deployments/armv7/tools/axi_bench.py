"""python 控 AXI：吞吐 + 超时恢复测试"""
import glob
import signal
import sys
import time

sys.path.insert(0, "/opt/pyflask/app/tools")
import axi  # noqa: E402

N = 1024


def _alarm(s, f):
    print("!! 整体超时", flush=True)
    sys.exit(2)


signal.signal(signal.SIGALRM, _alarm)
signal.alarm(120)


def find_gpio():
    for d in glob.glob("/sys/class/gpio/gpiochip*"):
        try:
            if "gpio@41200000" in open(f"{d}/label").read():
                return int(open(f"{d}/base").read())
        except Exception:
            continue
    return 1023


with axi.AxiDma() as dma:
    rx = dma.rx_channel()
    buf = dma.malloc(N)
    gpio = axi.AxiGpio(find_gpio(), "out")

    def frame():
        gpio.pulse(20)
        dma.read(rx, buf, N)
        return buf.read()

    print("=== 1. 触发 + 读，50 次 ===")
    t0 = time.time()
    for _ in range(50):
        frame()
    dt = time.time() - t0
    print(f"  {dt/50*1000:.2f} ms/次, {50/dt:.1f} 帧/秒")

    print("\n=== 2. 连续读两次（第二次应超时）===")
    frame()
    t0 = time.time()
    try:
        dma.read(rx, buf, N)
        print(f"  第二次竟然成功（{time.time()-t0:.2f}s）")
    except OSError as e:
        print(f"  预期超时: {e}，耗时 {time.time()-t0:.2f} s")

    print("\n=== 3. 超时之后还能用吗 ===")
    try:
        d = frame()
        print(f"  恢复正常，收到 {len(d)} 字节，前 8 字节 {d[:8].hex(' ')}")
    except Exception as e:
        print(f"  !! 没恢复: {e}")

    print("\n=== 4. 数据内容 ===")
    d = frame()
    words = [int.from_bytes(d[i:i+4], "little") for i in range(0, 64, 4)]
    print(f"  前 16 个 32 位字: {words}")
    print(f"  非零字节: {sum(1 for b in d if b)}/{N}, max={max(d)}")

    print("\n=== 5. 连续 5 帧是否变化 ===")
    for i in range(5):
        d = frame()
        print(f"  帧{i+1}: {d[:12].hex(' ')}  (非零 {sum(1 for b in d if b)})")

    dma.free(buf)
print("\n=== 完成 ===")
