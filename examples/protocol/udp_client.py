# UDP 客户端演示（教学）：与 udp_echo_handler.py 配套
#
# 用法：先启动服务端（python examples/protocol/udp_echo_handler.py），再运行本脚本

import socket

HOST = '127.0.0.1'
PORT = 9001


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(3)
    try:
        sock.sendto(b'hello', (HOST, PORT))
        data, addr = sock.recvfrom(1024)
        print(f'recv from {addr}: {data!r}')
    finally:
        sock.close()


if __name__ == '__main__':
    main()
