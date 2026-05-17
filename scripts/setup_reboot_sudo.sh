#!/bin/bash
# 为当前运行 bot 的用户配置免密重启权限
# 用法: sudo bash scripts/setup_reboot_sudo.sh

set -euo pipefail

SERVICE="telegram-userbot"
# 获取实际运行 bot 的用户（systemd service 里配置的 User=）
BOT_USER=$(systemctl show "$SERVICE" --property=User --value 2>/dev/null || echo "$SUDO_USER")

if [ -z "$BOT_USER" ] || [ "$BOT_USER" = "root" ]; then
    BOT_USER="${SUDO_USER:-$(logname 2>/dev/null || echo '')}"
fi

if [ -z "$BOT_USER" ]; then
    echo "❌ 无法自动识别用户，请手动指定："
    echo "   sudo bash scripts/setup_reboot_sudo.sh <username>"
    exit 1
fi

# 如果传了参数就用参数
[ -n "${1:-}" ] && BOT_USER="$1"

RULE="$BOT_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart $SERVICE"
SUDOERS_FILE="/etc/sudoers.d/telegram-userbot-reboot"

echo "$RULE" > "$SUDOERS_FILE"
chmod 440 "$SUDOERS_FILE"

# 验证语法
visudo -cf "$SUDOERS_FILE" && echo "✅ 已为用户 [$BOT_USER] 配置免密重启权限" \
    || { rm -f "$SUDOERS_FILE"; echo "❌ sudoers 语法错误，已回滚"; exit 1; }
