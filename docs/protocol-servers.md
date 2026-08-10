# TCP / UDP 协议服务器

除 HTTP/WebSocket 外，脚手架内置 **TCP** 与 **UDP** 传输层协议服务器（默认关闭，
零新依赖，Windows/Linux 均可用）。用户只需在 `flask_server/handler/` 下新建文件、
用装饰器注册消息处理器，即可接收对应协议的消息并实现自己的业务逻辑——
**建文件即出接口**，与 controller 的零注册风格一致。

## 快速开始（5 步）

**1. 启用配置**（`.env`）：

```bash
TCP_ENABLED=true      # TCP 服务器（端口默认 9000）
UDP_ENABLED=true      # UDP 服务器（端口默认 9001）
```

**2. 新建处理器文件** `flask_server/handler/echo_demo.py`：

```python
from flask_server.module import tcp_server, udp_server
from flask_server.util import Logger

# ---- TCP：连接建立 / 消息 / 连接断开 ----
@tcp_server.on_connect
def on_connect(conn, addr):
    Logger.info(f'tcp client connected: {addr}')

@tcp_server.on_message
def on_message(conn, data, addr):
    """data 为 bytes，已按分隔符拆好（一帧一条消息）"""
    conn.sendall(b'echo: ' + data)     # 用户自己决定如何回

@tcp_server.on_disconnect
def on_disconnect(conn, addr):
    Logger.info(f'tcp client disconnected: {addr}')

# ---- UDP：返回 bytes 自动回发，返回 None 不回发 ----
@udp_server.on_message
def on_message(data, addr):
    return b'echo: ' + data
```

**3. 启动**（任一入口均可，协议服务器随服务一并启动）：

```bash
python server.py            # 开发
python wsgi.py              # 生产（waitress）
python wsgi_gunicorn.py     # Linux 多进程（见下文多 worker 注意事项）
```

**4. 验证**：

```bash
# TCP：行帧协议，以换行结尾
echo -e 'hello\n' | nc 127.0.0.1 9000        # → echo: hello

# UDP
echo 'hello' | nc -u 127.0.0.1 9001          # → echo: hello
```

**5. 生产自检**：启动 banner 会显示 `TCP` / `UDP` 的状态行（ON/OFF）与监听地址。

## 处理器 API

| 装饰器 | 签名 | 说明 |
|---|---|---|
| `@tcp_server.on_connect` | `on_connect(conn, addr)` | 客户端建立连接时触发（每连接一次） |
| `@tcp_server.on_message` | `on_message(conn, data, addr)` | 收到一条完整消息；`data` 为 bytes，`conn` 为 socket，可直接 `conn.sendall(...)` 回复 |
| `@tcp_server.on_disconnect` | `on_disconnect(conn, addr)` | 连接关闭时触发（正常/异常均触发） |
| `@udp_server.on_message` | `on_message(data, addr)` | 收到一个数据报；**返回 bytes 自动回发**到来源地址，返回 `None` 不回发 |
| `@tcp_server.on_error` / `@udp_server.on_error` | `on_error(e, *原处理器参数)` | 任一处理器抛异常时触发（不影响其他连接/数据报） |

要点：

- 处理器异常**不会中断服务器**：`Logger.error` 记录完整 traceback（带连接地址上下文），
  随后调用 `on_error` 钩子；TCP 连接保持不断开
- UDP 主动发送：处理器返回 `None` 时，可调用 `udp_server.send(data, addr)` 主动回发；
  返回值**必须是 `bytes`**（返回 `str` 等非 bytes 会告警且不回发，便于排查类型错误）
- **处理器内访问数据库**：`on_message` 在独立线程执行，无 Flask app context——
  请使用 `@sqlalchemy_trans` 装饰器或 `in_app_context()` 包装（见
  `flask_server/module/sqlalchemy.py`），直接使用 `db.session`/`Model.query` 会报错
- 注册是 import 副作用（不启动 socket）；处理器文件放在 `handler/` 下任意 `.py`
  即自动导入生效（复用 controller 的自动发现机制）——**自动发现仅在启动时执行，
  运行中新增的 handler 文件需重启生效**；单个 handler 模块导入失败会记录 ERROR 跳过，
  不影响其他模块。⚠️ 自动发现只遍历 `handler/` **顶层模块**（子目录/子包不会被加载），
  请把处理器文件直接放在 `handler/` 下
- **on_message 阻塞提示**：处理器在线程中执行，若逻辑长时间阻塞（死循环/等待外部资源），
  该连接/数据报的并发槽位将一直占用（直至返回）——请保证处理器有界执行，
  避免耗尽 `TCP_MAX_CONNECTIONS` / `UDP_MAX_CONCURRENCY` 名额
- 完整可运行样例见 `examples/protocol/`（含 `tcp_client.py` / `udp_client.py` 客户端）

## TCP 消息定界（帧格式）

TCP 是**流**协议，"消息"边界需自行定义。脚手架提供四种模式（`TCP_FRAMING`），
全部自动处理**粘包**（一包含多帧分别回调）与**拆包**（一帧跨多包累积完整后回调）：

| 模式 | 说明 | 适用 |
|---|---|---|
| `line`（默认） | 按分隔符切分消息，分隔符默认 `\n`（`TCP_FRAME_SEPARATOR` 可配，支持 `\n`/`\r\n`）；`on_message` 拿到的 `data` 已剥离分隔符，纯空帧自动跳过 | 文本协议，最简单 |
| `fixed` | 按固定长度切分，`TCP_FRAME_LENGTH` 指定每帧字节数；`data` 为恰好一帧。⚠️ 每连接缓冲可累积到 `TCP_FRAME_LENGTH` 字节（慢速填充攻击下内存占用 ≈ 帧长 × 连接数），请按实际协议帧大小配置 | 定长报文协议（如 GPS/采集帧） |
| `head_tail` | 帧头（`TCP_FRAME_HEAD`）帧尾（`TCP_FRAME_TAIL`）定界；`data` 为**帧头帧尾之间的负载**（剥离开销字节） | 带帧头的二进制协议（如 STX/ETX） |
| `raw` | 每次 `recv` 的原始数据直接回调，粘包/拆包由用户自行处理 | 自定义二进制协议 |

配置示例：

```bash
# 固定长度：每帧 32 字节
TCP_FRAMING=fixed
TCP_FRAME_LENGTH=32

# 帧头帧尾：帧头 \xAA\x55，帧尾 \x0D\x0A（二进制字节用 \x 转义写法）
TCP_FRAMING=head_tail
TCP_FRAME_HEAD=\xaa\x55
TCP_FRAME_TAIL=\x0d\x0a
```

head_tail 模式的重同步策略（对脏数据/断流场景自动恢复）：

- **帧头前垃圾字节自动丢弃**（连接中途接入、半帧前缀）
- **帧尾缺失时重同步**：首帧帧尾丢失、后续新帧头到达时，以更靠后的帧头重新开始
  切帧（负载内含帧头字节时仍以首帧头+首帧尾为准，这是无转义协议的标准语义）；
  **连续**重同步（未完成任何帧）超限（256 次）视为协议错误断开——成功切帧后计数
  重置，合法长连接的偶发损坏不会被累计误断；"帧头+垃圾"攻击永不完成帧，
  计数持续增长，上限照常生效
- `TCP_MAX_MESSAGE_LENGTH`（默认 64KB）为单条消息上限：line 模式无分隔符超限、
  head_tail 模式找不到帧头/帧尾超限，均视为协议错误并断开该连接（防内存 DoS）
- **空负载帧语义**：`head_tail` 模式下帧头紧邻帧尾（无负载）会回调
  `on_message(b'', addr)`（二进制协议中可能是有意义的信号帧，如心跳）；
  与 `line` 模式的"纯空帧跳过"行为不同，请按需在处理器内过滤

> 若负载内可能包含帧尾字节，请使用带转义（如字节填充）的协议——此时建议
> `TCP_FRAMING=raw` 在处理器内自行解析，或对负载做 base64/转义编码后再用 line 定界。

## 配置项

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `TCP_ENABLED` | `false` | 是否启用 TCP 服务器 |
| `TCP_HOST` | `0.0.0.0` | 监听地址 |
| `TCP_PORT` | `9000` | 监听端口 |
| `TCP_FRAMING` | `line` | 消息定界：`line` / `fixed` / `head_tail` / `raw`（非法值回退 `line`） |
| `TCP_FRAME_SEPARATOR` | `\n` | 行帧分隔符（支持转义写法，如 `\r\n`） |
| `TCP_FRAME_LENGTH` | `1024` | 固定长度帧长（`fixed` 模式，需 > 0，否则回退 `line`） |
| `TCP_FRAME_HEAD` | _空_ | 帧头字节串（`head_tail` 模式，支持二进制转义如 `\xaa\x55`；缺任一帧头/帧尾回退 `line`） |
| `TCP_FRAME_TAIL` | _空_ | 帧尾字节串（`head_tail` 模式） |
| `TCP_MAX_MESSAGE_LENGTH` | `65536` | 单条消息上限（字节），超限断开连接 |
| `TCP_MAX_CONNECTIONS` | `256` | TCP 并发连接上限（每连接一线程，**超限连接在创建线程前即被拒绝**，连接风暴零线程 churn；`≤0` 表示不限制） |
| `UDP_ENABLED` | `false` | 是否启用 UDP 服务器 |
| `UDP_HOST` | `0.0.0.0` | 监听地址 |
| `UDP_PORT` | `9001` | 监听端口 |
| `UDP_MAX_MESSAGE_LENGTH` | `65536` | 单数据报大小上限（字节；**超过此大小的数据报会被操作系统静默截断**，请按协议最大报文设置） |
| `UDP_MAX_CONCURRENCY` | `256` | UDP 并发处理数据报上限（每数据报一线程，**超限数据报在创建线程前即被丢弃**，洪泛零线程 churn；`≤0` 表示不限制） |

## 部署注意事项

- **gunicorn 多 worker**：TCP/UDP 服务器是每进程独立实例，多 worker 会重复绑定同一
  端口导致冲突。多 worker 部署时请设置 `WORKER_NUM=1`，或将协议服务器独立进程部署
  （启动 banner 会告警）
- **防火墙**：需放行 TCP/UDP 监听端口（默认 9000 / 9001）
- **暴露面**：`TCP_HOST`/`UDP_HOST` 默认 `0.0.0.0`（全网卡），与 HTTP 的
  `SERVER_HOST`（development 预设 127.0.0.1）不同——本地开发启用协议服务器时
  请确认无外部网络可达，或显式改为 `127.0.0.1`
- **连接模型**：TCP 为每连接一线程（`socketserver.ThreadingTCPServer`），与 waitress
  线程模型一致；海量长连接场景请评估线程开销（可用 `TCP_MAX_CONNECTIONS` 设上限）。
  ⚠️ **空闲连接也占用并发槽位**：建立连接但不收发数据的客户端会一直占用
  `TCP_MAX_CONNECTIONS` 名额，耗尽后新连接被拒绝——请按业务规模设置上限
- **UDP 在途数据报**：`stop()` 时已进入处理线程的数据报仍会执行完处理器
  （UDP 无连接可关闭）；stop 后立即 start 重启的极短窗口内，新旧处理器可能并发—— 
  生产切换场景建议间隔几秒再重启
- **stop() 不等在途消息**：`stop()` 立即返回，不等待处理中的 `on_message` 完成
  （TCP/UDP 同理）——关闭/重启期间在途消息可能被中断，请在业务侧保证幂等
- **UDP 回发负载上限**：UDP 数据报最大约 65507 字节，返回负载超过此值时 `sendto`
  会失败（记 WARN，不回发）；请按协议约定控制回包大小
- **开发模式 reloader**：`python server.py` + debug 时，协议服务器仅在 Werkzeug
  reloader 子进程（真实服务进程）中启动，父进程（监督者）不绑定端口——避免子进程
  二次绑定 EADDRINUSE 崩溃循环
- **无消息处理器时**：`TCP_ENABLED=true` 但 `handler/` 下未注册 `on_message`，
  服务器不会启动并打印告警（避免空转占用端口）
- **端口被占用**：启动直接报错（不静默），按日志定位冲突进程即可
- **UDP 广播**：服务器 socket 未启用 `SO_BROADCAST`，不支持向广播地址发送
- **eventlet 模式**：`SOCKETIO_ASYNC_MODE=eventlet` 时 monkey_patch 会改变线程/socket
  语义，协议服务器与之组合未经验证，建议协议服务器场景使用默认 threading 模式
