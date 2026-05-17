"""
插件: ban —— 回复某条消息发 #b，删除该用户全部消息 + 踢出群 + 举报广告

用法:
  在群组内回复目标用户的消息，再发 #b

流程:
  1. 删除目标用户在该群的全部消息（需管理员权限）
  2. 踢出群组（ban + unban，不永久封禁群，只是踢出）
  3. 举报广告/垃圾信息
"""
import asyncio
import logging
import re

from telethon import events, errors
from telethon.tl.functions.channels import EditBannedRequest
from telethon.tl.functions.messages import ReportRequest, DeleteChatUserRequest
from telethon.tl.types import ChatBannedRights, InputReportReasonSpam

from core.plugin_base import BasePlugin

logger = logging.getLogger(__name__)


class BanPlugin(BasePlugin):
    name        = "ban"
    description = "#b 删除全部消息 + 踢出群 + 举报广告（需管理员权限）"
    version     = "1.0.0"

    async def on_startup(self):
        logger.info("[ban] 插件就绪")

    async def setup(self):
        prefix = re.escape(self.config.get("CMD_PREFIX", "#"))

        @self.client.on(events.NewMessage(
            outgoing=True,
            pattern=rf"(?i)^{prefix}b$"
        ))
        async def handler(event):
            if not event.is_group:
                return
            if not event.reply_to_msg_id:
                return

            try:
                await event.delete()
            except Exception:
                pass

            try:
                replied = await event.get_reply_message()
            except Exception as e:
                logger.warning("[ban] 获取被回复消息失败: %s", e)
                return

            target = replied.sender_id
            if not target:
                return

            chat_id = event.chat_id
            tip = await self.client.send_message(chat_id, "处理中...")

            # ── 1. 删除目标用户的全部消息 ──────────────────────
            deleted, skipped = await self._delete_user_messages(chat_id, target, tip)

            # ── 2. 踢出群组 ────────────────────────────────────
            kicked = await self._kick(chat_id, target)

            # ── 3. 举报广告 ────────────────────────────────────
            reported = await self._report(chat_id, replied)

            # ── 4. 汇报结果 ────────────────────────────────────
            lines = ["**处理结果**", ""]
            del_text = f"{deleted} 条" if deleted >= 0 else "失败"
            if isinstance(skipped, int) and skipped > 0:
                del_text += f"（{skipped} 条无权限跳过）"
            lines.append(f"删除消息：{del_text}")
            lines.append(f"踢出群组：{'成功' if kicked else '失败（可能权限不足）'}")
            lines.append(f"举报广告：{'成功' if reported else '失败'}")

            await tip.edit("\n".join(lines))
            await asyncio.sleep(5)
            try:
                await tip.delete()
            except Exception:
                pass

    # ── 核心方法 ──────────────────────────────────────────────

    async def _delete_user_messages(self, chat_id: int, user_id: int, tip) -> tuple[int, int]:
        """删除目标用户在群内的全部消息，返回删除数量，失败返回 -1。"""
        ids = []
        try:
            async for msg in self.client.iter_messages(
                chat_id, from_user=user_id
            ):
                ids.append(msg.id)
        except Exception as e:
            logger.warning("[ban] 拉取用户消息失败: %s", e)
            return -1, 0

        if not ids:
            return 0, 0

        deleted = 0
        skipped = 0
        total = len(ids)
        for i in range(0, total, 100):
            batch = ids[i:i + 100]
            try:
                await self.client.delete_messages(chat_id, batch)
                deleted += len(batch)
            except errors.MessageDeleteForbiddenError:
                skipped += len(batch)
            except Exception as e:
                logger.warning("[ban] 批量删除失败: %s", e)
                skipped += len(batch)
            # 超过一批时更新进度
            if total > 100:
                try:
                    await tip.edit(f"删除消息中... {deleted + skipped}/{total}")
                except Exception:
                    pass

        return deleted, skipped

    async def _kick(self, chat_id: int, user_id: int) -> bool:
        """踢出用户，兑容超级群组和普通群。"""
        try:
            entity = await self.client.get_entity(chat_id)
            from telethon.tl.types import Channel
            if isinstance(entity, Channel):
                # 超级群组 / 频道：用 EditBannedRequest
                await self.client(EditBannedRequest(
                    channel=chat_id,
                    participant=user_id,
                    banned_rights=ChatBannedRights(
                        until_date=None,
                        view_messages=True,
                    ),
                ))
                await asyncio.sleep(0.5)
                # 解封（踢出效果）
                await self.client(EditBannedRequest(
                    channel=chat_id,
                    participant=user_id,
                    banned_rights=ChatBannedRights(until_date=None),
                ))
            else:
                # 普通群组：用 DeleteChatUserRequest
                await self.client(DeleteChatUserRequest(
                    chat_id=chat_id,
                    user_id=user_id,
                ))
            return True
        except Exception as e:
            logger.warning("[ban] 踢出用户失败: %s", e)
            return False

    async def _report(self, chat_id: int, msg) -> bool:
        """举报该消息为广告/垃圾信息。"""
        try:
            await self.client(ReportRequest(
                peer=chat_id,
                id=[msg.id],
                reason=InputReportReasonSpam(),
                message="",
            ))
            return True
        except Exception as e:
            logger.warning("[ban] 举报失败: %s", e)
            return False
