"""
插件: del —— 每 12 小时静默删除指定群/频道中自己最早的 50 条发言

用法:
  #del on    —— 为当前群/频道启用定时删除
  #del off   —— 关闭当前群/频道的定时删除
  #del now   —— 立即对所有已启用会话执行一次删除
  #del       —— 查看已启用的会话列表（发到「已保存消息」）

说明:
  - 默认不删除任何会话，需手动 #del on 开启
  - 删除顺序：从最早到最新，老消息先清
  - 每条消息之间加随机短延迟，避免批量操作特征
  - 所有状态反馈均发送到「已保存消息」，不在目标频道留痕
"""
import asyncio
import html
import logging
import random
import re

from telethon import events

from core.plugin_base import BasePlugin

logger = logging.getLogger(__name__)

_INTERVAL    = 12 * 3600  # 自动删除间隔：12 小时
_BATCH_SIZE  = 50         # 每次删除条数
_DELAY_MIN   = 0.4        # 每条消息删除间隔下限（秒）
_DELAY_MAX   = 1.2        # 每条消息删除间隔上限（秒）
_NOTIFY_TTL  = 10         # 通知消息自动删除秒数


class DelPlugin(BasePlugin):
    name        = "del"
    description = "#del 每 12 小时静默删除频道/群组中自己的 50 条历史发言"
    version     = "1.0.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._targets: set[int] = set()
        self._me_id: int = 0
        self._loop_task: asyncio.Task | None = None

    async def on_startup(self):
        saved = await self.db.kv_get("del", "targets") or []
        self._targets = set(saved)

        me = await self.client.get_me()
        self._me_id = me.id

        self._loop_task = asyncio.create_task(self._auto_loop())
        logger.info("[del] 插件就绪，已启用 %d 个会话", len(self._targets))

    async def setup(self):
        prefix = re.escape(self.config.get("CMD_PREFIX", "#"))

        @self.client.on(events.NewMessage(
            outgoing=True,
            pattern=rf"(?i)^{prefix}del(?:\s+(on|off|now))?$",
        ))
        async def cmd_handler(event):
            await event.delete()
            sub     = (event.pattern_match.group(1) or "").lower()
            chat_id = event.chat_id

            if sub == "on":
                self._targets.add(chat_id)
                await self.db.kv_set("del", "targets", list(self._targets))
                await self._notify(f"已为该会话启用定时删除（每 {_INTERVAL // 3600} 小时删最早 {_BATCH_SIZE} 条）")

            elif sub == "off":
                self._targets.discard(chat_id)
                await self.db.kv_set("del", "targets", list(self._targets))
                await self.db.kv_set("del", f"cursor_{chat_id}", None)
                await self._notify("已关闭该会话的定时删除")

            elif sub == "now":
                if not self._me_id:
                    await self._notify("插件尚未就绪，请稍后再试")
                    return
                if not self._targets:
                    await self._notify("当前没有已启用的会话。请先进入目标群/频道发送 #del on")
                    return
                await self._notify(f"开始对 {len(self._targets)} 个会话执行删除...")
                total = await self._run_all()
                await self._notify(f"完成，共删除 {total} 条", ttl=60)

            else:
                if not self._targets:
                    await self._notify("当前未启用任何会话。进入目标群/频道发送 #del on 开启。")
                else:
                    lines = ["<b>#del 已启用列表</b>\n"]
                    for cid in self._targets:
                        try:
                            entity = await self.client.get_entity(cid)
                            title  = getattr(entity, "title", None) or getattr(entity, "username", str(cid))
                        except Exception:
                            title = str(cid)
                        lines.append(f"- {html.escape(title)}  <code>{cid}</code>")
                    await self._notify("\n".join(lines))

    # ── 定时循环 ─────────────────────────────────────────────────────

    async def _auto_loop(self):
        while True:
            await asyncio.sleep(_INTERVAL)
            if not self._me_id:
                continue
            total = await self._run_all()
            logger.info("[del] 自动删除完成，共 %d 条", total)

    async def _run_all(self) -> int:
        """遍历已启用会话，执行删除，返回总删除数。"""
        total = 0
        for chat_id in list(self._targets):
            try:
                count = await self._delete_in_chat(chat_id)
                try:
                    entity = await self.client.get_entity(chat_id)
                    title = getattr(entity, "title", None) or getattr(entity, "username", str(chat_id))
                except Exception:
                    title = str(chat_id)
                logger.info("[del] chat=%s 删除 %d 条", chat_id, count)
                await self._notify(f"{html.escape(title)}：删除 {count} 条", ttl=120)
                total += count
            except Exception as e:
                logger.warning("[del] 删除失败 chat=%s: %s", chat_id, e)
        return total

    # ── 核心删除逻辑 ─────────────────────────────────────────────────

    async def _delete_in_chat(self, chat_id: int) -> int:
        """
        从历史最早处开始，每次批量删除最多 _BATCH_SIZE 条自己的消息。

        使用 min_id 游标记录进度：群组/频道统一用客户端过滤 sender_id，
        扫描直到删够 _BATCH_SIZE 条或历史耗尽，Telethon 内部自动分页。
        """
        cursor_key = f"cursor_{chat_id}"
        min_id: int = await self.db.kv_get("del", cursor_key) or 0

        deleted  = 0
        last_id  = min_id   # 本批扫描到的最新 message_id，用于推进游标

        try:
            async for msg in self.client.iter_messages(
                chat_id,
                reverse=True,   # 从最旧 → 最新，老消息先删
                min_id=min_id,  # 从上次游标位置继续
            ):
                last_id = msg.id        # 无论是否删除，都记录扫描进度

                # 只删自己的消息：
                #   sender_id == me_id  → 普通群组/超级群组中自己发的
                #   sender_id is None   → 广播频道中以「频道」身份发出的帖子（管理员匿名发帖）
                if msg.sender_id is not None and msg.sender_id != self._me_id:
                    continue

                try:
                    await msg.delete()
                    deleted += 1
                    await asyncio.sleep(random.uniform(_DELAY_MIN, _DELAY_MAX))
                except Exception as e:
                    logger.warning("[del] 删除消息 %s 失败: %s", msg.id, e)

                if deleted >= _BATCH_SIZE:
                    break

        except Exception as e:
            logger.warning("[del] 扫描失败 chat=%s: %s", chat_id, e)

        # 推进游标，下次从本批结束位置继续
        if last_id > min_id:
            await self.db.kv_set("del", cursor_key, last_id)

        return deleted

    # ── 发到「已保存消息」，不在目标频道留痕 ─────────────────────────

    async def _notify(self, text: str, ttl: int = _NOTIFY_TTL):
        try:
            msg = await self.client.send_message("me", text, parse_mode="html")
            if ttl > 0:
                async def _delete_later():
                    await asyncio.sleep(ttl)
                    try:
                        await msg.delete()
                    except Exception:
                        pass
                asyncio.create_task(_delete_later())
        except Exception as e:
            logger.warning("[del] 通知发送失败: %s", e)
