"""
插件: re —— 发送 #re 快速称赞并引用对方消息

用法:
  回复某条消息，再发 #re  —— 发送称赞语并引用对方内容
"""
import logging
import re

from telethon import events
from telethon.tl.types import MessageEntityBold, MessageEntityItalic, MessageEntitySpoiler

from core.plugin_base import BasePlugin

logger = logging.getLogger(__name__)




class RePlugin(BasePlugin):
    name        = "re"
    description = "#re 自动称赞并引用对方内容"
    version     = "1.0.0"

    async def on_startup(self):
        logger.info("[re] 插件就绪")

    async def setup(self):
        prefix = re.escape(self.config.get("CMD_PREFIX", "#"))

        @self.client.on(events.NewMessage(
            outgoing=True,
            pattern=rf"(?i)^{prefix}re$"
        ))
        async def handler(event):
            # 必须是回复某条消息时才生效
            if not event.reply_to_msg_id:
                return

            try:
                await event.delete()
            except Exception as e:
                logger.warning("[re] 删除指令消息失败: %s", e)

            try:
                replied = await event.get_reply_message()
            except Exception as e:
                logger.warning("[re] 获取被引用消息失败: %s", e)
                return

            # 提取被引用消息的文字内容
            quote_text = (replied.raw_text or "").strip()

            praise = "说得好，我给你点个赞。"
            praise_len = len(praise)
            entities = [
                MessageEntitySpoiler(offset=0, length=praise_len),
                MessageEntityBold(offset=0, length=praise_len),
                MessageEntityItalic(offset=0, length=praise_len),
            ]

            if quote_text:
                caption = f'{praise}\n\n"{quote_text}"'
            else:
                caption = praise

            # 有媒体（图片/视频/文件等）则带图发送，否则纯文字
            if replied.media:
                await event.client.send_message(
                    event.chat_id,
                    caption,
                    file=replied.media,
                    formatting_entities=entities,
                )
            else:
                await event.client.send_message(
                    event.chat_id,
                    caption,
                    formatting_entities=entities,
                )
