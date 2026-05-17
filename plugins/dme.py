"""
插件: dme —— 删除自己在当前对话的消息（Telethon 版）

用法:
  #dme      —— 删除当前对话你发送的全部消息（包括历史消息）
  #dme 50   —— 从最近一条开始，删除最近 50 条
"""
import asyncio
import logging
import re

from telethon import events, errors
from telethon.tl.types import User
from core.plugin_base import BasePlugin

logger = logging.getLogger(__name__)


class DmePlugin(BasePlugin):
    name        = "dme"
    description = "#dme 删除全部消息（加 N 只删最近 N 条）"
    version     = "1.4.0"

    async def on_startup(self):
        logger.info("[dme] 插件就绪（直接从 Telegram 拉取消息，无需数据库）")

    async def setup(self):
        prefix = re.escape(self.config.get("CMD_PREFIX", "#"))
        cmd_re = re.compile(rf"^{prefix}dme(?:\s+(\d+))?$", re.IGNORECASE)

        @self.client.on(events.NewMessage(outgoing=True))
        async def handler(event):
            try:
                text = (event.raw_text or "").strip()
                m = cmd_re.match(text)
                if not m:
                    return
                limit = int(m.group(1)) if m.group(1) else None
                # 先删掉 #dme 指令本身
                try:
                    await event.delete()
                except Exception as e:
                    logger.warning("[dme] 删除指令消息失败: %s", e)
                # 发送进度状态消息
                status_msg = await self.client.send_message(event.chat_id, "扫描消息中...")
                await self._delete(event.chat_id, limit=limit, status_msg=status_msg)
            except Exception as e:
                logger.exception("[dme] 处理异常: %s", e)

    # ── 核心删除逻辑 ─────────────────────────────────────────

    async def _is_private(self, chat_id: int) -> bool:
        """判断是否是私聊（非群组/频道）。"""
        try:
            entity = await self.client.get_entity(chat_id)
            return isinstance(entity, User)
        except Exception:
            return False

    async def _delete(self, chat_id: int, *, limit: int | None, status_msg=None):
        """
        私聊：用 msg.out 客户端过滤（API 不支持 from_user 过滤）。
        群组：用 from_user='me' 让服务端过滤，效率更高。
        """
        ids: list[int] = []
        is_private = await self._is_private(chat_id)
        exclude_id = status_msg.id if status_msg else None

        if is_private:
            # 私聊：只能客户端过滤 msg.out
            async for msg in self.client.iter_messages(chat_id):
                if msg.out and msg.id != exclude_id:
                    ids.append(msg.id)
                    if limit and len(ids) >= limit:
                        break
        else:
            # 群组 / 超级群组：服务端直接按发送者过滤
            async for msg in self.client.iter_messages(chat_id, from_user="me", limit=limit):
                if msg.id != exclude_id:
                    ids.append(msg.id)

        if not ids:
            if status_msg:
                try:
                    await status_msg.edit("未找到可删除消息")
                    await asyncio.sleep(3)
                    await status_msg.delete()
                except Exception:
                    pass
            else:
                await self._tip(chat_id, "未找到可删除消息")
            return

        scope_text = f"最近 {limit} 条中找到" if limit else "共找到"
        if status_msg:
            try:
                await status_msg.edit(f"{scope_text} {len(ids)} 条，开始清理...")
            except Exception:
                pass

        deleted, failed = await self._bulk_delete(chat_id, ids, status_msg=status_msg, total=len(ids))
        summary = self._summary(deleted, failed, limit)
        if status_msg:
            try:
                await status_msg.edit(f"清理完成：{summary}")
                await asyncio.sleep(4)
                await status_msg.delete()
            except Exception:
                pass
        else:
            await self._tip(chat_id, f"清理完成：{summary}")

    # ── 工具方法 ─────────────────────────────────────────────

    async def _bulk_delete(self, chat_id: int, ids: list[int],
                           status_msg=None, total: int = 0) -> tuple[int, int]:
        """批量删除（每批最多 100 条），返回 (deleted, failed)。"""
        deleted = failed = 0
        for i in range(0, len(ids), 100):
            batch = ids[i:i + 100]
            try:
                await self.client.delete_messages(chat_id, batch)
                deleted += len(batch)
            except errors.MessageDeleteForbiddenError:
                failed += len(batch)
            except Exception as e:
                logger.warning("[dme] 批量删除失败: %s", e)
                failed += len(batch)
            # 消息超过一批时实时更新进度
            if status_msg and total > 100:
                try:
                    await status_msg.edit(f"正在清理... {deleted + failed}/{total}")
                except Exception:
                    pass
        return deleted, failed

    async def _tip(self, chat_id: int, text: str, delay: int = 4):
        """发送临时提示消息，delay 秒后自动删除。"""
        msg = await self.client.send_message(chat_id, text)
        await asyncio.sleep(delay)
        await msg.delete()

    @staticmethod
    def _summary(deleted: int, failed: int, limit: int | None) -> str:
        scope = f"最近 {limit} 条中" if limit else ""
        s = f"{scope}共删除 {deleted} 条消息"
        if failed:
            s += f"（{failed} 条无权限，已跳过）"
        return s
