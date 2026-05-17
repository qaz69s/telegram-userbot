"""
插件: up —— 从 GitHub 拉取最新代码并重启

用法:
  #up          —— git pull + 重启
  #up status   —— 仅查看远程是否有更新（不拉取）
  #up --force  —— git reset --hard + pull（丢弃本地修改）
"""
import asyncio
import json
import logging
import os
import re
from pathlib import Path

from telethon import events

from core.plugin_base import BasePlugin

logger = logging.getLogger(__name__)

_PENDING_FILE = Path(__file__).resolve().parent.parent / "data" / ".up_pending_delete"


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
        lines = [l.strip() for l in pull_output.strip().split("\n") if l.strip()][:5]
    return "\n".join(lines) if lines else ""


class UpPlugin(BasePlugin):
    name        = "up"
    description = "#up 从 GitHub 拉取最新代码并重启 Bot"
    version     = "1.0.0"

    async def on_startup(self):
        """重启后清理上一次的提示消息"""
        if _PENDING_FILE.exists():
            try:
                data = json.loads(_PENDING_FILE.read_text())
                chat_id = data["chat_id"]
                msg_id = data["msg_id"]
                async with self.client:
                    await self.client.delete_messages(chat_id, [msg_id])
                    logger.info("[up] 已删除上一次重启提示消息")
            except Exception as e:
                logger.warning("[up] 清理重启提示消息失败: %s", e)
            finally:
                _PENDING_FILE.unlink(missing_ok=True)

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
                event.chat_id, "检查远程仓库...", parse_mode="md",
            )

            # ── #up status —— 仅检查远程更新 ────────────────
            if args == "status":
                out, code = _git("remote", "update")
                if code != 0:
                    await msg.edit(f"更新检查失败\n`{out[:500]}`")
                    await asyncio.sleep(8)
                    await msg.delete()
                    return

                out, code = _git("log", "HEAD..origin/main", "--oneline", "--no-decorate")
                if not out.strip():
                    await msg.edit("已是最新版本")
                    await asyncio.sleep(4)
                    await msg.delete()
                else:
                    lines = out.strip().split("\n")
                    await msg.edit(f"远程有 {len(lines)} 个新提交\n`{out[:1500]}`")
                    await asyncio.sleep(15)
                    await msg.delete()
                return

            # ── #up  —— 拉取并重启 ────────────
            force = "--force" in args

            await msg.edit("获取远程更新...")

            out_fetch, code_fetch = _git("fetch")
            if code_fetch != 0:
                await msg.edit(f"获取远程更新失败\n`{out_fetch[:500]}`")
                await asyncio.sleep(8)
                await msg.delete()
                return

            # 先看有没有新提交
            out_log, _ = _git("log", "HEAD..origin/main", "--oneline", "--no-decorate")
            if not out_log.strip():
                await msg.edit("已是最新版本，无需更新")
                await asyncio.sleep(4)
                await msg.delete()
                return

            await msg.edit("拉取最新代码...")

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
                await msg.edit(f"拉取失败\n`{out[:1000]}`")
                await asyncio.sleep(10)
                await msg.delete()
                return

            commits = _parse_commits(out)

            # 写入待删除记录，重启后清理
            _PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
            _PENDING_FILE.write_text(json.dumps({
                "chat_id": event.chat_id,
                "msg_id": msg.id,
            }))

            await msg.edit(
                f"更新完成\n```\n{commits[:1500]}\n```\n重启中..."
            )

            await asyncio.sleep(2)
            logger.info("[up] git pull 完成，触发重启")
            os._exit(0)