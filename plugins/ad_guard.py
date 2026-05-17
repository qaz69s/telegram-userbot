"""
插件: ad_guard —— 陌生人私聊自动验证反广告

用法:
  #ad      —— 切换开关（开启 / 关闭）
  #ad on   —— 开启
  #ad off  —— 关闭

流程:
  1. 陌生人首次私聊 → 自动回复验证问题
  2. 对方回复"不是/no"之类的否定词 → 通过验证，允许继续私聊
  3. 对方回复其他内容，或 60 秒内无回复 → 自动拉黑 + 举报垃圾信息
"""
import asyncio
import logging
import re

from telethon import events, functions
from telethon.tl.types import InputPeerUser
from core.plugin_base import BasePlugin

logger = logging.getLogger(__name__)

_TIMEOUT    = 60  # 秒
_NS         = "ad_guard"

_CHALLENGE = (
    "If you are not advertising, reply 'No'.\n\n"
    "如果不是广告，请回复“不是”继续私聊。"
)

# 识别否定回答：英文 no 系列 + 中文否定词
_NO_RE = re.compile(
    r"\b(no|nope|nah|nay|not|never|negative)\b"
    r"|^(不是|不|没有|没|否|当然不|绝对不|肯定不|并不|并没|不打|没有打)$",
    re.IGNORECASE,
)


class AdGuardPlugin(BasePlugin):
    name        = "ad_guard"
    description = "#ad 开关陌生人私聊验证，超时或非否定回答自动拉黑举报"
    version     = "1.0.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._enabled:    bool     = False
        self._pending:    dict     = {}        # user_id -> {chat_id, challenge_msg_id, task}
        self._processing: set[int] = set()     # 正在发送验证中（防竞态）
        self._known:      set[int] = set()     # 已通过验证 or 已拉黑的用户

    async def on_startup(self):
        await self._load_state()
        logger.info("[ad_guard] 插件就绪，当前状态：%s", "开启" if self._enabled else "关闭")

    async def setup(self):
        prefix = re.escape(self.config.get("CMD_PREFIX", "#"))

        # ── #ad 开关指令 ──────────────────────────────
        @self.client.on(events.NewMessage(
            outgoing=True,
            pattern=rf"(?i)^{prefix}ad(?:\s+(on|off))?$"
        ))
        async def toggle_handler(event):
            try:
                await event.delete()
            except Exception:
                pass

            arg = (event.pattern_match.group(1) or "").lower()
            if arg == "on":
                self._enabled = True
            elif arg == "off":
                self._enabled = False
            else:
                self._enabled = not self._enabled

            await self._save_state()
            status = "陌生人验证：开启" if self._enabled else "陌生人验证：关闭"
            tip = await self.client.send_message(event.chat_id, status)
            await asyncio.sleep(3)
            try:
                await tip.delete()
            except Exception:
                pass

        # ── 监听私聊消息 ──────────────────────────────────────
        @self.client.on(events.NewMessage(
            incoming=True,
            func=lambda e: e.is_private
        ))
        async def incoming_handler(event):
            if not self._enabled:
                return

            user_id = event.sender_id
            if user_id is None:
                return

            # 跳过联系人和 Bot
            sender = None
            try:
                sender = await event.get_sender()
                if getattr(sender, "contact", False):
                    self._known.add(user_id)
                    return
                if getattr(sender, "bot", False):
                    self._known.add(user_id)
                    return
            except Exception:
                pass

            # 已通过验证或已拉黑的用户直接放行
            if user_id in self._known:
                return

            # 等待验证中 → 处理回复
            if user_id in self._pending:
                await self._handle_reply(event, user_id)
                return

            # 有历史聊天记录 → 老朋友，直接放行
            try:
                prior = await self.client.get_messages(event.chat_id, limit=1,
                                                        offset_id=event.message.id)
                if len(prior) > 0:
                    self._known.add(user_id)
                    return
            except Exception:
                pass

            # 正在发送验证消息中（防竞态，跳过重复触发）
            if user_id in self._processing:
                return

            # 新陌生人（无历史记录）→ 发送验证问题
            await self._send_challenge(event.chat_id, user_id, sender)

    # ── 核心逻辑 ──────────────────────────────────────────────

    async def _send_challenge(self, chat_id: int, user_id: int, sender):
        """发送验证问题并启动超时计时器。"""
        self._processing.add(user_id)
        try:
            msg = await self.client.send_message(chat_id, _CHALLENGE)
        except Exception as e:
            logger.warning("[ad_guard] 发送验证消息失败: %s", e)
            self._processing.discard(user_id)
            return

        task = asyncio.create_task(self._timeout_task(chat_id, user_id, sender))
        self._pending[user_id] = {
            "chat_id":           chat_id,
            "challenge_msg_id":  msg.id,
            "task":              task,
            "sender":            sender,
        }
        self._processing.discard(user_id)
        logger.info("[ad_guard] 已向用户 %s 发送验证问题", user_id)

    async def _handle_reply(self, event, user_id: int):
        """处理待验证用户的回复。"""
        # 防止并发重复处理（用户快速连发多条时）
        if user_id not in self._pending:
            return
        info = self._pending.pop(user_id)
        info["task"].cancel()
        sender = info["sender"]

        text = (event.raw_text or "").strip()

        if _NO_RE.search(text):
            # 通过验证
            self._known.add(user_id)
            await self._save_state()
            logger.info("[ad_guard] 用户 %s 通过验证", user_id)
            tip = await self.client.send_message(info["chat_id"], "验证通过，可以继续私聊。")
            await asyncio.sleep(3)
            try:
                await tip.delete()
            except Exception:
                pass
        else:
            # 非否定回答 → 立即拉黑举报
            await self._block_and_report(info["chat_id"], info["sender"], user_id, reason="回复非否定内容")

    async def _timeout_task(self, chat_id: int, user_id: int, sender):
        """60 秒无回应后自动拉黑举报。"""
        await asyncio.sleep(_TIMEOUT)
        if user_id in self._pending:
            del self._pending[user_id]
            await self._block_and_report(chat_id, sender, user_id, reason="超时未回复")

    async def _block_and_report(self, chat_id: int, sender, user_id: int, reason: str = ""):
        """拉黑并举报垃圾信息。"""
        self._known.add(user_id)
        await self._save_state()

        # sender 可能为 None（get_sender 失败），用 user_id 构造兑退用的 peer
        peer = sender
        if peer is None:
            try:
                peer = await self.client.get_entity(user_id)
            except Exception:
                peer = InputPeerUser(user_id=user_id, access_hash=0)

        try:
            await self.client(functions.contacts.BlockRequest(id=peer))
            logger.info("[ad_guard] 已拉黑用户 %s（%s）", user_id, reason)
        except Exception as e:
            logger.warning("[ad_guard] 拉黑失败: %s", e)

        try:
            await self.client(functions.messages.ReportSpamRequest(peer=peer))
            logger.info("[ad_guard] 已举报用户 %s", user_id)
        except Exception as e:
            logger.warning("[ad_guard] 举报失败: %s", e)

        # 静默删除整个会话，不留痕迹
        try:
            await self.client.delete_dialog(chat_id)
            logger.info("[ad_guard] 已删除与用户 %s 的会话记录", user_id)
        except Exception as e:
            logger.warning("[ad_guard] 删除会话失败: %s", e)

    # ── 状态持久化 ────────────────────────────────────────────

    async def _save_state(self):
        try:
            await self.db.kv_set(_NS, "enabled", self._enabled)
            await self.db.kv_set(_NS, "known", list(self._known))
        except Exception as e:
            logger.warning("[ad_guard] 保存状态失败: %s", e)

    async def _load_state(self):
        try:
            self._enabled = await self.db.kv_get(_NS, "enabled", False)
            known_list    = await self.db.kv_get(_NS, "known", [])
            self._known   = set(known_list)
        except Exception as e:
            logger.warning("[ad_guard] 加载状态失败: %s", e)
