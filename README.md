# Telegram Userbot

基于 Telethon 的模块化 Telegram 人形 Bot，支持多账号、插件系统。

## 一键安装

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/qaz69s/telegram-userbot/main/install.sh)
```

或手动操作：

```bash
git clone https://github.com/qaz69s/telegram-userbot.git
cd telegram-userbot
bash setup.sh
```

安装流程：检查环境 → 创建虚拟环境 → 装依赖 → 登录 Telegram → 启动服务

## 命令

默认命令前缀 `#`，所有命令**在任意聊天窗口发送**即可（userbot 模式）。

| 命令 | 说明 |
|------|------|
| `#help` | 列出所有已安装插件 |
| `#dc` | 测试到 Telegram DC1~DC5 的延迟 |
| `#ai <prompt>` | AI 对话 |
| `#reboot` | 重启 Bot 服务 |

更多命令由各插件提供，见 `plugins/` 目录。

## 插件

插件放在 `plugins/` 目录下，继承 `BasePlugin` 即可。内置插件：

- `help.py` — 帮助列表
- `dc.py` — 数据中心延迟测试
- `ai.py` — AI 对话
- `reboot.py` — 重启 Bot
- `del.py` — 消息管理
- 等等

## 结构

```
telegram-userbot/
├── main.py                 # 入口（修复: connect 超时保护 + session 自动清理）
├── setup.sh                # 安装脚本
├── install.sh              # 一键安装入口
├── core/
│   ├── __init__.py
│   ├── database.py         # SQLite 数据库
│   ├── plugin_manager.py   # 插件管理器
│   └── plugin_base.py      # 插件基类
├── plugins/                # 插件目录
├── scripts/
│   ├── auth.py             # 账号登录管理
│   └── setup_reboot_sudo.sh
├── .env.example
├── requirements.txt
└── README.md
```

## 卸载

```bash
cd telegram-userbot && bash setup.sh
# 选管理 → 卸载
```

或手动：

```bash
systemctl stop telegram-userbot
systemctl disable telegram-userbot
rm -rf /root/telegram-bot
```
