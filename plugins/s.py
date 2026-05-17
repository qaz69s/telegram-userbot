"""
插件: s —— 回复 #s 将图片/贴纸/文字收藏到个人贴纸包

用法:
  #s                    —— 回复图片、贴纸或文字消息，添加到贴纸包
  #s set <短名称>       —— 绑定或新建贴纸包（纯英文，如 mycollect）
  #s info               —— 查看当前绑定的贴纸包

依赖 Pillow：安装后支持图片转换和文字渲染为贴纸。
  pip install Pillow
"""
import asyncio
import importlib
import io
import logging
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

from telethon import events
from telethon.errors import StickersetInvalidError
from telethon.tl.functions.messages import GetStickerSetRequest, UploadMediaRequest
from telethon.tl.functions.stickers import AddStickerToSetRequest, CreateStickerSetRequest
from telethon.tl.types import (
    DocumentAttributeSticker,
    InputDocument,
    InputMediaUploadedDocument,
    InputStickerSetEmpty,
    InputStickerSetItem,
    InputStickerSetShortName,
    MessageMediaDocument,
    MessageMediaPhoto,
)

from core.plugin_base import BasePlugin

logger = logging.getLogger(__name__)

_NS            = "s"
_KEY_SET       = "stickerset"
_DEFAULT_EMOJI = "🖼"


class SPlugin(BasePlugin):
    name        = "s"
    description = "#s 收藏图片/贴纸到个人贴纸包"
    version     = "1.0.0"

    async def on_startup(self):
        await _ensure_pillow()
        await _ensure_cjk_font()
        logger.info("[s] 插件就绪")

    async def setup(self):
        prefix = re.escape(self.config.get("CMD_PREFIX", "#"))
        cmd_re = re.compile(rf"^{prefix}s(?:\s+(.*))?$", re.IGNORECASE)

        @self.client.on(events.NewMessage(
            outgoing=True,
            pattern=rf"(?i)^{prefix}s(?:\s+.*)?$",
        ))
        async def handler(event):
            try:
                await event.delete()
            except Exception:
                pass

            text = (event.raw_text or "").strip()
            m = cmd_re.match(text)
            args = (m.group(1) or "").strip() if m else ""
            parts = args.split(maxsplit=1)
            sub = parts[0].lower() if parts else ""

            if sub == "set":
                name = parts[1].strip() if len(parts) > 1 else ""
                await self._cmd_set(event.chat_id, name)
            elif sub == "info":
                await self._cmd_info(event.chat_id)
            else:
                await self._cmd_save(event)

    # ── 子命令 ────────────────────────────────────────────────

    async def _cmd_info(self, chat_id: int):
        name = await self.db.kv_get(_NS, _KEY_SET)
        if not name:
            await self._tip(chat_id, "尚未绑定贴纸包\n使用 `#s set <短名称>` 绑定或新建")
        else:
            await self._tip(chat_id, f"当前贴纸包：`{name}`\nt.me/addstickers/{name}", delay=8)

    async def _cmd_set(self, chat_id: int, name: str):
        if not name:
            await self._tip(chat_id, "用法：`#s set <短名称>`\n仅限英文字母/数字/下划线")
            return
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$", name):
            await self._tip(chat_id, "短名称须以字母开头，仅限英文字母/数字/下划线，最长 64 位")
            return

        # 检查是否已存在该贴纸包
        try:
            await self.client(GetStickerSetRequest(
                stickerset=InputStickerSetShortName(short_name=name),
                hash=0,
            ))
            await self.db.kv_set(_NS, _KEY_SET, name)
            await self._tip(chat_id, f"已绑定贴纸包 `{name}`\nt.me/addstickers/{name}", delay=6)
        except Exception:
            # 不存在 → 新建
            await self._create_set(chat_id, name)

    async def _create_set(self, chat_id: int, name: str):
        tip = await self.client.send_message(chat_id, f"新建贴纸包 `{name}` 中...")
        try:
            placeholder = _make_placeholder_png()
            input_doc = await self._upload_sticker_doc(io.BytesIO(placeholder))
            me = await self.client.get_me()
            await self.client(CreateStickerSetRequest(
                user_id=me,
                title=name,
                short_name=name,
                stickers=[InputStickerSetItem(
                    document=input_doc,
                    emoji=_DEFAULT_EMOJI,
                )],
            ))
            await self.db.kv_set(_NS, _KEY_SET, name)
            await tip.edit(f"贴纸包创建成功\nt.me/addstickers/{name}")
        except Exception as e:
            logger.error("[s] 创建贴纸包失败: %s", e)
            await tip.edit(f"创建失败：{e}")

        await asyncio.sleep(5)
        try:
            await tip.delete()
        except Exception:
            pass

    async def _cmd_save(self, event):
        chat_id = event.chat_id

        set_name = await self.db.kv_get(_NS, _KEY_SET)
        if not set_name:
            await self._tip(chat_id, "尚未绑定贴纸包\n请先使用 `#s set <短名称>` 绑定或新建")
            return

        if not event.reply_to_msg_id:
            await self._tip(chat_id, "请回复图片、贴纸或文字消息后发 `#s`")
            return

        replied = await event.get_reply_message()
        if not replied:
            await self._tip(chat_id, "获取被回复消息失败")
            return

        tip = await self.client.send_message(chat_id, "收藏中...")
        try:
            # 有媒体(图片/贴纸)走原有流程；纯文字走渲染流程
            if replied.media:
                doc = await self._extract_doc(replied)
            elif replied.raw_text:
                doc = await self._render_text_sticker(replied)
            else:
                await tip.edit("被回复消息无内容（既无图片也无文字）")
                await asyncio.sleep(4)
                await tip.delete()
                return

            if doc is None:
                await tip.edit("转换失败，请确认 Pillow 已安装")
                await asyncio.sleep(4)
                await tip.delete()
                return

            await self.client(AddStickerToSetRequest(
                stickerset=InputStickerSetShortName(short_name=set_name),
                sticker=InputStickerSetItem(document=doc, emoji=_DEFAULT_EMOJI),
            ))
            await tip.edit(f"已收藏到贴纸包\nt.me/addstickers/{set_name}")
            await asyncio.sleep(4)
            await tip.delete()
        except StickersetInvalidError:
            await tip.edit(f"贴纸包 `{set_name}` 不存在，请重新绑定")
            await asyncio.sleep(4)
            await tip.delete()
        except Exception as e:
            logger.error("[s] 添加贴纸失败: %s", e)
            await tip.edit(f"添加失败：{e}")
            await asyncio.sleep(4)
            try:
                await tip.delete()
            except Exception:
                pass

    # ── 工具方法 ──────────────────────────────────────────────

    async def _extract_doc(self, msg) -> InputDocument | None:
        """
        从消息中提取 InputDocument：
          - 原生贴纸 → 直接用文档引用
          - 图片/普通文档 → 转为 512x512 PNG 上传（需要 Pillow）
        """
        media = msg.media

        # 原生贴纸：有 DocumentAttributeSticker 属性
        if isinstance(media, MessageMediaDocument):
            doc = media.document
            attrs = getattr(doc, "attributes", [])
            if any(isinstance(a, DocumentAttributeSticker) for a in attrs):
                return InputDocument(
                    id=doc.id,
                    access_hash=doc.access_hash,
                    file_reference=doc.file_reference,
                )

        # 图片或其他文档 → 下载并转换为 512x512 PNG，上传为正式文档
        if isinstance(media, (MessageMediaPhoto, MessageMediaDocument)):
            raw = await self.client.download_media(msg, bytes)
            if not raw:
                return None
            png = _to_sticker_png(raw)
            if png is None:
                return None
            return await self._upload_sticker_doc(io.BytesIO(png))

        return None

    async def _render_text_sticker(self, msg) -> InputDocument | None:
        """
        将文字消息渲染为头像 + 名字 + 内容的贴纸图片。
        """
        try:
            sender = await msg.get_sender()
        except Exception:
            sender = None

        # 获取发送者显示名
        if sender:
            first = getattr(sender, "first_name", "") or ""
            last  = getattr(sender, "last_name",  "") or ""
            display_name = (first + " " + last).strip() or getattr(sender, "username", "Unknown") or "Unknown"
        else:
            display_name = "Unknown"

        # 下载头像
        avatar_bytes: bytes | None = None
        if sender:
            try:
                avatar_bytes = await self.client.download_profile_photo(sender, bytes)
            except Exception:
                pass

        png = _render_quote_png(display_name, msg.raw_text or "", avatar_bytes)
        if png is None:
            return None
        return await self._upload_sticker_doc(io.BytesIO(png))

    async def _upload_sticker_doc(self, file_like: io.BytesIO) -> InputDocument:
        """
        上传 PNG 并通过 UploadMediaRequest 注册为文档，
        返回 Telegram 认可的 InputDocument。
        CreateStickerSetRequest / AddStickerToSetRequest 均需要此类型。
        """
        uploaded = await self.client.upload_file(file_like, file_name="sticker.png")
        media = await self.client(UploadMediaRequest(
            peer="me",
            media=InputMediaUploadedDocument(
                file=uploaded,
                mime_type="image/png",
                attributes=[DocumentAttributeSticker(
                    alt=_DEFAULT_EMOJI,
                    stickerset=InputStickerSetEmpty(),
                )],
            ),
        ))
        doc = media.document
        return InputDocument(
            id=doc.id,
            access_hash=doc.access_hash,
            file_reference=doc.file_reference,
        )

    async def _tip(self, chat_id: int, text: str, delay: int = 5):
        msg = await self.client.send_message(chat_id, text)
        await asyncio.sleep(delay)
        try:
            await msg.delete()
        except Exception:
            pass


# ── 模块级工具函数 ────────────────────────────────────────────

async def _ensure_pillow():
    """若 Pillow 未安装则自动安装，仅执行一次。"""
    try:
        importlib.import_module("PIL")
        return  # 已安装
    except ImportError:
        pass
    logger.info("[s] Pillow 未检测到，正在自动安装...")
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", "Pillow", "--quiet",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode == 0:
            # 安装成功后重新加载模块，让后续调用立即可用
            importlib.invalidate_caches()
            logger.info("[s] Pillow 安装成功")
        else:
            logger.warning("[s] Pillow 安装失败: %s", stderr.decode().strip())
    except Exception as e:
        logger.warning("[s] Pillow 安装出错: %s", e)


async def _ensure_cjk_font():
    """启动时预下载 Noto Sans SC 字体到本地缓存，保证中文渲染正常。"""
    # 检查系统是否已有 CJK 字体
    _SYSTEM_CJK = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ]
    if _FONT_CACHE_PATH.exists():
        logger.info("[s] CJK 字体已就绪: %s", _FONT_CACHE_PATH)
        return
    for p in _SYSTEM_CJK:
        if Path(p).exists():
            logger.info("[s] 使用系统 CJK 字体: %s", p)
            return

    # 无系统字体，异步下载 Noto Sans SC
    _URLS = [
        "https://cdn.jsdelivr.net/gh/googlefonts/noto-cjk@main/Sans/SubsetOTF/SC/NotoSansSC-Regular.otf",
        "https://github.com/googlefonts/noto-cjk/raw/main/Sans/SubsetOTF/SC/NotoSansSC-Regular.otf",
    ]
    for url in _URLS:
        try:
            logger.info("[s] 正在下载 CJK 字体 (~9 MB): %s", url)
            _FONT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = _FONT_CACHE_PATH.with_suffix(".tmp")
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, lambda u=url, t=tmp: urllib.request.urlretrieve(u, t)
            )
            tmp.rename(_FONT_CACHE_PATH)
            logger.info("[s] 字体下载完成: %s", _FONT_CACHE_PATH)
            return
        except Exception as e:
            logger.warning("[s] 字体下载失败 (%s): %s", url, e)
            tmp = _FONT_CACHE_PATH.with_suffix(".tmp")
            if tmp.exists():
                tmp.unlink(missing_ok=True)
    logger.warning("[s] 所有字体源均下载失败，中文将显示为方块。可手动放置字体: %s", _FONT_CACHE_PATH)


def _to_sticker_png(data: bytes) -> bytes | None:
    """将任意图片转换为 512x512 PNG（等比缩放，透明背景）。需要 Pillow。"""
    try:
        from PIL import Image
    except ImportError:
        logger.warning("[s] Pillow 未安装，无法转换普通图片为贴纸。可运行: pip install Pillow")
        return None
    try:
        img = Image.open(io.BytesIO(data)).convert("RGBA")
        img.thumbnail((512, 512), Image.LANCZOS)
        canvas = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
        offset = ((512 - img.width) // 2, (512 - img.height) // 2)
        canvas.paste(img, offset, img)
        buf = io.BytesIO()
        canvas.save(buf, "PNG")
        return buf.getvalue()
    except Exception as e:
        logger.warning("[s] 图片转换失败: %s", e)
        return None


def _make_placeholder_png() -> bytes:
    """生成一张 512x512 透明 PNG 作为新建贴纸包时的占位贴纸。"""
    try:
        from PIL import Image
        img = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()
    except ImportError:
        import base64
        return base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQI12NgAAIABQ"
            "AABjkB6QAAAABJRU5ErkJggg=="
        )


# 本地字体缓存路径
_FONT_CACHE_DIR = Path.home() / ".cache" / "tgbot-fonts"
_FONT_CACHE_PATH = _FONT_CACHE_DIR / "NotoSansSC-Regular.otf"


def _find_font(size: int):
    """查找支持 CJK/Unicode 的字体。启动时已预下载，此处直接使用缓存或系统字体。"""
    from PIL import ImageFont

    # ── 1. 优先使用本地缓存（启动时预下载）────────────────────────────
    if _FONT_CACHE_PATH.exists():
        try:
            return ImageFont.truetype(str(_FONT_CACHE_PATH), size)
        except Exception:
            pass

    # ── 2. 系统内置 CJK 字体路径 ────────────────────────────────────
    candidates = [
        # Windows
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
        # Linux — Noto CJK
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto/NotoSansCJK-Regular.ttc",
        # Linux — WQY 文泉驿
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc",
        "/usr/share/fonts/wenquanyi/wqy-microhei.ttc",
        # Linux — Droid / Arphic
        "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
        "/usr/share/fonts/truetype/arphic/uming.ttc",
        # macOS
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/Library/Fonts/Arial Unicode MS.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue

    # ── 3. 最终降级（字体未就绪，中文会显示方块）────────────────────
    logger.warning("[s] 未找到 CJK 字体，中文将显示为方块")
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()

    # ── 5. 最终降级：PIL 内置位图字体（不支持 CJK，仅保证不崩溃）──────────
    logger.warning("[s] 所有字体加载失败，中文将显示为方块")
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _render_quote_png(
    name: str,
    text: str,
    avatar_bytes: bytes | None,
) -> bytes | None:
    """
    渲染仿 Telegram 引用气泡：
      ┌──────────────────────────────────┐
      │  [头像]  发送者名字              │
      │          消息正文（自动换行）    │
      └──────────────────────────────────┘
    输出 512x512 透明背景 PNG。
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        logger.warning("[s] Pillow 未安装，无法渲染文字贴纸")
        return None

    try:
        W, H       = 512, 512
        OUTER      = 14          # 气泡到画布边缘的留白
        PAD        = 24          # 气泡内边距
        AVATAR_R   = 36          # 头像半径
        AVATAR_D   = AVATAR_R * 2
        GAP        = 14          # 头像与文字的间距
        BUBBLE_CLR = (30, 39, 57, 230)   # 深蓝气泡背景（带透明）
        NAME_CLR   = (100, 181, 246, 255) # 蓝色名字
        TEXT_CLR   = (236, 239, 241, 255) # 浅色正文
        RADIUS     = 24          # 气泡圆角

        font_name = _find_font(34)
        font_text = _find_font(32)

        # ── 布局约束 ──────────────────────────────
        bubble_w   = W - OUTER * 2
        max_bubble_h = H - OUTER * 2      # 气泡最大高度，保证不超出画布

        text_x_off = PAD + AVATAR_D + GAP  # 文字左偏移（相对气泡）
        text_w     = bubble_w - text_x_off - PAD

        line_h  = 42
        name_h  = 46

        # ── 先算可容纳的最大行数 ──────────────────
        avail_for_lines = max_bubble_h - PAD * 2 - name_h
        max_lines = max(1, avail_for_lines // line_h)

        # ── 自动换行 ──────────────────────────────
        def wrap_text(txt: str, font, max_w: int) -> list[str]:
            lines, cur = [], ""
            for ch in txt:
                test = cur + ch
                bbox = font.getbbox(test) if hasattr(font, "getbbox") else (0, 0, len(test) * 12, 20)
                if bbox[2] > max_w and cur:
                    lines.append(cur)
                    cur = ch
                else:
                    cur = test
                if ch == "\n":
                    lines.append(cur.rstrip("\n"))
                    cur = ""
            if cur:
                lines.append(cur)
            return lines or [""]

        wrapped = wrap_text(text, font_text, text_w)

        # 超出最大行数时截断并加省略号
        if len(wrapped) > max_lines:
            wrapped = wrapped[:max_lines - 1]
            wrapped.append("…")

        # ── 根据实际内容计算气泡高度 ──────────────
        content_h = name_h + len(wrapped) * line_h
        bubble_h  = max(AVATAR_D + PAD * 2, content_h + PAD * 2)
        bubble_h  = min(bubble_h, max_bubble_h)   # 保证不超出

        # ── 绘制气泡 ──────────────────────────────
        canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw   = ImageDraw.Draw(canvas)

        bx = OUTER
        by = (H - bubble_h) // 2
        # 圆角矩形背景
        draw.rounded_rectangle(
            [bx, by, bx + bubble_w, by + bubble_h],
            radius=RADIUS,
            fill=BUBBLE_CLR,
        )

        # ── 头像 ──────────────────────────────────
        ax = bx + PAD
        ay = by + (bubble_h - AVATAR_D) // 2

        if avatar_bytes:
            try:
                av_img = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
                av_img = av_img.resize((AVATAR_D, AVATAR_D), Image.LANCZOS)
                # 圆形遮罩
                mask = Image.new("L", (AVATAR_D, AVATAR_D), 0)
                ImageDraw.Draw(mask).ellipse((0, 0, AVATAR_D, AVATAR_D), fill=255)
                av_img.putalpha(mask)
                canvas.paste(av_img, (ax, ay), av_img)
            except Exception:
                avatar_bytes = None

        if not avatar_bytes:
            # 无头像：用名字首字母填充
            draw.ellipse([ax, ay, ax + AVATAR_D, ay + AVATAR_D], fill=(80, 120, 180, 255))
            initial = (name[:1] or "?").upper()
            fi = _find_font(28)
            bbox = fi.getbbox(initial) if hasattr(fi, "getbbox") else (0, 0, 20, 28)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            draw.text(
                (ax + (AVATAR_D - tw) // 2, ay + (AVATAR_D - th) // 2),
                initial, font=fi, fill=(255, 255, 255, 255),
            )

        # ── 名字 + 正文 ───────────────────────────
        tx = bx + text_x_off
        ty = by + PAD

        draw.text((tx, ty), name, font=font_name, fill=NAME_CLR)
        ty += name_h

        for line in wrapped:
            draw.text((tx, ty), line, font=font_text, fill=TEXT_CLR)
            ty += line_h

        buf = io.BytesIO()
        canvas.save(buf, "PNG")
        return buf.getvalue()

    except Exception as e:
        logger.warning("[s] 文字贴纸渲染失败: %s", e)
        return None
