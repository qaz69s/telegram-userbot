"""
插件: gif —— 将 GIF/视频转换为 Telegram 动态贴纸

用法:
  回复一条含 GIF/视频的消息，然后发送 #gif

依赖: ffmpeg（缺失时自动通过 apt-get 安装）
"""
import asyncio
import logging
import re
import os
import shutil
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
_MAX_DURATION = 30                  # 30秒，超过自动截取前30秒
_MAX_RES = 512                      # 贴纸最大边长
_CRF = 15                           # 视频质量 0-51, 越低越好

_RANDOM_EMOJIS = [
    "😀", "😂", "😍", "🤩", "😎", "🥳", "🔥", "✨", "❤️", "💙",
    "🐱", "🐶", "🐼", "🦊", "🐸", "🐯", "🦁", "🐮", "🐷", "🐵",
]


class GifPlugin(BasePlugin):
    name = "gif"
    description = "#gif 将 GIF/视频回复转为动态贴纸"
    version = "1.2.1"

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

        # 确保 ffmpeg 可用（缺失时自动安装）
        loop = asyncio.get_running_loop()
        ok = await loop.run_in_executor(None, self._ensure_ffmpeg)
        if ok:
            logger.info("[gif] ffmpeg 就绪")
        else:
            logger.warning("[gif] ffmpeg 不可用，首次使用 #gif 时会再次尝试安装")

        logger.info("[gif] 插件就绪")

    @staticmethod
    def _ensure_ffmpeg() -> bool:
        """确保 ffmpeg 可用；缺失时通过 apt-get 自动安装（Debian/Ubuntu）"""
        if shutil.which("ffmpeg"):
            return True
        logger.warning("[gif] 未检测到 ffmpeg，尝试自动安装...")
        try:
            subprocess.run(
                ["apt-get", "update", "-qq"],
                capture_output=True, text=True, timeout=180,
            )
            r = subprocess.run(
                ["apt-get", "install", "-y", "-qq", "ffmpeg"],
                capture_output=True, text=True, timeout=600,
            )
            if r.returncode != 0:
                logger.error("[gif] ffmpeg 自动安装失败: %s", r.stderr.strip()[-500:])
                return False
        except Exception as e:
            logger.error("[gif] ffmpeg 自动安装异常: %s", e)
            return False
        if shutil.which("ffmpeg"):
            logger.info("[gif] ffmpeg 自动安装成功")
            return True
        logger.warning("[gif] ffmpeg 安装后仍不可用")
        return False

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
                    "限制：≤ 50MB，超过 30 秒自动截取，自动缩放至 512x512"
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

            # 确保 ffmpeg 可用（缺失时自动安装，兜底启动时的检测）
            if not shutil.which("ffmpeg"):
                tip = await self.client.send_message(
                    event.chat_id, "服务器缺少 ffmpeg，正在自动安装，请稍候..."
                )
                loop = asyncio.get_running_loop()
                ok = await loop.run_in_executor(None, self._ensure_ffmpeg)
                try:
                    await tip.delete()
                except Exception:
                    pass
                if not ok:
                    tip = await self.client.send_message(
                        event.chat_id,
                        "ffmpeg 自动安装失败，请手动在服务器执行：apt-get update && apt-get install -y ffmpeg",
                    )
                    await asyncio.sleep(8)
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

        # 下载 —— 必须捕获返回值作为实际路径！
        # download_media(file=...) 不保证按指定路径写入（Telethon 可能追加扩展名）
        await status_msg.edit("正在下载...")
        actual_path = await self.client.download_media(replied, file=input_path)
        if not actual_path:
            raise Exception("下载失败")
        input_path = str(actual_path)
        logger.info("[gif] 已下载到 %s", input_path)

        # 检查时长（视频才有时长属性）；超过上限自动截取前 _MAX_DURATION 秒
        dur = None
        for attr in replied.media.document.attributes:
            if isinstance(attr, DocumentAttributeVideo):
                dur = attr.duration
                break
        if dur and dur > _MAX_DURATION:
            await status_msg.edit(f"视频较长（{dur:.0f}秒），自动截取前 {_MAX_DURATION} 秒...")
            dur = _MAX_DURATION

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

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except FileNotFoundError:
            raise Exception("服务器未安装 ffmpeg（自动安装失败），请手动执行：apt-get install -y ffmpeg")
        except subprocess.TimeoutExpired:
            raise Exception("FFmpeg 转换超时（>120s）")
        if result.returncode != 0:
            err_msg = result.stderr.strip()
            # 移除 FFmpeg 版本标题
            lines = [l for l in err_msg.split(chr(10)) if not l.startswith("ffmpeg") and not l.startswith("  built") and not l.startswith("  config") and not l.startswith("  lib")]
            actual_err = chr(10).join(lines)[:500]
            raise Exception(f"FFmpeg 错误: {actual_err or err_msg[:200]}")
        if not Path(output_path).exists():
            raise Exception("FFmpeg 未生成输出文件")

