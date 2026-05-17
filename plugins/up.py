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


def _parse_commits(pull_output: str) -> str:
    """从 git pull 输出中提取简短的 commit 列表"""
    lines = []
    for line in pull_output.split("\n"):
        m = re.match(r"^\s{4}(\w{7,})\s", line)
        if m:
            lines.append(line.strip())
    if not lines and pull_output.strip():
        # fallback: raw 前 5 行
        lines = [l.strip() for l in pull_output.strip().split("\n") if l.strip()][:5]
    return "\n".join(lines) if lines else "（无新提交）"


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

            msg = await event.client.send_message(
                event.chat_id, "up: 检查远程仓库...", parse_mode="md",
            )

            # ── #up status —— 仅检查远程更新 ────────────────
            if args == "status":
                out, code = _git("remote", "update")
                if code != 0:
                    await msg.edit(f"up: git remote update 失败\n`{out[:500]}`")
                    await asyncio.sleep(8)
                    await msg.delete()
                    return

                out, code = _git("log", "HEAD..origin/main", "--oneline", "--no-decorate")
                if not out.strip():
                    await msg.edit("up: 已是最新版本")
                    await asyncio.sleep(4)
                    await msg.delete()
                else:
                    lines = out.strip().split("\n")
                    await msg.edit(f"up: 远程有 {len(lines)} 个新提交\n`{out[:1500]}`")
                    await asyncio.sleep(15)
                    await msg.delete()
                return

            # ── #up  (或 #up --force) —— 拉取并重启 ────────────
            force = "--force" in args

            await msg.edit("up: 获取远程更新...")

            out_fetch, code_fetch = _git("fetch")
            if code_fetch != 0:
                await msg.edit(f"up: git fetch 失败\n`{out_fetch[:500]}`")
                await asyncio.sleep(8)
                await msg.delete()
                return

            await msg.edit("up: 拉取最新代码...")

            # 检查本地是否有未提交修改
            out_stash, _ = _git("status", "--porcelain")
            has_local_changes = bool(out_stash.strip())

            if force:
                _git("reset", "--hard")
                _git("clean", "-fd")
                out, code = _git("pull", "--ff-only")
            elif has_local_changes:
                _git("stash")
                out, code = _git("pull", "--ff-only")
                if code == 0:
                    _git("stash", "pop")
                else:
                    _git("stash", "pop")
            else:
                out, code = _git("pull", "--ff-only")

            if code != 0:
                await msg.edit(f"up: git pull 失败\n`{out[:1000]}`")
                await asyncio.sleep(10)
                await msg.delete()
                return

            # 更新完成 → 显示 commit 信息 → 重启
            commits = _parse_commits(out)
            await msg.edit(
                f"up: 更新完成\n```\n{commits[:1500]}\n```\nup: 重启中..."
            )

            # 等 2 秒让用户看到最终状态，再退出让 systemd 重启
            await asyncio.sleep(2)
            logger.info("[up] git pull 完成，触发重启")
            os._exit(0)
