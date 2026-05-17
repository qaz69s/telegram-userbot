#!/bin/bash
# ============================================================
#  Telegram Userbot —— 一键安装入口
#  用法: bash <(curl -fsSL https://raw.githubusercontent.com/...)
# ============================================================
set -euo pipefail

REPO_OWNER="qaz69s"
REPO_NAME="telegram-userbot"
INSTALL_DIR="${1:-${HOME}/telegram-userbot}"

R="\033[31m"; G="\033[32m"; Y="\033[33m"
B="\033[34m"; C="\033[36m"; X="\033[0m"
OK="  ${G}✔${X}"; ERR="  ${R}✘${X}"; INFO="  ${B}ℹ${X}"

echo -e "${INFO} 目标目录: ${C}$INSTALL_DIR${X}"

# 1. 克隆仓库
if [ -d "$INSTALL_DIR" ]; then
    echo -e "${INFO} 目录已存在，git pull 更新..."
    cd "$INSTALL_DIR"
    git pull
else
    echo -e "${INFO} 克隆仓库..."
    git clone "https://github.com/${REPO_OWNER}/${REPO_NAME}.git" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# 2. 运行 setup.sh
echo -e "${INFO} 启动安装向导..."
exec bash setup.sh
