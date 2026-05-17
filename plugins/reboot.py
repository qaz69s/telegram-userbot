"""
插件: reboot —— 发送 #reboot 远程重启 bot

自动检测运行模式：
  - systemd 模式：sudo systemctl restart telegram-userbot
  - screen  模式：screen 内直接退出，由 screen 命令重新拉起
  - 其他模式：提示不支持
"""
import asyncio
import json
import logging
import os
import re
import subprocess
from pathlib import Path

from telethon import events
from core.plugin_base import BasePlugin

logger = logging.getLogger(__name__)

SERVICE      = "telegram-userbot"
PENDING_FILE = Path("data/reboot_pending.json")


def _detect_mode() -> str:
    """检测当前运行模式：systemd / screen / foreground"""
    # 判断是否由 systemd 管理
    if os.getenv("INVOCATION_ID"):           # systemd 注入此变量
        return "systemd"
    # 判断是否在 screen 会话内
    if os.getenv("STY"):                     # screen 注入 STY=<pid>.<name>
        return "screen"
    return "foreground"


class RebootPlugin(BasePlugin):
    name        = "reboot"
    description = "#reboot 重启 bot（自动适配 systemd / screen）"
    version     = "1.2.0"

    async def on_startup(self):
        mode = _detect_mode()
        logger.info("[reboot] 插件就绪，运行模式: %s", mode)

        # 更新上次重启留下的提示消息为“已完成”
        if PENDING_FILE.exists():
            data = None
            try:
                data = json.loads(PENDING_FILE.read_text())
                # 立即删除文件，防止多账号并发时重复处理（删除在第一个 await 之前，asyncio 单线程安全）
                PENDING_FILE.unlink(missing_ok=True)
                await self.client.edit_message(
                    data["chat_id"], data["msg_id"],
                    "重启完成。"
                )
                await asyncio.sleep(3)
                await self.client.delete_messages(data["chat_id"], data["msg_id"])
                logger.info("[reboot] 已更新并删除重启提示消息")
            except Exception as e:
                logger.warning("[reboot] 处理提示消息失败: %s", e)
                if data:
                    try:
                        await self.client.delete_messages(data["chat_id"], data["msg_id"])
                    except Exception:
                        pass

    async def setup(self):
        prefix = re.escape(self.config.get("CMD_PREFIX", "#"))

        @self.client.on(events.NewMessage(outgoing=True, pattern=rf"(?i)^{prefix}reboot$"))
        async def handler(event):
            try:
                await event.delete()
            except Exception as e:
                logger.warning("[reboot] 删除指令消息失败: %s", e)

            mode = _detect_mode()
            try:
                if mode == "systemd":
                    await self._reboot_systemd(event.chat_id)
                elif mode == "screen":
                    await self._reboot_screen(event.chat_id)
                else:
                    await self.client.send_message(
                        event.chat_id,
                        "前台运行模式不支持 #reboot\n请改用 systemd 或 screen 模式"
                    )
            except Exception as e:
                logger.exception("[reboot] 重启失败: %s", e)
                await self.client.send_message(event.chat_id, f"重启失败：{e}")

    # ── systemd 重启 ──────────────────────────────────────────

    async def _reboot_systemd(self, chat_id: int):
        tip = await self.client.send_message(chat_id, "正在重启 Bot...")
        self._save_pending(chat_id, tip.id)
        logger.info("[reboot] systemd 模式，执行 systemctl restart")
        await asyncio.sleep(1)
        subprocess.Popen(
            ["sudo", "systemctl", "restart", SERVICE],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    # ── screen 重启 ───────────────────────────────────────────

    async def _reboot_screen(self, chat_id: int):
        tip = await self.client.send_message(chat_id, "正在重启 Bot...")
        self._save_pending(chat_id, tip.id)
        logger.info("[reboot] screen 模式，退出进程由 screen 命令重启")
        await asyncio.sleep(1)
        # screen 的启动命令里包含了重启逻辑（见 setup.sh launch_screen）
        # 直接退出当前进程，screen 会按配置自动重新执行启动命令
        os._exit(0)

    # ── 工具 ─────────────────────────────────────────────────

    def _save_pending(self, chat_id: int, msg_id: int):
        PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
        PENDING_FILE.write_text(json.dumps({"chat_id": chat_id, "msg_id": msg_id}))
