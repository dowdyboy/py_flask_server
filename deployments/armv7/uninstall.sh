#!/bin/sh
# =============================================================================
#  py_flask_server 卸载脚本（在板子上运行）
#
#  用法：
#      sh uninstall.sh                    # 停服务 + 删服务脚本 + 删 /opt/pyflask
#      sh uninstall.sh --keep-python      # 保留 Python 运行时（/opt/python3.11）
#      sh uninstall.sh --prefix /srv/opt  # 安装时用了 --prefix 的，卸载指定同一目录
#
#  不指定 --prefix 时，从已安装的 /etc/init.d/pyflask 里读回安装根目录
#  （install.sh 会写入），读不到按默认 /opt 处理。
# =============================================================================
set -e

KEEP_PYTHON=0
PREFIX=""
while [ $# -gt 0 ]; do
    case "$1" in
        --keep-python) KEEP_PYTHON=1; shift ;;
        --prefix)      PREFIX=$2; shift 2 ;;
        -h|--help)     sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)             echo "未知参数: $1"; exit 2 ;;
    esac
done

if [ -z "$PREFIX" ]; then
    PREFIX=$(sed -n 's/^PREFIX=\(.*\)$/\1/p' /etc/init.d/pyflask 2>/dev/null | head -1)
    [ -n "$PREFIX" ] || PREFIX=/opt
fi
# 与 install.sh 同样的规范化：去掉尾部斜杠，拒绝根目录
while [ "${PREFIX%/}" != "$PREFIX" ]; do PREFIX=${PREFIX%/}; done
[ -n "$PREFIX" ] || { echo "  !! 安装根目录不能是 /"; exit 1; }

BASE=$PREFIX/pyflask
PYROOT=$PREFIX/python3.11

echo "==> 停止服务"
if [ -x /etc/init.d/pyflask ]; then
    /etc/init.d/pyflask stop || true
fi

echo "==> 取消开机自启"
if command -v update-rc.d >/dev/null 2>&1; then
    update-rc.d -f pyflask remove 2>/dev/null || true
fi
rm -f /etc/rc5.d/S20pyflask /etc/rc5.d/K20pyflask

echo "==> 删除服务脚本"
rm -f /etc/init.d/pyflask

echo "==> 删除应用与虚拟环境（$BASE）"
rm -rf "$BASE"

if [ "$KEEP_PYTHON" = "1" ]; then
    echo "==> 保留 Python：$PYROOT"
else
    echo "==> 删除 Python 运行时（$PYROOT）"
    rm -rf "$PYROOT"
fi

rm -f /var/log/pyflask.log
echo "==> 完成"
