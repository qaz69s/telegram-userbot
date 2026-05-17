#!/bin/bash
# ============================================================
#  Telegram Userbot —— 一键交互式安装脚本
#  用法: bash setup.sh
# ============================================================
set -euo pipefail

# ── 颜色 ─────────────────────────────────────────────────────
R="\033[31m"; G="\033[32m"; Y="\033[33m"
B="\033[34m"; C="\033[36m"; W="\033[1m";  X="\033[0m"
OK="  ${G}✔${X}"; ERR="  ${R}✘${X}"; INFO="  ${B}ℹ${X}"; ASK="  ${C}?${X}"

BOT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$BOT_DIR/.venv"
PYTHON_BIN="$(command -v python3 || command -v python || true)"
SERVICE_NAME="telegram-userbot"

# ── 工具函数 ──────────────────────────────────────────────────
section() { echo -e "\n${W}══════ $* ══════${X}"; }
pause()   { read -rp "  按 Enter 继续..." _; }

require_python() {
    if [ -z "$PYTHON_BIN" ]; then
        echo -e "${INFO} 未找到 Python3，尝试自动安装..."
        if command -v apt-get &>/dev/null; then
            apt-get update -qq
            apt-get install -y python3 python3-venv python3-pip
            PYTHON_BIN="$(command -v python3)"
            echo -e "${OK} Python3 已安装"
        elif command -v apk &>/dev/null; then
            apk add python3 py3-pip
            PYTHON_BIN="$(command -v python3)"
            echo -e "${OK} Python3 已安装"
        else
            echo -e "${ERR} 无法自动安装 Python3，请手动安装后重试"
            exit 1
        fi
    fi
    PY_VER=$($PYTHON_BIN -c "import sys; print(sys.version_info[:2])" 2>/dev/null)
    echo -e "${OK} Python: $($PYTHON_BIN --version)  ($PYTHON_BIN)"

    # 确保 python3-venv 可用（Debian/Ubuntu 系统可能需要单独安装）
    if ! $PYTHON_BIN -m ensurepip --version &>/dev/null; then
        echo -e "${INFO} 检测到缺少 python3-venv，尝试自动安装..."
        if command -v apt-get &>/dev/null; then
            PY_VER_SHORT=$($PYTHON_BIN -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
            apt-get install -y "python${PY_VER_SHORT}-venv" 2>/dev/null \
                || apt-get install -y python3-venv
            echo -e "${OK} python3-venv 安装完成"
        else
            echo -e "${ERR} 请手动安装 python3-venv 后重试"
            exit 1
        fi
    fi
}

setup_venv() {
    if [ ! -f "$VENV_DIR/bin/activate" ]; then
        if [ -d "$VENV_DIR" ]; then
            echo -e "${INFO} 虚拟环境不完整，重新创建..."
            rm -rf "$VENV_DIR"
        else
            echo -e "${INFO} 创建虚拟环境..."
        fi
        $PYTHON_BIN -m venv "$VENV_DIR"
        echo -e "${OK} 虚拟环境: $VENV_DIR"
    else
        echo -e "${OK} 虚拟环境已存在"
    fi
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"
}

install_deps() {
    echo -e "${INFO} 安装依赖（可能需要一两分钟）..."
    pip install --upgrade pip -q
    pip install -r "$BOT_DIR/requirements.txt" -q
    echo -e "${OK} 依赖安装完成"
}

init_env() {
    if [ ! -f "$BOT_DIR/.env" ]; then
        cp "$BOT_DIR/.env.example" "$BOT_DIR/.env"
        echo -e "${OK} 已创建 .env 配置文件"
    else
        echo -e "${OK} .env 已存在"
    fi
    mkdir -p "$BOT_DIR/data" "$BOT_DIR/sessions"

    # ── 命令前缀 ────────────────────────────────────────────
    echo ""
    echo -e "  ${W}命令前缀${X}：用于触发 Bot 命令（如 #ai、#help）"
    echo -e "  常用选择：${C}#  /  !  .${X}"
    read -rp "$(echo -e "  ${ASK} 输入命令前缀 [默认: #]: ")" _PREFIX
    _PREFIX="${_PREFIX:-#}"
    if grep -q "^CMD_PREFIX=" "$BOT_DIR/.env" 2>/dev/null; then
        sed -i "s|^CMD_PREFIX=.*|CMD_PREFIX=${_PREFIX}|" "$BOT_DIR/.env"
    else
        echo "CMD_PREFIX=${_PREFIX}" >> "$BOT_DIR/.env"
    fi
    echo -e "${OK} 命令前缀已设为: ${C}${_PREFIX}${X}"
}

# ── 账号管理菜单 ──────────────────────────────────────────────
account_menu() {
    section "账号管理"
    # 激活 venv
    source "$VENV_DIR/bin/activate"
    cd "$BOT_DIR"

    while true; do
        echo ""
        echo -e "  ${W}请选择操作:${X}"
        echo -e "    ${C}1.${X} 首次登录 / 检查当前 session"
        echo -e "    ${C}2.${X} 重新登录（session 失效时使用）"
        echo -e "    ${C}3.${X} 添加 / 激活账号（多账号支持）"
        echo -e "    ${C}4.${X} 查看所有已保存账号"
        echo -e "    ${C}5.${X} 退出登录（从多账号列表删除）"
        echo -e "    ${C}q.${X} 跳过，直接启动"
        echo ""
        read -rp "$(echo -e "  ${ASK} 输入选项: ")" choice

        case "$choice" in
            1) python scripts/auth.py ;;
            2) python scripts/auth.py --reauth ;;
            3) python scripts/auth.py --switch ;;
            4) python scripts/auth.py --list ;;
            5) python scripts/auth.py --logout ;;
            q|Q) break ;;
            *) echo -e "${ERR} 无效选项" ;;
        esac
    done
}

# ── 启动方式菜单 ──────────────────────────────────────────────
launch_menu() {
    section "启动方式"
    echo ""
    echo -e "  ${W}选择运行模式:${X}"
    echo -e "    ${C}1.${X} 前台运行（测试用，关闭终端即停止）"
    echo -e "    ${C}2.${X} screen 后台保活（无需 root）"
    echo -e "    ${C}3.${X} systemd 服务（推荐生产，需要 root）"
    echo -e "    ${C}q.${X} 暂不启动"
    echo ""
    read -rp "$(echo -e "  ${ASK} 输入选项: ")" choice

    source "$VENV_DIR/bin/activate"
    cd "$BOT_DIR"

    case "$choice" in
        1)
            echo -e "${INFO} 前台启动（Ctrl+C 停止）..."
            python main.py
            ;;
        2)
            if ! command -v screen &>/dev/null; then
                echo -e "${INFO} 正在安装 screen..."
                sudo apt-get install -y screen -q 2>/dev/null || \
                    (echo -e "${ERR} 请手动安装 screen: sudo apt install screen"; return)
            fi
            # 停掉旧的
            screen -S "$SERVICE_NAME" -X quit 2>/dev/null || true
            # 用循环包裹，#reboot 退出后自动重新拉起
            screen -S "$SERVICE_NAME" -d -m bash -c \
                "while true; do source $VENV_DIR/bin/activate && cd $BOT_DIR && python main.py; sleep 2; done"
            sleep 1
            if screen -list | grep -q "$SERVICE_NAME"; then
                echo -e "${OK} 已在 screen 后台启动"
                echo -e "${INFO} 查看日志:  ${C}screen -r $SERVICE_NAME${X}"
                echo -e "${INFO} 脱离窗口:  ${C}Ctrl+A D${X}"
                # screen 模式不需要 sudo 重启，#reboot 直接 os._exit 即可
                # 此处仅提示
                echo -e "${INFO} ${C}#reboot${X} 指令在 screen 模式下可用（bot 退出后自动重新拉起）"
            else
                echo -e "${ERR} screen 启动失败，请检查日志: data/bot.log"
            fi
            ;;
        3)
            install_systemd
            ;;
        q|Q)
            echo -e "${INFO} 稍后可用以下命令启动:"
            echo -e "    ${C}source $VENV_DIR/bin/activate && cd $BOT_DIR && python main.py${X}"
            ;;
        *)
            echo -e "${ERR} 无效选项"
            ;;
    esac
}

# ── systemd 安装 ──────────────────────────────────────────────
install_systemd() {
    if [ "$(id -u)" -ne 0 ]; then
        echo -e "${ERR} 安装 systemd 服务需要 root 权限"
        echo -e "${INFO} 请运行: ${C}sudo bash setup.sh${X} 并选择 systemd 模式"
        return
    fi

    VENV_PYTHON="$VENV_DIR/bin/python"
    RUN_USER=$(logname 2>/dev/null || echo "root")
    SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

    cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Telegram Userbot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$BOT_DIR
ExecStart=$VENV_PYTHON $BOT_DIR/main.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    systemctl restart "$SERVICE_NAME"
    sleep 2

    if systemctl is-active --quiet "$SERVICE_NAME"; then
        echo -e "${OK} systemd 服务已启动: ${SERVICE_NAME}"
    else
        echo -e "${ERR} 服务启动失败，查看详情:"
        echo -e "    ${C}journalctl -u $SERVICE_NAME -n 30${X}"
        return
    fi

    # 顺带配置 #reboot 插件所需的免密 sudo 权限
    setup_reboot_sudo "$RUN_USER"

    echo ""
    echo -e "  ${W}常用命令:${X}"
    echo -e "    查看状态:  ${C}systemctl status $SERVICE_NAME${X}"
    echo -e "    实时日志:  ${C}journalctl -u $SERVICE_NAME -f${X}"
    echo -e "    重启服务:  ${C}systemctl restart $SERVICE_NAME${X}"
    echo -e "    停止服务:  ${C}systemctl stop $SERVICE_NAME${X}"
}

# ── #reboot 插件免密 sudo 配置 ────────────────────────────────
setup_reboot_sudo() {
    local run_user="${1:-}"
    local sudoers_file="/etc/sudoers.d/telegram-userbot-reboot"
    local rule="$run_user ALL=(ALL) NOPASSWD: /bin/systemctl restart $SERVICE_NAME"

    echo -e "\n${INFO} 配置 #reboot 插件免密权限 (用户: $run_user)..."

    echo "$rule" > "$sudoers_file"
    chmod 440 "$sudoers_file"

    if visudo -cf "$sudoers_file" &>/dev/null; then
        echo -e "${OK} #reboot 插件免密权限配置完成"
    else
        rm -f "$sudoers_file"
        echo -e "${ERR} sudoers 配置失败，#reboot 指令将无法使用"
        echo -e "${INFO} 可手动运行: ${C}sudo bash scripts/setup_reboot_sudo.sh${X}"
    fi
}

# ── 安装流程 ──────────────────────────────────────────────────
do_install() {
    section "1 · 检查环境"
    require_python

    section "2 · 虚拟环境"
    setup_venv

    section "3 · 安装依赖"
    install_deps

    section "4 · 初始化配置"
    init_env

    section "5 · 账号登录"
    echo -e "${INFO} 首次使用需要先完成 Telegram 账号授权"
    echo -e "${INFO} 需要前往 ${C}https://my.telegram.org/apps${X} 获取 API_ID 和 API_HASH\n"
    account_menu

    section "6 · 启动服务"
    launch_menu

    section "✅ 安装完成"
    echo ""
    echo -e "  项目目录 : ${C}$BOT_DIR${X}"
    echo -e "  添加插件 : ${C}$BOT_DIR/plugins/${X}  （新建 .py 文件，继承 BasePlugin）"
    echo -e "  查看日志 : ${C}tail -f $BOT_DIR/data/bot.log${X}"
    echo -e "  账号管理 : ${C}source .venv/bin/activate && python scripts/auth.py --help${X}"
    echo ""
}

# ── 卸载 ──────────────────────────────────────────────────────
do_uninstall() {
    section "卸载确认"
    echo -e "  ${R}警告：此操作将删除以下内容：${X}"
    echo -e "    • systemd 服务 (${SERVICE_NAME})"
    echo -e "    • 虚拟环境 (.venv)"
    echo -e "    • 数据目录 (data/ 包括 session、数据库、日志)"
    echo -e "    • .env 配置文件"
    echo -e "    • sudoers 免密配置"
    echo -e "  插件代码 (plugins/) 和脚本文件不会删除"
    echo ""
    read -rp "$(echo -e "  ${ASK} 确认卸载？输入 yes 确认: ")" confirm
    if [ "$confirm" != "yes" ]; then
        echo -e "${INFO} 已取消"
        return
    fi

    # 停止并禁用服务
    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        systemctl stop "$SERVICE_NAME"
        echo -e "${OK} 已停止服务"
    fi
    if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
        systemctl disable "$SERVICE_NAME"
    fi
    rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
    systemctl daemon-reload 2>/dev/null || true
    echo -e "${OK} 已删除 systemd 服务"

    # 删除 sudoers
    rm -f "/etc/sudoers.d/telegram-userbot-reboot"
    echo -e "${OK} 已删除 sudoers 配置"

    # 删除虚拟环境
    rm -rf "$VENV_DIR"
    echo -e "${OK} 已删除虚拟环境"

    # 删除数据目录
    rm -rf "$BOT_DIR/data" "$BOT_DIR/sessions"
    echo -e "${OK} 已删除数据目录"

    # 删除 .env
    rm -f "$BOT_DIR/.env"
    echo -e "${OK} 已删除 .env 配置"

    echo ""
    echo -e "${OK} ${G}卸载完成！${X}"
    echo -e "  如需重新安装，请再次运行: ${C}bash setup.sh${X}"
}

# ── 添加新账号 ────────────────────────────────────────────────
do_add_account() {
    section "添加 Telegram 账号"
    echo -e "  ${INFO} 将登录新手机号并追加到 PHONES 列表"
    echo -e "  ${INFO} 已有账号不受影响，Bot 同时运行所有账号"
    echo ""

    source "$VENV_DIR/bin/activate"
    cd "$BOT_DIR"
    python scripts/auth.py --switch

    # 重启服务使新账号生效
    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        systemctl restart "$SERVICE_NAME" && \
            echo -e "${OK} 服务已重启，新账号已加入" || \
            echo -e "${INFO} 请手动重启: ${C}systemctl restart $SERVICE_NAME${X}"
    fi
}

# ── 管理菜单 ──────────────────────────────────────────────────
manage_menu() {
    while true; do
        section "管理菜单"
        echo ""
        echo -e "  ${W}请选择操作：${X}"
        echo -e "    ${C}1.${X} 重新安装（保留插件，重建环境和配置）"
        echo -e "    ${C}2.${X} 添加新账号（追加到多账号列表）"
        echo -e "    ${C}3.${X} 管理 Telegram 账号（登录/退出/查看列表）"
        echo -e "    ${C}4.${X} 卸载 Bot"
        echo -e "    ${C}q.${X} 退出"
        echo ""
        read -rp "$(echo -e "  ${ASK} 输入选项: ")" choice
        case "$choice" in
            1) do_install ;;
            2) do_add_account ;;
            3)
                source "$VENV_DIR/bin/activate" 2>/dev/null || true
                cd "$BOT_DIR"
                account_menu
                ;;
            4) do_uninstall ; break ;;
            q|Q) break ;;
            *) echo -e "${ERR} 无效选项" ;;
        esac
    done
}

# ── 入口菜单 ──────────────────────────────────────────────────
main() {
    clear
    echo -e "${W}"
    echo "  ████████╗███████╗██╗     ███████╗ ██████╗ ██████╗  █████╗ ███╗   ███╗"
    echo "     ██╔══╝██╔════╝██║     ██╔════╝██╔════╝ ██╔══██╗██╔══██╗████╗ ████║"
    echo "     ██║   █████╗  ██║     █████╗  ██║  ███╗██████╔╝███████║██╔████╔██║"
    echo "     ██║   ██╔══╝  ██║     ██╔══╝  ██║   ██║██╔══██╗██╔══██║██║╚██╔╝██║"
    echo "     ██║   ███████╗███████╗███████╗╚██████╔╝██║  ██║██║  ██║██║ ╚═╝ ██║"
    echo "     ╚═╝   ╚══════╝╚══════╝╚══════╝ ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝     ╚═╝"
    echo -e "${X}"
    echo -e "  ${C}Telegram Userbot 管理脚本${X}\n"

    # 判断是否已安装
    if [ -d "$VENV_DIR" ] && [ -f "$BOT_DIR/.env" ]; then
        echo -e "  ${OK} 检测到已安装的 Bot"
        echo ""
        echo -e "  ${W}请选择操作：${X}"
        echo -e "    ${C}1.${X} 管理（重装 / 换号 / 卸载）"
        echo -e "    ${C}2.${X} 重新安装"
        echo -e "    ${C}q.${X} 退出"
        echo ""
        read -rp "$(echo -e "  ${ASK} 输入选项: ")" choice
        case "$choice" in
            1) manage_menu ;;
            2) do_install ;;
            q|Q) exit 0 ;;
            *) echo -e "${ERR} 无效选项" ;;
        esac
    else
        echo -e "  ${INFO} 未检测到安装，开始安装流程..."
        sleep 1
        do_install
    fi
}

main
