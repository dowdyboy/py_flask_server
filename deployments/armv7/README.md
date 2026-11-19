# armv7 部署包 —— Zynq-7020 / PetaLinux 2020.2

> 目标板：Xilinx Zynq-7020（Cortex-A9 ×2 @666MHz, NEON, 1GB RAM）
> 系统：PetaLinux 2020.2，glibc 2.30，**sysvinit（不是 systemd）**
> 特性：**完全离线** —— 包里自带 Python 运行时和全部依赖，板子不需要联网

---

## 1. 这套东西解决什么问题

板子自带 **Python 3.7.6 且没有 pip**，而本项目要求 **Python ≥ 3.10**。
同时板子没有外网，`pip install` 没法用。

所以这个包把三样东西都带上了：

| 组成 | 说明 |
|---|---|
| Python 3.11.18 运行时 | python-build-standalone 的 armv7 预编译版，解压即用，不依赖板子的 Python |
| 27 个依赖 wheel | 全部 pure-python（只有 MarkupSafe 是原生轮子，也有 armv7 版），**无需编译** |
| 安装/服务脚本 | 一键部署 + SysV 服务 + 开机自启 |

---

## 2. 包内容

```
deployments/armv7/
├── README.md                                    本文档
├── install.sh                                   一键安装（在板子上跑）
├── uninstall.sh                                 卸载
├── runtime/
│   └── cpython-3.11.18+...-armv7-...-install_only.tar.gz   Python 运行时（44MB）
├── wheels/
│   └── *.whl                                    27 个依赖（3.9MB）
├── scripts/
│   └── pyflask                                  SysV init 服务脚本
├── config/
│   └── env.example                              板端推荐配置
├── requirements.lock.txt                        实际安装的版本（可复现）
└── SHA256SUMS                                   完整性校验
```

---

## 3. 部署前检查

在板子上执行：

```bash
uname -m                     # 应为 armv7l
ldd --version | head -1      # 无 ldd 就跳过，一般为 glibc 2.30
df -h / | tail -1            # 根分区至少留 250MB（Python 168MB + 应用）
free -m | sed -n 2p          # 至少 100MB 可用
```

| 检查项 | 预期 | 不满足怎么办 |
|---|---|---|
| 架构 | `armv7l` | 这个包是 armv7 的；aarch64 需要用 aarch64 的 Python 运行时 |
| 根分区剩余 | ≥ 250MB | 清理 `/opt` 或换更大的 SD 卡 |
| 可用内存 | ≥ 100MB | 服务稳定占用约 45MB |

> ⚠ **注意 `/tmp` 是 tmpfs**（`/tmp -> /var/tmp -> /var/volatile`），重启就清空。
> 所以应用**必须**装在 `/opt`（rootfs，持久），不要放 `/tmp`。

---

## 4. 快速安装（推荐）

### 4.1 把包传到板子上

包大约 48MB，用 scp / U 盘 / SD 卡都行。

```powershell
# 电脑侧（Windows，包在项目里）
scp -r deployments\armv7 root@192.168.0.130:/tmp/deploy-armv7
```

```bash
# 板侧
mkdir -p /opt/deploy-armv7
cp -r /tmp/deploy-armv7/. /opt/deploy-armv7/    # 或直接从 U 盘解压
cd /opt/deploy-armv7
```

### 4.2 准备项目源码

`install.sh` 需要拿到项目源码（含 `wsgi.py`）。三种方式任选：

```bash
# 方式 A：项目已经在板子上
sh install.sh --app /path/to/py_flask_server

# 方式 B：把项目拷进包里（离线场景常用）
cp -r /path/to/py_flask_server /opt/deploy-armv7/app
sh install.sh

# 方式 C：包在项目仓库里（deployments/armv7 的上两级就是项目）
sh install.sh
```

### 4.3 一键安装

```bash
cd /opt/deploy-armv7
sh install.sh --app /path/to/py_flask_server
```

脚本会依次做：解压 Python → 部署应用 → 建 venv 装依赖 → 生成 `.env` →
安装 init 服务并注册开机自启 → 启动 → 健康检查。

成功后会打印访问地址。

### 4.4 改配置（必须）

```bash
vi /opt/pyflask/app/.env
```

**至少要改 `SECRET_KEY`**：

```bash
python3 -c "import secrets;print(secrets.token_urlsafe(48))"
```

改完重启：

```bash
/etc/init.d/pyflask restart
```

---

## 5. 手动安装（不跑脚本，逐步操作）

如果自动脚本出问题，按这个顺序手工做。

### 5.1 安装 Python 运行时

```bash
mkdir -p /opt
cd /opt/deploy-armv7/runtime
tar xzf cpython-3.11.18+20261110-armv7-unknown-linux-gnueabihf-install_only.tar.gz
mv python /opt/python3.11
/opt/python3.11/bin/python3 --version        # 应输出 Python 3.11.18
/opt/python3.11/bin/python3 -c "import ssl, sqlite3; print('ok')"
```

### 5.2 部署应用

```bash
mkdir -p /opt/pyflask/app /opt/pyflask/wheels
cp -r /path/to/py_flask_server/flask_server \
      /path/to/py_flask_server/webui \
      /path/to/py_flask_server/storage \
      /path/to/py_flask_server/wsgi.py \
      /path/to/py_flask_server/requirements.txt \
      /opt/pyflask/app/
cp /opt/deploy-armv7/wheels/*.whl /opt/pyflask/wheels/
```

### 5.3 建虚拟环境并装依赖

```bash
/opt/python3.11/bin/python3 -m venv /opt/pyflask/venv
/opt/pyflask/venv/bin/python -m pip install \
    --no-index --find-links /opt/pyflask/wheels \
    -r /opt/pyflask/app/requirements.txt
/opt/pyflask/venv/bin/python -m pip list | wc -l      # 应约 29
```

### 5.4 写配置

```bash
cp /opt/deploy-armv7/config/env.example /opt/pyflask/app/.env
vi /opt/pyflask/app/.env          # 改 SECRET_KEY
```

### 5.5 装服务

```bash
cp /opt/deploy-armv7/scripts/pyflask /etc/init.d/pyflask
chmod 755 /etc/init.d/pyflask
update-rc.d pyflask defaults          # 注册开机自启
/etc/init.d/pyflask start             # 会等端口就绪（约 10 秒）
curl http://127.0.0.1:5000/api/v1/healthz
```

### 5.6 手工验证

```bash
/etc/init.d/pyflask status
curl -s http://127.0.0.1:5000/api/v1/health
curl -s http://127.0.0.1:5000/api/v1/healthz
curl -s http://127.0.0.1:5000/metrics | head
tail -20 /var/log/pyflask.log
```

---

## 6. 服务管理

这块板子是 **sysvinit**，没有 `systemctl`。

```bash
/etc/init.d/pyflask start       # 启动（等端口就绪才返回）
/etc/init.d/pyflask stop        # 停止（先 SIGTERM，20 秒后 SIGKILL）
/etc/init.d/pyflask restart     # 重启
/etc/init.d/pyflask status      # 状态（运行中返回 0，未运行返回 3）
```

开机自启靠 `/etc/rc5.d/S20pyflask -> ../init.d/pyflask`（runlevel 5）。
`update-rc.d pyflask defaults` 会自动创建。

**已验证**：断电重启后服务自动拉起，约 20 秒可访问。

### 日志

```bash
tail -f /var/log/pyflask.log
```

> `/var/log` 是指向 `/var/volatile/log` 的 tmpfs，**重启后清空**。
> 需要持久化日志就改 init 脚本里的 `LOGFILE` 到 `/opt/pyflask/logs/`。

---

## 7. 性能实测（这块板子上的真实数字）

| 项目 | 结果 |
|---|---|
| 冷启动（exec → 可服务） | **10.2 秒** |
| `/api/v1/healthz` | **13 ms** |
| `/api/v1/health` | **12.6 ms** |
| `/metrics` | 30 ms |
| 并发 4 线程 × 100 请求 | **87 req/s**，平均 11.5 ms |
| 稳态内存 | **45 MB** |
| 服务进程线程数 | 6 |
| 磁盘占用 | Python 168MB + 应用 1MB + venv 约 30MB |

### 登录/注册慢是正常的

`AUTH_PBKDF2_ITERATIONS` 默认 **600000**，在 A9 上每次哈希要 **3.1 秒**。
配置里已经调成 **150000**（约 0.8 秒）。

如果还是嫌慢（内网环境）：

```bash
AUTH_PBKDF2_ITERATIONS=50000     # 约 0.26 秒
```

> 这是安全性和体验的权衡。OWASP 建议 600k（x86 上约 100ms），
> 但 A9 算力只有 x86 的百分之一，照搬推荐值会导致登录超时。

---

## 8. 配置说明

完整示例见 `config/env.example`。几个关键项：

| 变量 | 板端建议 | 说明 |
|---|---|---|
| `SERVER_HOST` | `0.0.0.0` | 允许局域网访问 |
| `SERVER_PORT` | `5000` | HTTP 端口 |
| `THREAD_NUM` | `4` | waitress 工作线程（A9 双核，4 够用） |
| `SECRET_KEY` | **必须改** | 会话/CSRF 签名密钥 |
| `AUTH_PBKDF2_ITERATIONS` | `150000` | 越大越安全越慢 |
| `AUTH_STORE` | `memory` / `sqlalchemy` | memory 重启丢用户数据 |
| `SOCKETIO_ENABLED` | `false` | waitress 不支持 WebSocket |
| `TCP_ENABLED` / `UDP_ENABLED` | `false` | 需要协议服务时再开 |
| `METRICS_ENABLED` | `true` | `/metrics` 监控 |

### 想让用户数据持久化

```bash
SQLALCHEMY_URI=sqlite:////opt/pyflask/app/storage/app.db
AUTH_STORE=sqlalchemy
```

然后初始化表（如果项目提供了迁移脚本）：

```bash
cd /opt/pyflask/app
/opt/pyflask/venv/bin/python -c "
from flask_server import app
from flask_server.module import sqlalchemy
db = sqlalchemy()
with app.app_context():
    db.create_all()
"
```

---

## 9. 常见问题

| 现象 | 原因 | 解决 |
|---|---|---|
| `install.sh` 报 `需要 root 权限` | 不是 root | `su -` 后用 root 跑 |
| 启动后 `status` 显示未运行 | 应用启动报错 | `tail -50 /var/log/pyflask.log` |
| `508` / 端口占用 | 之前手工起的进程还在 | `ps \| grep wsgi.py`，`kill` 掉 |
| `ImportError: No module named xxx` | venv 没装全 | 重跑 `pip install --no-index --find-links /opt/pyflask/wheels -r requirements.txt` |
| 网页打不开，本机 curl 正常 | 防火墙 / 不同网段 | 检查 `SERVER_HOST=0.0.0.0`，`ping` 板子 |
| 登录要 3 秒 | PBKDF2 迭代太高 | 调 `AUTH_PBKDF2_ITERATIONS` |
| 重启后用户没了 | `AUTH_STORE=memory` | 改 `sqlalchemy` + 持久化 DB |
| 重启后应用没了 | 应用装在了 `/tmp` | 必须在 `/opt` |
| `update-rc.d: not found` | 极简 rootfs | 手工 `ln -sf ../init.d/pyflask /etc/rc5.d/S20pyflask` |

---

## 10. 升级与卸载

### 升级应用（只换代码）

```bash
# 传新代码上去覆盖
scp -r flask_server webui wsgi.py root@<ip>:/opt/pyflask/app/
/etc/init.d/pyflask restart
```

依赖没变就不用动 venv。

### 升级依赖

```bash
# 在有网机器上下载新的 armv7 wheel（同一套 --platform 参数）
pip download --platform manylinux2014_armv7l --python-version 3.11 \
             --only-binary=:all: -r requirements.txt -d wheels-new/
# 传上去后
/opt/pyflask/venv/bin/python -m pip install --no-index \
    --find-links /opt/pyflask/wheels -r /opt/pyflask/app/requirements.txt
/etc/init.d/pyflask restart
```

### 卸载

```bash
cd /opt/deploy-armv7
sh uninstall.sh                 # 全部删除
sh uninstall.sh --keep-python   # 保留 Python 运行时
```

---

## 11. 已知限制

1. **waitress 不支持 WebSocket**。需要 WebSocket 就得用 `server.py`（Flask 开发服务器/eventlet），
   但那样就失去了生产级可靠性。本项目默认关闭 SocketIO。
2. **单进程多线程**。`AUTH_STORE=memory` 和内存缓存都是进程内的，
   多进程部署（gunicorn 多 worker）会导致 token 不共享 —— 要么单进程，要么上 Redis。
3. **没有 HTTPS**。板子是 HTTP 明文。要对外暴露请在前面挂 nginx 或走内网。
4. **Python 运行时不能随便挪**。`venv` 里的路径是绝对路径，移动 `/opt/pyflask` 后需要
   重建 venv（或 `python -m venv --upgrade`）。
5. **`/var/log` 在内存里**。日志重启即失，排查启动问题时要趁早看。

---

## 12. 这台机器上的安装记录（供对照）

```
/opt/python3.11/          Python 3.11.18（168MB）
/opt/pyflask/app/         项目（1.1MB）
/opt/pyflask/venv/        虚拟环境（29 个包）
/opt/pyflask/wheels/      离线 wheel（留档，重装用）
/etc/init.d/pyflask       SysV 服务
/etc/rc5.d/S20pyflask     开机自启
/var/log/pyflask.log      运行日志（tmpfs）
```

验证记录：

- `update-rc.d pyflask defaults` → 生成 `S20pyflask` ✅
- `start` 等待就绪 8~11 秒后返回 ✅
- `restart` / `stop` / `status`（未运行返回 3）✅
- **断电重启后自动拉起，接口正常** ✅
- **从零跑 `install.sh` 全流程通过**（卸载 → 传包 → 安装 → 自检）✅

最后一次从零安装的完整输出（供对照）：

```
[0/7] 环境检查          架构: armv7l
[1/7] 安装 Python 3.11  已安装: Python 3.11.18
[2/7] 部署应用          已部署: 420.0K
[3/7] 虚拟环境          已安装 29 个包（离线）
[4/7] 配置文件          已从 config/env.example 生成 .env
[5/7] 安装 init 服务    已注册开机自启（rc5.d）
[6/7] 启动服务          pyflask 已启动 (pid 822, 11s)
[7/7] 自检              健康检查: 通过
```

局域网访问验证（从另一台机器）：

```bash
curl http://192.168.0.130:5000/api/v1/health
# {"code":0,"data":{"db":"ok","redis":"ok","status":"up","uptime":23,"version":"v1"},"msg":"成功"}
# HTTP 200, 15ms
```

安装后磁盘占用：`/opt/python3.11` 126MB + `/opt/pyflask` 68MB（含 venv 和留档的 wheels）。

---

## 13. 关于放进 git

这个包 48MB（Python 运行时 44MB + wheels 3.9MB），比一般源码大很多。

三种处理方式：

1. **直接提交**（最简单）
   48MB 在 GitHub 单文件 100MB 限制内，能用。仓库会变重，但 clone 一次就好。
   已加 `.gitattributes` 把 `*.tar.gz` / `*.whl` 标成 binary，避免 diff 卡顿。

2. **用 Git LFS**
   ```
   git lfs track "deployments/armv7/runtime/*.tar.gz"
   git lfs track "deployments/armv7/wheels/*.whl"
   ```

3. **不进 git，随 U 盘/SD 卡走**
   在 `.gitignore` 里加：
   ```
   deployments/armv7/runtime/
   deployments/armv7/wheels/
   ```
   文档和脚本进 git，二进制单独保管。
   需要重建时用下面第 14 节的命令重新下载。

---

## 14. 怎么重新生成这个包（换版本 / 换板子时）

在有网的 x86 Linux（或 WSL）上：

```bash
# 1) 换 Python 版本：去 python-build-standalone 找对应的 armv7 包
#    https://github.com/astral-sh/python-build-standalone/releases
#    文件名形如 cpython-3.11.18+20261110-armv7-unknown-linux-gnueabihf-install_only.tar.gz
mkdir -p runtime && cd runtime
curl -LO <上面的下载地址>

# 2) 下载 armv7 wheel
cd ..
pip download \
    --platform manylinux2014_armv7l \
    --platform manylinux_2_17_armv7l \
    --platform linux_armv7l \
    --python-version 3.11 \
    --only-binary=:all: \
    -r ../../requirements.txt -d wheels/

# 3) 重新算校验和
cd .. && sha256sum runtime/*.tar.gz wheels/*.whl > SHA256SUMS
```

> `--platform` 是关键：不加的话 pip 会按当前机器（x86_64）下载，装到板子上会报
> `not a supported wheel on this platform`。

换 aarch64 板子（如 ZynqMP）同理，把 `armv7` 换成 `aarch64`，
Python 包换成 `aarch64-unknown-linux-gnu`。
