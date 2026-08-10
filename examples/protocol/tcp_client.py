# TCP 客户端演示（教学）：与 tcp_echo_handler.py 配套
#
# 用法：先启动服务端（python examples/protocol/tcp_echo_handler.py），再运行本脚本
# 或直接发送原始命令验证：echo -e 'hello\n' | nc 127.0.0.1 9000

import socket

HOST = '127.0.0.1'
PORT = 9000


def main():
    sock = socket.create_connection((HOST, PORT), timeout=5)
    try:
        sock.sendall(b'hello\n')          # 行帧：必须以分隔符结尾
        data = sock.recv(1024)
        print(f'recv: {data!r}')
    finally:
        sock.close()


if __name__ == '__main__':
    main()
