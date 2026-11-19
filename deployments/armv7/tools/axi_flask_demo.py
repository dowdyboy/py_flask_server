"""把 AXI 读写挂到现有 Flask 应用上（不修改项目源码，运行时加路由）

启动：
    /opt/pyflask/venv/bin/python tools/axi_flask_demo.py
然后：
    curl http://127.0.0.1:5001/api/v1/fft/frame
"""

import sys
import threading
import time

sys.path.insert(0, "/opt/pyflask/app")
sys.path.insert(0, "/opt/pyflask/app/tools")

import axi  # noqa: E402
from flask_server import app, json_response  # noqa: E402
from flask_server.util import GraceResult  # noqa: E402
from waitress import serve  # noqa: E402

FRAME_SIZE = 1024
GPIO_ID = 1023


class AxiController:
    """单例：DMA 设备只能被一个进程打开（O_EXCL），内部加锁串行化"""

    def __init__(self):
        self.lock = threading.Lock()
        self.dma = axi.AxiDma()
        self.rx = self.dma.rx_channel()
        self.tx = self.dma.tx_channel()
        self.buf = self.dma.malloc(FRAME_SIZE)
        self.gpio = axi.AxiGpio(GPIO_ID, "out")
        self.gpio.write(0)
        self.frames = 0

    def read_frame(self):
        with self.lock:
            t0 = time.time()
            self.gpio.pulse(20)
            self.dma.read(self.rx, self.buf, FRAME_SIZE)
            data = self.buf.read()
            self.frames += 1
            return data, (time.time() - t0) * 1000


_ctrl = None


def get_ctrl():
    global _ctrl
    if _ctrl is None:
        _ctrl = AxiController()
    return _ctrl


@app.route("/api/v1/fft/frame", methods=["GET"])
@json_response
def fft_frame():
    """读一帧，返回帧号 + 前 64 个 32 位字"""
    data, ms = get_ctrl().read_frame()
    words = [int.from_bytes(data[i:i + 4], "little") for i in range(0, 256, 4)]
    return GraceResult.ok({
        "frame_counter": words[0],
        "bins_head": words[1:33],
        "latency_ms": round(ms, 2),
        "bytes": len(data),
    })


@app.route("/api/v1/fft/raw", methods=["GET"])
@json_response
def fft_raw():
    data, ms = get_ctrl().read_frame()
    return GraceResult.ok({"hex": data.hex(), "latency_ms": round(ms, 2)})


@app.route("/api/v1/fft/stats", methods=["GET"])
@json_response
def fft_stats():
    c = get_ctrl()
    return GraceResult.ok({"frames": c.frames, "size": FRAME_SIZE})


if __name__ == "__main__":
    print("初始化 AXI...", flush=True)
    c = get_ctrl()
    print(f"  tx_ch={c.tx} rx_ch={c.rx} gpio={GPIO_ID}", flush=True)
    data, ms = c.read_frame()
    print(f"  自检一帧: {ms:.2f} ms, 前 16 字节 {data[:16].hex(' ')}", flush=True)
    print("启动 waitress on :5001", flush=True)
    serve(app, host="0.0.0.0", port=5001, threads=4)
