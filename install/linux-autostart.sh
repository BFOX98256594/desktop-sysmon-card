#!/usr/bin/env bash
# 安装 Linux 开机自启（GNOME / 通用 autostart）
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$DIR/linux/sysmon-card.py"
AUTOSTART="$HOME/.config/autostart/sysmon-card.desktop"

mkdir -p "$(dirname "$AUTOSTART")"
cat > "$AUTOSTART" <<DESKTOP
[Desktop Entry]
Type=Application
Name=系统状态卡片
Comment=桌面状态卡片：磁盘、内存、CPU、网络、电池
Exec=/bin/sh -c "sleep 3; GDK_BACKEND=x11 exec python3 $SCRIPT"
Terminal=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
DESKTOP

chmod 644 "$AUTOSTART"
echo "已写入开机自启：$AUTOSTART"
echo "立即启动：GDK_BACKEND=x11 nohup python3 $SCRIPT >/dev/null 2>&1 &"
