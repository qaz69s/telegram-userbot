"""
插件: ig —— 从配置的 Instagram 账号随机发送视频

用法:
  #ig                    —— 从已配置账号随机发一条视频
  #ig login <用户名> <密码>  —— 登录 Instagram（首次使用时配置）
  #ig logout             —— 退出登录并清除凭据
  #ig add <账号> ...      —— 添加 Instagram 账号（支持批量）
  #ig del <账号> ...      —— 删除账号（支持批量）
  #ig list               —— 查看已配置的账号列表
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import tempfile
import time
from pathlib import Path

from telethon import events

from core.plugin_base import BasePlugin

logger = logging.getLogger(__name__)

_CONFIG_FILE  = Path("data/ig_accounts.json")
_SESSION_FILE = Path("data/ig_session.json")
_CRED_FILE    = Path("data/ig_credentials.json")
# 每个账号最多拉取的帖子数
_FETCH_LIMIT  = 20
# 视频列表缓存有效期（秒）
_MEDIA_CACHE_TTL = 1800



def _normalize_username(raw: str) -> str:
    raw = raw.strip()
    raw = re.sub(r"https?://(www\.)?instagram\.com/", "", raw)
    raw = raw.lstrip("@").rstrip("/")
    return raw.split("?")[0]


class IgPlugin(BasePlugin):
    name        = "ig"
    description = "#ig 从配置的 Instagram 账号随机发送视频"
    version     = "1.0.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._accounts: list[str] = []
        self._cl = None   # instagrapi Client
        # 缓存：username -> user_id（永久有效）
        self._uid_cache: dict[str, str] = {}
        # 缓存：username -> (timestamp, [video_pk, ...])
        self._media_cache: dict[str, tuple[float, list]] = {}

    async def on_startup(self):
        self._load_config()
        # 如果已保存凭据，尝试自动登录
        cred = self._load_credentials()
        if cred:
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._init_instagram(cred["username"], cred["password"])
            )
        else:
            logger.info("[ig] 未配置登录信息，请发送 #ig login <用户名> <密码> 登录")
        logger.info("[ig] 插件就绪，已配置 %d 个账号", len(self._accounts))

    def _init_instagram(self, username: str, password: str):
        """初始化 instagrapi 客户端并登录。"""
        try:
            from instagrapi import Client
        except ImportError:
            logger.error("[ig] 未安装 instagrapi，请运行: pip install instagrapi")
            return

        if not username or not password:
            logger.error("[ig] 用户名或密码为空")
            return

        cl = Client()
        cl.delay_range = [1, 3]

        if _SESSION_FILE.exists():
            try:
                cl.load_settings(_SESSION_FILE)
                cl.login(username, password)
                logger.info("[ig] 使用已有 session 登录成功")
            except Exception:
                logger.info("[ig] session 失效，重新登录...")
                cl = Client()
                cl.delay_range = [1, 3]
                cl.login(username, password)
        else:
            cl.login(username, password)
            logger.info("[ig] 登录成功")

        cl.dump_settings(_SESSION_FILE)
        self._cl = cl

    async def setup(self):
        prefix = re.escape(self.config.get("CMD_PREFIX", "#"))
        cmd_re = re.compile(rf"^{prefix}ig(?:\s+(.*))?$", re.IGNORECASE)

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

            if action == "login":
                await self._cmd_login(event.chat_id, target)
            elif action == "logout":
                await self._cmd_logout(event.chat_id)
            elif action == "add":
                await self._cmd_add(event.chat_id, target)
            elif action == "del":
                await self._cmd_del(event.chat_id, target)
            elif action == "list":
                await self._cmd_list(event.chat_id)
            else:
                await self._cmd_send(event.chat_id)

    # ── 子命令 ────────────────────────────────────────────────
    async def _cmd_login(self, chat_id: int, raw: str):
        parts = raw.split(maxsplit=1)
        if len(parts) < 2:
            await self._tip(
                chat_id,
                "用法：`#ig login <用户名> <密码>`\n"
                "示例：`#ig login myaccount MyPassword123`",
            )
            return

        username, password = parts[0].strip(), parts[1].strip()
        tip = await self.client.send_message(chat_id, f"登录 Instagram 中（{username}）...")
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, lambda: self._init_instagram(username, password)
            )
        except Exception as e:
            logger.error("[ig] 登录失败: %s", e)
            await tip.edit(f"登录失败：{e}")
            await asyncio.sleep(6)
            try:
                await tip.delete()
            except Exception:
                pass
            return

        if not self._cl:
            await tip.edit("登录失败，请检查用户名和密码")
            await asyncio.sleep(5)
            try:
                await tip.delete()
            except Exception:
                pass
            return

        # 保存凭据
        self._save_credentials(username, password)
        await tip.edit(f"Instagram 登录成功\n当前用户：`{username}`", parse_mode="md")
        await asyncio.sleep(4)
        try:
            await tip.delete()
        except Exception:
            pass

    async def _cmd_logout(self, chat_id: int):
        self._cl = None
        # 清除保存的文件
        for f in (_CRED_FILE, _SESSION_FILE):
            try:
                if f.exists():
                    f.unlink()
            except Exception:
                pass
        await self._tip(chat_id, "已退出 Instagram 并清除凭据")
    async def _cmd_add(self, chat_id: int, raw: str):
        if not raw:
            await self._tip(chat_id, "用法：`#ig add @username`")
            return

        raws = [s for s in re.split(r"[\s,，]+", raw) if s]
        ok_list, dup_list = [], []

        for r in raws:
            u = _normalize_username(r)
            if u in self._accounts:
                dup_list.append(u)
            else:
                self._accounts.append(u)
                ok_list.append(u)

        if ok_list:
            self._save_config()

        lines = []
        if ok_list:
            lines.append("已添加：" + "、".join(f"`{u}`" for u in ok_list))
        if dup_list:
            lines.append("已存在，跳过：" + "、".join(f"`{u}`" for u in dup_list))
        lines.append(f"\n当前共 {len(self._accounts)} 个账号")
        await self._tip(chat_id, "\n".join(lines))

    async def _cmd_del(self, chat_id: int, raw: str):
        if not raw:
            await self._tip(chat_id, "用法：`#ig del @username`")
            return

        raws = [s for s in re.split(r"[\s,，]+", raw) if s]
        ok_list, miss_list = [], []

        for r in raws:
            u = _normalize_username(r)
            if u in self._accounts:
                self._accounts.remove(u)
                ok_list.append(u)
            else:
                miss_list.append(u)

        if ok_list:
            self._save_config()

        lines = []
        if ok_list:
            lines.append("已移除：" + "、".join(f"`{u}`" for u in ok_list))
        if miss_list:
            lines.append("不在列表，跳过：" + "、".join(f"`{u}`" for u in miss_list))
        lines.append(f"\n当前共 {len(self._accounts)} 个账号")
        await self._tip(chat_id, "\n".join(lines))

    async def _cmd_list(self, chat_id: int):
        if not self._accounts:
            await self._tip(chat_id, "尚未配置账号\n使用 `#ig add @用户名` 添加")
            return

        lines = ["**Instagram 账号**", ""]
        for i, u in enumerate(self._accounts, 1):
            lines.append(f"  {i}. `{u}`")
        lines.append(f"\n共 {len(self._accounts)} 个账号")
        await self._tip(chat_id, "\n".join(lines), delay=8)

    async def _cmd_send(self, chat_id: int):
        if not self._accounts:
            await self._tip(chat_id, "尚未配置账号\n请先使用 `#ig add @用户名` 添加")
            return

        if not self._cl:
            await self._tip(
                chat_id,
                "Instagram 未登录\n请先使用：`#ig login <用户名> <密码>`",
            )
            return

        tip = await self.client.send_message(chat_id, "随机抽取 Instagram 视频中...")

        # 随机选择账号，最多重试3次
        video_path = None
        tried_accounts = set()
        max_retries = min(3, len(self._accounts))

        try:
            for attempt in range(max_retries):
                username = random.choice(self._accounts)
                if username in tried_accounts:
                    continue
                tried_accounts.add(username)

                try:
                    media_path = await asyncio.get_running_loop().run_in_executor(
                        None, self._fetch_random_video, username
                    )
                    if media_path:
                        video_path = media_path
                        break
                except Exception as e:
                    logger.warning("[ig] 获取 %s 视频失败: %s", username, e)
                    continue

            if not video_path:
                await tip.edit("未找到可用视频，请检查账号配置")
                await asyncio.sleep(4)
                try:
                    await tip.delete()
                except Exception:
                    pass
                return

            await tip.edit("发送中...")
            await self.client.send_file(
                chat_id,
                file=video_path,
                spoiler=True,
            )
            await tip.delete()

        finally:
            # 清理临时文件
            if video_path and Path(video_path).exists():
                try:
                    Path(video_path).unlink()
                except Exception:
                    pass

    # ── 核心抓取 ──────────────────────────────────────────────

    def _fetch_random_video(self, username: str) -> str | None:
        """
        从指定 Instagram 账号随机取一条视频并下载到临时目录，
        返回本地文件路径。
        user_id 永久缓存；视频列表缓存 _MEDIA_CACHE_TTL 秒。
        """
        # 1. 获取 user_id（缓存）
        if username not in self._uid_cache:
            self._uid_cache[username] = self._cl.user_id_from_username(username)
        user_id = self._uid_cache[username]

        # 2. 获取视频 pk 列表（带 TTL 缓存）
        now = time.monotonic()
        cache_entry = self._media_cache.get(username)
        if cache_entry and now - cache_entry[0] < _MEDIA_CACHE_TTL:
            video_pks = cache_entry[1]
            logger.debug("[ig] 使用缓存视频列表 %s (%d 条)", username, len(video_pks))
        else:
            medias = self._cl.user_medias(user_id, amount=_FETCH_LIMIT)
            video_pks = [m.pk for m in medias if m.media_type == 2]
            self._media_cache[username] = (now, video_pks)
            logger.debug("[ig] 已刷新视频列表 %s (%d 条)", username, len(video_pks))

        if not video_pks:
            return None

        # 3. 随机选一条并下载
        chosen_pk = random.choice(video_pks)
        tmp_dir = Path(tempfile.gettempdir())
        path = self._cl.video_download(chosen_pk, folder=tmp_dir)
        return str(path)

    # ── 工具方法 ──────────────────────────────────────────────

    async def _tip(self, chat_id: int, text: str, delay: int = 5):
        msg = await self.client.send_message(chat_id, text)
        await asyncio.sleep(delay)
        try:
            await msg.delete()
        except Exception:
            pass

    # ── 配置持久化 ────────────────────────────────────────────

    def _save_credentials(self, username: str, password: str):
        try:
            _CRED_FILE.parent.mkdir(parents=True, exist_ok=True)
            _CRED_FILE.write_text(
                json.dumps({"username": username, "password": password}, ensure_ascii=False)
            )
        except Exception as e:
            logger.warning("[ig] 保存凭据失败: %s", e)

    def _load_credentials(self) -> dict | None:
        try:
            if _CRED_FILE.exists():
                return json.loads(_CRED_FILE.read_text())
        except Exception as e:
            logger.warning("[ig] 加载凭据失败: %s", e)
        return None

    def _save_config(self):
        try:
            _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            _CONFIG_FILE.write_text(
                json.dumps({"accounts": self._accounts}, ensure_ascii=False, indent=2)
            )
        except Exception as e:
            logger.warning("[ig] 保存配置失败: %s", e)

    def _load_config(self):
        try:
            if _CONFIG_FILE.exists():
                data = json.loads(_CONFIG_FILE.read_text())
                self._accounts = data.get("accounts", [])
        except Exception as e:
            logger.warning("[ig] 加载配置失败: %s", e)
