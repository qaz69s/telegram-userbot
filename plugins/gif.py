"""
插件: gif —— 将 GIF/视频转换为 Telegram 动态贴纸

用法:
  回复一条含 GIF/视频的消息，然后发送 #gif

依赖: ffmpeg（需系统安装）
"""
import asyncio
import logging
import re
import os
import subprocess
import tempfile
import random
from pathlib import Path

from telethon import events
from telethon.tl.types import MessageMediaDocument, DocumentAttributeVideo, DocumentAttributeFilename
from telethon.tl.functions.messages import SendMediaRequest
from telethon.tl.types import InputMediaUploadedDocument

from core.plugin_base import BasePlugin

logger = logging.getLogger(__name__)

_MAX_FILE_SIZE = 50 * 1024 * 1024   # 50MB
_MAX_DURATION = 10                  # 10秒
_MAX_RES = 512                      # 贴纸最大边长
_CRF = 15                           # 视频质量 0-51, 越低越好

_RANDOM_EMOJIS = [
    "😀", "😂", "😍", "🤩", "😎", "🥳", "🔥", "✨", "❤️", "💙",
    "🐱", "🐶", "🐼", "🦊", "🐸", "🐯", "🦁", "🐮", "🐷", "🐵",
]


class GifPlugin(BasePlugin):
    name = "gif"
    description = "#gif 将 GIF/视频回复转为动态贴纸"
    version = "1.0.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._tmp_dir = Path(tempfile.gettempdir()) / "gif_stickers"

    async def on_startup(self):
        self._tmp_dir.mkdir(parents=True, exist_ok=True)
        # 清理旧临时文件
        for f in self._tmp_dir.glob("*"):
            try:
                f.unlink()
            except Exception:
                pass
        logger.info("[gif] 插件就绪")

    async def setup(self):
        prefix = re.escape(self.config.get("CMD_PREFIX", "#"))

        @self.client.on(events.NewMessage(
            outgoing=True,
            pattern=rf"(?i)^{prefix}gif(\s+.*)?$",
        ))
        async def cmd_handler(event):
            raw_args = (event.pattern_match.group(1) or "").strip().lower()

            await event.delete()

            # ── help ──
            if raw_args in ("help", "h"):
                tip = await self.client.send_message(
                    event.chat_id,
                    "用法：回复一条 GIF/视频消息，然后发送 #gif\n"
                    "限制：≤ 50MB，≤ 10 秒，自动缩放至 512x512"
                )
                await asyncio.sleep(8)
                try:
                    await tip.delete()
                except Exception:
                    pass
                return

            # ── 必须是回复消息 ──
            if not event.is_reply:
                tip = await self.client.send_message(
                    event.chat_id, "请回复一条 GIF 或视频消息后再使用 #gif"
                )
                await asyncio.sleep(5)
                try:
                    await tip.delete()
                except Exception:
                    pass
                return

            replied = await event.get_reply_message()

            # 检查是否有媒体
            media = replied.media
            if not media or not isinstance(media, MessageMediaDocument):
                tip = await self.client.send_message(
                    event.chat_id, "回复的消息不是 GIF/视频"
                )
                await asyncio.sleep(4)
                try:
                    await tip.delete()
                except Exception:
                    pass
                return

            doc = media.document
            mime = (doc.mime_type or "").lower()
            is_video = mime.startswith("video/") or mime == "image/gif"

            if not is_video:
                tip = await self.client.send_message(
                    event.chat_id, "不支持的文件格式，请回复 GIF 或视频"
                )
                await asyncio.sleep(4)
                try:
                    await tip.delete()
                except Exception:
                    pass
                return

            # 检查大小
            if doc.size > _MAX_FILE_SIZE:
                mb = doc.size / 1024 / 1024
                tip = await self.client.send_message(
                    event.chat_id, f"文件过大（{mb:.0f}MB），最大支持 50MB"
                )
                await asyncio.sleep(5)
                try:
                    await tip.delete()
                except Exception:
                    pass
                return

            # 下载并转换
            msg = await self.client.send_message(event.chat_id, "正在转换...")

            try:
                await self._convert_and_send(replied, event.chat_id, msg)
            except Exception as e:
                logger.error("[gif] 转换失败: %s", e)
                try:
                    await msg.edit(f"转换失败：{e}")
                    await asyncio.sleep(6)
                    await msg.delete()
                except Exception:
                    pass

    async def _convert_and_send(self, replied, chat_id: int, status_msg):
        """下载 → 转换 → 发送"""
        ts = str(random.randint(100000, 999999))
        input_path = str(self._tmp_dir / f"input_{ts}")
        output_path = str(self._tmp_dir / f"sticker_{ts}.webm")

        # 下载
        await status_msg.edit("正在下载...")
        await self.client.download_media(replied, file=input_path)

        # 检查时长（视频才有时长属性）
        dur = None
        for attr in replied.media.document.attributes:
            if isinstance(attr, DocumentAttributeVideo):
                dur = attr.duration
                break
        if dur and dur > _MAX_DURATION:
            raise Exception(f"视频过长（{dur}秒），最大支持 10 秒")

        # FFmpeg 转换
        await status_msg.edit("正在转换...")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._ffmpeg_convert, input_path, output_path, dur)

        # 文件大小检查
        out_size = Path(output_path).stat().st_size
        if out_size > _MAX_FILE_SIZE:
            # 降低质量重试
            await status_msg.edit("文件过大，降低质量重试...")
            await loop.run_in_executor(None, self._ffmpeg_convert, input_path, output_path, dur, 28)
            out_size = Path(output_path).stat().st_size
            if out_size > _MAX_FILE_SIZE:
                raise Exception(f"压缩后仍超过 50MB，请使用更短的视频")

        # 发送为贴纸
        await status_msg.edit("正在发送贴纸...")
        emoji = random.choice(_RANDOM_EMOJIS)

        try:
            # 用 send_file 发送 WebM 作为贴纸
            await self.client.send_file(
                chat_id,
                file=output_path,
                video_note=False,
                supports_streaming=False,
                attributes=[
                    DocumentAttributeVideo(
                        duration=min(dur or 3, _MAX_DURATION),
                        w=_MAX_RES,
                        h=_MAX_RES,
                        supports_streaming=False,
                    )
                ],
                mime_type="video/webm",
                force_document=False,
            )
        except Exception as e:
            logger.warning("[gif] 发送贴纸失败，尝试以文档发送: %s", e)
            await self.client.send_file(
                chat_id,
                file=output_path,
                force_document=True,
            )

        await status_msg.delete()

        # 清理
        for p in (input_path, output_path):
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass

    @staticmethod
    def _ffmpeg_convert(input_path: str, output_path: str, duration: int = None, crf: int = _CRF):
        """用 FFmpeg 转成 WebM 贴纸"""
        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-c:v", "libvpx-vp9",
            "-b:v", "0",          # 使用 CRF 编码
            f"-crf", str(crf),
            "-vf", f"scale='min({_MAX_RES},iw)':'min({_MAX_RES},ih)':force_original_aspect_ratio=decrease",
            "-an",                 # 去音频
        ]
        if duration:
            cmd += ["-t", str(duration)]
        cmd += ["-f", "webm", output_path]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            err_msg = result.stderr.strip()
            # 移除 FFmpeg 版本标题
            lines = [l for l in err_msg.split(chr(10)) if not l.startswith("ffmpeg") and not l.startswith("  built") and not l.startswith("  config") and not l.startswith("  lib")]
            actual_err = chr(10).join(lines)[:500]
            raise Exception(f"FFmpeg 错误: {actual_err or err_msg[:200]}")
        if not Path(output_path).exists():
            raise Exception("FFmpeg 未生成输出文件")

