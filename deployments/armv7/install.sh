#!/bin/sh
# =============================================================================
#  py_flask_server —— armv7 (Zynq-7020 / PetaLinux 2020.2) 离线安装脚本
#
#  在【板子上】运行。依赖全部在本目录里，不需要联网。
#
#  用法：
#      sh install.sh                    # 用本包自带的 app/ 或上级目录的项目
#      sh install.sh --app /mnt/usb/py_flask_server
#      sh install.sh --prefix /opt      # 改安装根目录（默认 /opt）
#
#  装完之后：
#      /etc/init.d/pyflask status
#      curl http://127.0.0.1:5000/api/v1/healthz
# =============================================================================
set -e

HERE=$(cd "$(dirname "$0")" && pwd)
PREFIX=/opt
APP_SRC=""

usage() {
    sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

while [ $# -gt 0 ]; do
    case "$1" in
        --app)    APP_SRC=$2; shift 2 ;;
        --prefix) PREFIX=$2;  shift 2 ;;
        -h|--help) usage ;;
        *) echo "未知参数: $1"; usage ;;
    esac
done

PYROOT=$PREFIX/python3.11
BASE=$PREFIX/pyflask
APPDIR=$BASE/app
VENVDIR=$BASE/venv
WHEELDIR=$BASE/wheels

echo "=============================================="
echo " py_flask_server 离线安装"
echo "  安装根目录: $PREFIX"
echo "  包所在位置: $HERE"
echo "=============================================="

# ---------------------------------------------------------------- 0. 检查
echo
echo "[0/7] 环境检查"
ARCH=$(uname -m)
echo "  架构: $ARCH"
case "$ARCH" in
    armv7l|armv6l) ;;
    *) echo "  !! 这个包是 armv7 的，当前架构是 $ARCH，继续可能失败";;
esac

[ "$(id -u)" = "0" ] || { echo "  !! 需要 root 权限运行"; exit 1; }
command -v tar >/dev/null || { echo "  !! 缺少 tar"; exit 1; }

# ---------------------------------------------------------------- 1. Python
echo
echo "[1/7] 安装 Python 3.11 运行时"
if [ -x "$PYROOT/bin/python3" ]; then
    echo "  已存在: $($PYROOT/bin/python3 --version)"
else
    PY_TGZ=$(ls "$HERE"/runtime/cpython-*-armv7-*.tar.gz 2>/dev/null | head -1)
    [ -n "$PY_TGZ" ] || { echo "  !! 找不到 runtime/ 下的 Python 压缩包"; exit 1; }
    echo "  解压 $(basename "$PY_TGZ") ..."
    mkdir -p "$PREFIX"
    TMPD=$(mktemp -d)
    tar xzf "$PY_TGZ" -C "$TMPD"
    rm -rf "$PYROOT"
    mv "$TMPD/python" "$PYROOT"
    rmdir "$TMPD"
    echo "  已安装: $($PYROOT/bin/python3 --version)"
fi

# ---------------------------------------------------------------- 2. 项目
echo
echo "[2/7] 部署应用"
if [ -z "$APP_SRC" ]; then
    if [ -f "$HERE/app/wsgi.py" ]; then
        APP_SRC="$HERE/app"
    elif [ -f "$HERE/../../wsgi.py" ]; then
        APP_SRC="$HERE/../.."
    fi
fi
[ -n "$APP_SRC" ] && [ -f "$APP_SRC/wsgi.py" ] || {
    echo "  !! 找不到项目源码（需要含 wsgi.py 的目录）"
    echo "     用 --app <项目路径> 指定，或把项目拷到 $HERE/app/"
    exit 1
}
echo "  来源: $APP_SRC"
mkdir -p "$BASE"
rm -rf "$APPDIR"
mkdir -p "$APPDIR"
# 只拷运行需要的东西（不用 tar --exclude：BusyBox 的 tar 对这个选项支持不一致）
for item in flask_server webui storage; do
    [ -d "$APP_SRC/$item" ] && cp -r "$APP_SRC/$item" "$APPDIR"/
done
for item in wsgi.py requirements.txt; do
    [ -f "$APP_SRC/$item" ] && cp -f "$APP_SRC/$item" "$APPDIR"/
done
# 清掉缓存目录
for d in $(find "$APPDIR" -name __pycache__ -type d 2>/dev/null); do
    rm -rf "$d"
done
[ -f "$APPDIR/wsgi.py" ] || { echo "  !! 部署失败：wsgi.py 没拷过来"; exit 1; }
echo "  已部署: $(du -sh "$APPDIR" | cut -f1)"

# ---------------------------------------------------------------- 3. 虚拟环境
echo
echo "[3/7] 创建虚拟环境并离线安装依赖"
if [ ! -x "$VENVDIR/bin/python" ]; then
    "$PYROOT/bin/python3" -m venv "$VENVDIR"
fi
mkdir -p "$WHEELDIR"
cp -f "$HERE"/wheels/*.whl "$WHEELDIR"/ 2>/dev/null || true
"$VENVDIR/bin/python" -m pip install -q --no-index --find-links "$WHEELDIR" \
    -r "$APPDIR/requirements.txt"
echo "  已安装 $("$VENVDIR/bin/python" -m pip list 2>/dev/null | wc -l) 个包"

# ---------------------------------------------------------------- 4. 配置
echo
echo "[4/7] 配置文件"
if [ -f "$APPDIR/.env" ]; then
    echo "  已存在 .env，保留不动"
else
    if [ -f "$HERE/config/env.example" ]; then
        cp "$HERE/config/env.example" "$APPDIR/.env"
        echo "  已从 config/env.example 生成 .env"
        echo "  ⚠ 请修改 $APPDIR/.env 里的 SECRET_KEY"
    else
        echo "  !! 缺 config/env.example，跳过（服务可能起不来）"
    fi
fi

# ---------------------------------------------------------------- 5. 服务
echo
echo "[5/7] 安装 init 服务"
[ -f "$HERE/scripts/pyflask" ] || { echo "  !! 缺 scripts/pyflask"; exit 1; }
cp -f "$HERE/scripts/pyflask" /etc/init.d/pyflask
chmod 755 /etc/init.d/pyflask
if command -v update-rc.d >/dev/null 2>&1; then
    update-rc.d pyflask defaults >/dev/null 2>&1 || true
    echo "  已注册开机自启（rc5.d）"
else
    ln -sf ../init.d/pyflask /etc/rc5.d/S20pyflask 2>/dev/null || true
    echo "  已手动创建 rc5.d 链接"
fi

# ---------------------------------------------------------------- 6. 启动
echo
echo "[6/7] 启动服务"
/etc/init.d/pyflask restart || {
    echo "  !! 启动失败，看日志： /var/log/pyflask.log"
    tail -30 /var/log/pyflask.log
    exit 1
}

# ---------------------------------------------------------------- 7. 自检
echo
echo "[7/7] 自检"
PORT=$(sed -n 's/^[[:space:]]*SERVER_PORT[[:space:]]*=[[:space:]]*\([0-9]\+\).*/\1/p' "$APPDIR/.env" 2>/dev/null | tail -1)
[ -n "$PORT" ] || PORT=5000
IP=$(ip route get 1 2>/dev/null | sed -n 's/.* src \([0-9.]\+\).*/\1/p' | head -1)
echo "  监听端口: $PORT"
echo "  本机地址: ${IP:-127.0.0.1}"
if wget -q -T 3 -O /dev/null "http://127.0.0.1:$PORT/api/v1/healthz"; then
    echo "  健康检查: 通过"
else
    echo "  健康检查: 失败（看 /var/log/pyflask.log）"
fi

echo
echo "=============================================="
echo " 安装完成"
echo
echo "  访问:    http://${IP:-<板子IP>}:$PORT/"
echo "  接口:    http://${IP:-<板子IP>}:$PORT/api/v1/health"
echo "  文档:    http://${IP:-<板子IP>}:$PORT/docs"
echo "  指标:    http://${IP:-<板子IP>}:$PORT/metrics"
echo
echo "  服务:    /etc/init.d/pyflask {start|stop|restart|status}"
echo "  日志:    /var/log/pyflask.log"
echo "  配置:    $APPDIR/.env"
echo "=============================================="
