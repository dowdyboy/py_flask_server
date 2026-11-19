#!/bin/sh
# =============================================================================
#  py_flask_server 卸载脚本（在板子上运行）
#
#  用法：
#      sh uninstall.sh              # 停服务 + 删服务脚本 + 删 /opt/pyflask
#      sh uninstall.sh --keep-python  # 保留 Python 运行时（/opt/python3.11）
# =============================================================================
set -e

KEEP_PYTHON=0
[ "$1" = "--keep-python" ] && KEEP_PYTHON=1

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

echo "==> 删除应用与虚拟环境"
rm -rf /opt/pyflask

if [ "$KEEP_PYTHON" = "1" ]; then
    echo "==> 保留 Python：/opt/python3.11"
else
    echo "==> 删除 Python 运行时"
    rm -rf /opt/python3.11
fi

rm -f /var/log/pyflask.log
echo "==> 完成"
