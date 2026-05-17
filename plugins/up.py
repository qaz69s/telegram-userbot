"""
插件: up —— 从 GitHub 拉取最新代码并重启

用法:
  #up          —— git pull + 重启
  #up status   —— 仅查看远程是否有更新（不拉取）
  #up --force  —— git reset --hard + pull（丢弃本地修改）
"""
import asyncio
import logging
import os
import re
import sys
from pathlib import Path

from telethon import events

from core.plugin_base import BasePlugin

logger = logging.getLogger(__name__)


def _git(*args: str) -> tuple[str, int]:
    """在 bot 根目录执行 git 命令，返回 (output, exit_code)"""
    bot_dir = Path(__file__).resolve().parent.parent
    import subprocess
    r = subprocess.run(
        ["git"] + list(args),
        cwd=str(bot_dir),
        capture_output=True, text=True, timeout=30,
    )
    out = r.stdout.strip()
    err = r.stderr.strip()
    combined = out + ("\n" + err if err else "")
    return combined, r.returncode


def _parse_commits(pull_output: str) -> list[str]:
    """从 git pull 输出中提取 commit 信息"""
    lines = []
    for line in pull_output.split("\n"):
        m = re.search(r"^[\s]{0,4}([a-f0-9]{7,})\.\.([a-f0-9]{7,})", line)
        if m:
            # 普通 pull:  old..new  branch -> branch
            continue
        m = re.match(r"^\s{4}(\w{7,})\s", line)
        if m:
            lines.append(line.strip())
    # 没有解析到具体 commit，用 raw 输出前几行
    if not lines and pull_output:
        lines = pull_output.split("\n")[:5]
    return lines


class UpPlugin(BasePlugin):
    name        = "up"
    description = "#up 从 GitHub 拉取最新代码并重启 Bot"
    version     = "1.0.0"

    async def on_startup(self):
        logger.info("[up] 插件就绪")

    async def setup(self):
        prefix = re.escape(self.config.get("CMD_PREFIX", "#"))
        actual_prefix = self.config.get("CMD_PREFIX", "#")

        @self.client.on(events.NewMessage(
            outgoing=True,
            pattern=rf"(?i)^{prefix}up(\s+.*)?$",
        ))
        async def cmd_handler(event):
            raw_text = event.raw_text.strip()
            args = raw_text[len(actual_prefix + "up"):].strip().lower()

            await event.delete()

            # ── #up status —— 仅检查远程更新 ────────────────
            if args == "status":
                out, code = _git("remote", "update")
                if code != 0:
                    msg = await event.client.send_message(
                        event.chat_id, f"❌ git remote update 失败:\n`{out}`",
                        parse_mode="md",
                    )
                    await asyncio.sleep(8)
                    await msg.delete()
                    return

                out, code = _git("log", "HEAD..origin/main", "--oneline", "--no-decorate")
                if not out.strip():
                    tip = await event.client.send_message(
                        event.chat_id, "✅ 已是最新版本"
                    )
                    await asyncio.sleep(4)
                    await tip.delete()
                else:
                    lines = out.strip().split("\n")
                    count = len(lines)
                    msg_text = f"🔄 远程有 **{count}** 个新提交:\n`{out[:1500]}`"
                    msg = await event.client.send_message(
                        event.chat_id, msg_text, parse_mode="md",
                    )
                    await asyncio.sleep(15)
                    await msg.delete()
                return

            # ── #up  (或 #up --force) —— 拉取并重启 ────────────
            force = "--force" in args

            # 先查看远程是否有更新
            out_fetch, code_fetch = _git("fetch")
            if code_fetch != 0:
                msg = await event.client.send_message(
                    event.chat_id, f"❌ git fetch 失败:\n`{out_fetch}`",
                    parse_mode="md",
                )
                await asyncio.sleep(8)
                await msg.delete()
                return

            # 检查本地是否有未提交修改
            out_stash, _ = _git("status", "--porcelain")
            has_local_changes = bool(out_stash.strip())

            if force:
                # 丢弃所有本地修改
                _git("reset", "--hard")
                _git("clean", "-fd")
                out, code = _git("pull", "--ff-only")
            elif has_local_changes:
                # 有本地修改时自动 stash
                _git("stash")
                out, code = _git("pull", "--ff-only")
                if code == 0:
                    _git("stash", "pop")
                else:
                    # pull 失败，恢复 stash
                    _git("stash", "pop")
            else:
                out, code = _git("pull", "--ff-only")

            if code != 0:
                msg = await event.client.send_message(
                    event.chat_id, f"❌ git pull 失败:\n`{out[:1000]}`",
                    parse_mode="md",
                )
                await asyncio.sleep(10)
                await msg.delete()
                return

            # 解析新提交
            commits = _parse_commits(out)
            summary = "\n".join(commits) if commits else "（无新提交）"

            # 重启前发送结果
            await event.client.send_message(
                event.chat_id,
                f"✅ 更新完成，Bot 重启中...\n```\n{summary[:1500]}\n```",
                parse_mode="md",
            )

            # 让 systemd 自动重启（Restart=always）
            logger.info("[up] git pull 完成，触发重启")
            os._exit(0)
