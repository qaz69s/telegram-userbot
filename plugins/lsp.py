"""
插件: lsp —— 从配置的频道随机发送套图/视频（带遮罩）

用法:
  #lsp              —— 从已配置频道随机发一张图/视频（带遮罩）
  #lsp add <频道> [频道2] ...   —— 添加频道（支持批量，空格 / , / ，分隔）
  #lsp del <频道> [频道2] ...   —— 删除频道（支持批量，空格 / , / ，分隔）
  #lsp list         —— 查看已配置的频道列表
"""
import asyncio
import json
import logging
import random
import re
from pathlib import Path

from telethon import events
from telethon.tl.types import (
    InputMessagesFilterPhotoVideo,
    MessageMediaPhoto, MessageMediaDocument,
    InputMediaPhoto, InputMediaDocument,
    InputPhoto, InputDocument,
)

from core.plugin_base import BasePlugin

logger = logging.getLogger(__name__)

_CONFIG_FILE = Path("data/lsp_channels.json")
_ALBUM_CHUNK      = 10    # Telegram 单组最多 10 张，超出自动分批
_FETCH_WINDOW     = 30    # 随机偏移后拉取的消息窗口大小
_SESSION_TIME_GAP = 180   # 同一套图会话的最大时间间隔（秒，用于合并分批发送的套图）
_SESSION_ID_GAP   = 5     # 同一套图会话的最大消息 ID 间隔



def _normalize_channel(raw: str) -> str:
    """
    统一化频道标识符：
      https://t.me/example  →  example
      @example              →  example
      example               →  example
    """
    raw = raw.strip()
    # 处理 t.me 链接
    raw = re.sub(r"https?://t\.me/", "", raw)
    # 去掉开头 @
    raw = raw.lstrip("@")
    return raw


class LspPlugin(BasePlugin):
    name        = "lsp"
    description = "#lsp 从配置频道随机发送带遮罩的图片/视频"
    version     = "1.0.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._channels: list[str] = []

    async def on_startup(self):
        self._load_config()
        logger.info("[lsp] 插件就绪，已配置 %d 个频道", len(self._channels))

    async def setup(self):
        prefix = re.escape(self.config.get("CMD_PREFIX", "#"))
        cmd_re = re.compile(rf"^{prefix}lsp(?:\s+(.*))?$", re.IGNORECASE)

        @self.client.on(events.NewMessage(outgoing=True))
        async def handler(event):
            text = (event.raw_text or "").strip()
            m = cmd_re.match(text)
            if not m:
                return

            try:
                await event.delete()
            except Exception:
                pass

            raw_args = (m.group(1) or "").strip()
            parts = raw_args.split(maxsplit=1)
            action = parts[0].lower() if parts else ""
            target = parts[1].strip() if len(parts) > 1 else ""

            if action == "add":
                await self._cmd_add(event.chat_id, target)
            elif action == "del":
                await self._cmd_del(event.chat_id, target)
            elif action == "list":
                await self._cmd_list(event.chat_id)
            else:
                # 无子命令 → 随机发送
                await self._cmd_send(event.chat_id)

    # ── 子命令 ────────────────────────────────────────────────

    async def _cmd_add(self, chat_id: int, raw: str):
        if not raw:
            await self._tip(chat_id, "用法：`#lsp add @ch1 @ch2`")
            return

        # 支持空格或逗号分隔的多个频道
        raws = [s for s in re.split(r"[\s,，]+", raw) if s]
        ok_list, fail_list, dup_list = [], [], []

        for r in raws:
            ch = _normalize_channel(r)
            if ch in self._channels:
                dup_list.append(ch)
                continue
            try:
                await self.client.get_entity(ch)
                self._channels.append(ch)
                ok_list.append(ch)
            except Exception as e:
                fail_list.append(f"`{ch}`（{e}）")

        if ok_list:
            self._save_config()

        lines = []
        if ok_list:
            lines.append(f"已添加 {len(ok_list)} 个频道：" + "、".join(f"`{c}`" for c in ok_list))
        if dup_list:
            lines.append(f"已存在，跳过：" + "、".join(f"`{c}`" for c in dup_list))
        if fail_list:
            lines.append(f"添加失败：" + "、".join(fail_list))
        lines.append(f"\n当前共 {len(self._channels)} 个频道")
        await self._tip(chat_id, "\n".join(lines), delay=6)

    async def _cmd_del(self, chat_id: int, raw: str):
        if not raw:
            await self._tip(chat_id, "用法：`#lsp del @ch1 @ch2`")
            return

        raws = [s for s in re.split(r"[\s,，]+", raw) if s]
        ok_list, miss_list = [], []

        for r in raws:
            ch = _normalize_channel(r)
            if ch in self._channels:
                self._channels.remove(ch)
                ok_list.append(ch)
            else:
                miss_list.append(ch)

        if ok_list:
            self._save_config()

        lines = []
        if ok_list:
            lines.append(f"已移除 {len(ok_list)} 个频道：" + "、".join(f"`{c}`" for c in ok_list))
        if miss_list:
            lines.append(f"不在列表，跳过：" + "、".join(f"`{c}`" for c in miss_list))
        lines.append(f"\n当前共 {len(self._channels)} 个频道")
        await self._tip(chat_id, "\n".join(lines))

    async def _cmd_list(self, chat_id: int):
        if not self._channels:
            await self._tip(chat_id, "尚未配置频道\n使用 `#lsp add @频道名` 添加")
            return

        lines = ["**频道列表**", ""]
        for i, ch in enumerate(self._channels, 1):
            lines.append(f"  {i}. `{ch}`")
        lines.append(f"\n共 {len(self._channels)} 个频道")
        await self._tip(chat_id, "\n".join(lines), delay=8)

    async def _cmd_send(self, chat_id: int):
        if not self._channels:
            await self._tip(chat_id, "尚未配置频道\n请先使用 `#lsp add @频道名` 添加")
            return

        tip = await self.client.send_message(chat_id, "随机抽取中...")

        channels = self._channels.copy()
        random.shuffle(channels)

        chosen_album: list | None = None

        for ch in channels:
            try:
                # 获取该频道图片/视频消息总数
                sample = await self.client.get_messages(
                    ch, limit=0, filter=InputMessagesFilterPhotoVideo
                )
                total = getattr(sample, "total", 0)
                if not total:
                    continue

                # 随机偏移，真正从全历史中随机抽取
                offset = random.randint(0, max(0, total - _FETCH_WINDOW))

                msgs = []
                async for msg in self.client.iter_messages(
                    ch,
                    limit=_FETCH_WINDOW,
                    add_offset=offset,
                    filter=InputMessagesFilterPhotoVideo,
                ):
                    msgs.append(msg)

                # 过滤转发消息（频道广告通常是从其他频道转发来的）
                own_msgs = [m for m in msgs if m.fwd_from is None]
                pool = own_msgs if own_msgs else msgs

                albums = self._group_albums(pool)
                albums = self._merge_session_albums(albums)
                if albums:
                    chosen_album = random.choice(albums)
                    break
            except Exception as e:
                logger.warning("[lsp] 获取频道 %s 消息失败: %s", ch, e)
                continue

        if not chosen_album:
            await tip.edit("未找到可用图片或视频，请检查频道配置")
            await asyncio.sleep(4)
            try:
                await tip.delete()
            except Exception:
                pass
            return

        count = len(chosen_album)
        if count > 1:
            groups = (count + _ALBUM_CHUNK - 1) // _ALBUM_CHUNK
            label = f"套图 {count} 张，分 {groups} 组发送" if groups > 1 else f"套图 {count} 张"
        else:
            label = "单张图/视频"

        await tip.edit(f"发送中（{label}）...")

        try:
            if count > 1:
                await self._send_album(chat_id, chosen_album)
            else:
                try:
                    await self.client.send_file(
                        chat_id,
                        file=self._to_spoiler(chosen_album[0].media),
                    )
                except Exception as e:
                    logger.warning("[lsp] 单张遣罩发送失败，直接发送: %s", e)
                    await self.client.send_file(chat_id, chosen_album[0].media)
            await tip.delete()
        except Exception as e:
            logger.error("[lsp] 发送失败: %s", e)
            await tip.edit(f"发送失败：{e}")
            await asyncio.sleep(4)
            try:
                await tip.delete()
            except Exception:
                pass

    # ── 工具方法 ──────────────────────────────────────────────

    async def _send_album(self, chat_id: int, msgs: list):
        """
        分批发送套图（每批最多 10 张）。
        每批独立处理：遮罩发送失败时自动回退转发该批，不影响其他批次。
        批次间等待 1 秒，避免触发 FloodWait。
        """
        files = [self._to_spoiler(m.media) for m in msgs]
        total = len(files)
        for idx, i in enumerate(range(0, total, _ALBUM_CHUNK)):
            if idx > 0:
                await asyncio.sleep(1)  # 批次间间隔，避免 FloodWait
            chunk_files = files[i:i + _ALBUM_CHUNK]
            chunk_msgs  = msgs[i:i + _ALBUM_CHUNK]
            try:
                await self.client.send_file(chat_id, file=chunk_files)
            except Exception as e:
                logger.warning("[lsp] 第 %d 组遣罩发送失败，直接发送: %s", idx + 1, e)
                try:
                    await self.client.send_file(chat_id, [m.media for m in chunk_msgs])
                except Exception as fwd_e:
                    logger.error("[lsp] 第 %d 组发送也失败: %s", idx + 1, fwd_e)

    @staticmethod
    def _group_albums(msgs: list) -> list[list]:
        """
        将消息按 grouped_id 分组：
          有 grouped_id → 属于同一套图（Telegram 原生相册）
          无 grouped_id → 单张/视频，各自独立
        返回按第一条消息 ID 排序的 list[list[msg]]。
        """
        groups: dict[int, list] = {}
        singles: list[list] = []
        for msg in msgs:
            gid = getattr(msg, "grouped_id", None)
            if gid is not None:
                groups.setdefault(gid, []).append(msg)
            else:
                singles.append([msg])
        result: list[list] = [sorted(g, key=lambda m: m.id) for g in groups.values()]
        result.extend(singles)
        # 按首条消息 ID 排序，确保 _merge_session_albums 能正确判断相邻关系
        result.sort(key=lambda g: g[0].id)
        return result

    @staticmethod
    def _merge_session_albums(albums: list[list]) -> list[list]:
        """
        将相邻的多图相册合并为"发图会话"：
          若两个相邻多图相册的 ID 间隔 <= SESSION_ID_GAP
          且发送时间间隔 <= SESSION_TIME_GAP，则视为同一套图。
        单张（无 grouped_id）不参与合并，始终独立。
        """
        if not albums:
            return albums

        result: list[list] = []
        current = albums[0]

        for nxt in albums[1:]:
            # 两者都必须是多图相册才合并
            if len(current) > 1 and len(nxt) > 1:
                last_msg  = current[-1]
                first_msg = nxt[0]
                id_gap    = first_msg.id - last_msg.id
                t_last    = getattr(last_msg,  "date", None)
                t_first   = getattr(first_msg, "date", None)
                time_ok   = (
                    t_last and t_first and
                    abs((t_first - t_last).total_seconds()) <= _SESSION_TIME_GAP
                )
                if id_gap <= _SESSION_ID_GAP and time_ok:
                    current = current + nxt
                    continue
            result.append(current)
            current = nxt

        result.append(current)
        return result

    @staticmethod
    def _to_spoiler(media):
        """将已有媒体对象包装为带遮罩的 InputMedia。"""
        try:
            if isinstance(media, MessageMediaPhoto):
                p = media.photo
                return InputMediaPhoto(
                    id=InputPhoto(id=p.id, access_hash=p.access_hash,
                                  file_reference=p.file_reference),
                    spoiler=True,
                )
            if isinstance(media, MessageMediaDocument):
                d = media.document
                return InputMediaDocument(
                    id=InputDocument(id=d.id, access_hash=d.access_hash,
                                     file_reference=d.file_reference),
                    spoiler=True,
                )
        except Exception:
            pass
        return media

    async def _tip(self, chat_id: int, text: str, delay: int = 5):
        """发送临时提示消息，delay 秒后自动删除。"""
        msg = await self.client.send_message(chat_id, text)
        await asyncio.sleep(delay)
        try:
            await msg.delete()
        except Exception:
            pass

    # ── 配置持久化 ────────────────────────────────────────────

    def _save_config(self):
        try:
            _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            _CONFIG_FILE.write_text(
                json.dumps({"channels": self._channels}, ensure_ascii=False, indent=2)
            )
        except Exception as e:
            logger.warning("[lsp] 保存配置失败: %s", e)

    def _load_config(self):
        try:
            if _CONFIG_FILE.exists():
                data = json.loads(_CONFIG_FILE.read_text())
                self._channels = data.get("channels", [])
        except Exception as e:
            logger.warning("[lsp] 加载配置失败: %s", e)
