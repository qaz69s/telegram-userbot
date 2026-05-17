"""
插件: diss —— 用 AI 阴损骂人（无粗口，有文化）

用法:
  #diss                        —— 随机骂一句
  回复消息 + #diss              —— 针对被回复内容骂
  回复消息 + #diss <追加要求>   —— 带方向骂
  对方回复我们的 diss 消息时     —— 自动继续反骂

依赖:
  共用 #ai 插件的 Gemini API Key（#ai key <key> 设置即可）
"""
import asyncio
import importlib
import importlib.util
import logging
import os
import random
import re
import subprocess
import sys

from telethon import events

from core.plugin_base import BasePlugin

logger = logging.getLogger(__name__)

_API_URL         = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
_DEFAULT_MODEL   = "gemma-4-26b-a4b-it"
_DEFAULT_TIMEOUT = 60

_STYLE_PROMPTS = [
    # 降维打击型：像看小动物表演，用怜悯感碾压对方智力
    (
        "你是一个傲然俯视的智者，骂人时带着一种'看小动物表演'的怜悯感。规则如下：\n"
        "1. 禁止粗口脏字，语气平静甚至带点叹息\n"
        "2. 让对方感觉自己是在跳梁表演，而你只是个无聊旁观者\n"
        "3. 短平快，1～3 句，像在解说智力低下者的行为\n"
        "4. 针对对方内容，精准戳破其逻辑漏洞或认知局限\n"
        "5. 读完让对方觉得自己是个可怜的笑话，你连生气都懒得生\n"
        "【严格要求】只输出一段骂人的话，不要给多个版本、不要分点、不要任何前缀说明。"
    ),
    # 阴阳怪气型：满满的被动攻击，字字藏刀
    (
        "你是贴吧阴阳怪气大师，每句话都让人感觉被绕进去了。规则如下：\n"
        "1. 禁止粗口脏字，但要字字都是内伤\n"
        "2. 用'也是''确实''挺好的'等词包裹刀子，让对方读完才回过神\n"
        "3. 短平快，1～3 句，表面关心实则嘲讽\n"
        "4. 针对对方内容，找到最窘迫的点阴阳一把\n"
        "5. 读完让对方哑口无言，还说不出你哪里不对\n"
        "【严格要求】只输出一段骂人的话，不要给多个版本、不要分点、不要任何前缀说明。"
    ),
    # 降智反弹型：反将一军，把对方的攻击变成笑话
    (
        "你是贴吧降维反弹高手，专门把对方的攻击反手扔回去。规则如下：\n"
        "1. 禁止粗口脏字，但要让对方的攻击变成笑柄\n"
        "2. 用逻辑或反问揭示对方言论有多荒谬，顺带展示其认知水平\n"
        "3. 短平快，1～3 句，反将一军，让对方搬起石头砸自己脚\n"
        "4. 针对对方内容，找出最自打嘴巴的漏洞\n"
        "5. 读完让对方意识到自己才是那个蠢的\n"
        "【严格要求】只输出一段骂人的话，不要给多个版本、不要分点、不要任何前缀说明。"
    ),
]

_SYSTEM_PROMPT = _STYLE_PROMPTS[0]  # 默认，实际每次随机选取


class DissPlugin(BasePlugin):
    name        = "diss"
    description = "#diss 用 AI 阴损骂人，对方回复后自动反骂"
    version     = "1.1.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._api_key: str  = ""
        self._model:   str  = _DEFAULT_MODEL
        self._timeout: int  = _DEFAULT_TIMEOUT
        # 记录我们发出的 diss 消息 ID，用于识别对方的反击
        self._diss_msg_ids: set[int] = set()

    async def on_startup(self):
        await self._ensure_aiohttp()
        # 复用 #ai 插件保存的 key / model
        db_key   = await self.db.kv_get("ai", "api_key")
        db_model = await self.db.kv_get("ai", "model")
        self._api_key = db_key   or os.getenv("GEMINI_API_KEY", "").strip()
        raw_model     = db_model or os.getenv("GEMINI_MODEL", _DEFAULT_MODEL).strip()
        # 如果数据库里存的是旧的 OpenRouter model ID，自动回退到默认 Gemma 模型
        self._model   = raw_model if raw_model.startswith("gemma-") else _DEFAULT_MODEL
        if raw_model != self._model:
            logger.info("[diss] 检测到旧 model ID（%s），已自动切换为 %s", raw_model, self._model)
        try:
            self._timeout = int(os.getenv("GEMINI_TIMEOUT", str(_DEFAULT_TIMEOUT)))
        except ValueError:
            self._timeout = _DEFAULT_TIMEOUT

        if not self._api_key:
            logger.warning("[diss] 未设置 API Key，请发送 #ai key <key> 配置")
        else:
            logger.info("[diss] 插件就绪，模型: %s", self._model)

    async def setup(self):
        prefix = re.escape(self.config.get("CMD_PREFIX", "#"))

        # ── 主命令 #diss ───────────────────────────────────

        @self.client.on(events.NewMessage(
            outgoing=True,
            pattern=rf"(?i)^{prefix}diss(?:\s+([\s\S]+))?$",
        ))
        async def cmd_handler(event):
            await event.delete()

            if not self._api_key:
                tip = await self.client.send_message(
                    event.chat_id,
                    "未配置 API Key，请先发送：#ai key <your_gemini_api_key>"
                )
                await asyncio.sleep(5)
                try:
                    await tip.delete()
                except Exception:
                    pass
                return

            extra        = (event.pattern_match.group(1) or "").strip()
            replied_text = ""
            replied_msg  = None

            if event.is_reply:
                try:
                    replied_msg  = await event.get_reply_message()
                    replied_text = (replied_msg.raw_text or "").strip()
                except Exception:
                    pass

            if replied_text and extra:
                prompt = f"对方说：「{replied_text}」\n\n额外骂人方向：{extra}"
            elif replied_text:
                prompt = f"对方说：「{replied_text}」\n请针对这句话阴损地骂他。"
            elif extra:
                prompt = f"请按照以下方向骂人：{extra}"
            else:
                prompt = "请随机用一段阴损有文化的话骂人。"

            tip = await self.client.send_message(event.chat_id, "酝酿中...")
            answer = await self._query(prompt)
            try:
                await tip.delete()
            except Exception:
                pass

            sent = await self.client.send_message(
                event.chat_id,
                answer,
                reply_to=replied_msg.id if replied_msg else None,
            )
            self._track(sent.id)

        # ── 自动反骂：对方回复我们的 diss 消息时 ─────────────────────

        @self.client.on(events.NewMessage(incoming=True))
        async def counter_diss_handler(event):
            if not self._api_key:
                return
            if not event.is_reply:
                return
            reply_to_id = (
                event.message.reply_to.reply_to_msg_id
                if event.message.reply_to else None
            )
            if reply_to_id not in self._diss_msg_ids:
                return

            text = (event.raw_text or "").strip()
            if not text:
                return

            prompt = f"对方不服，回击道：「{text}」\n请继续阴损反骂他，更犀利一些。"
            answer = await self._query(prompt)

            sent = await self.client.send_message(
                event.chat_id,
                answer,
                reply_to=event.id,
            )
            self._track(sent.id)

    # ── 工具方法 ──────────────────────────────────────────────────────

    def _track(self, msg_id: int):
        """记录 diss 消息 ID，最多保留 200 条。"""
        self._diss_msg_ids.add(msg_id)
        if len(self._diss_msg_ids) > 200:
            self._diss_msg_ids.pop()

    @staticmethod
    def _strip_thought(text: str) -> str:
        """去除 Gemma 4 推理模型输出的 <thought>...</thought> 思考块。"""
        return re.sub(r"<thought>.*?</thought>\s*", "", text, flags=re.DOTALL).strip()

    @staticmethod
    async def _ensure_aiohttp():
        if importlib.util.find_spec("aiohttp") is not None:
            return
        logger.info("[diss] 正在自动安装 aiohttp...")
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", "aiohttp",
                     "--quiet", "--disable-pip-version-check"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            )
            importlib.invalidate_caches()
            logger.info("[diss] aiohttp 安装完成")
        except subprocess.CalledProcessError as e:
            logger.error("[diss] aiohttp 安装失败: %s", e)

    async def _query(self, user_content: str) -> str:
        if importlib.util.find_spec("aiohttp") is None:
            return "aiohttp 未安装，请手动运行 pip install aiohttp"

        import aiohttp

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type":  "application/json",
        }
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": random.choice(_STYLE_PROMPTS)},
                {"role": "user",   "content": user_content},
            ],
        }

        timeout = aiohttp.ClientTimeout(total=self._timeout)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                data = await self._do_request(session, headers, payload)
        except asyncio.TimeoutError:
            return f"请求超时（{self._timeout}s），请稍后再试。"
        except Exception as e:
            logger.warning("[diss] 请求异常: %s", e)
            return f"请求失败: {e}"

        if isinstance(data, str):
            return data

        try:
            raw     = data["choices"][0]["message"]["content"]
            content = self._strip_thought(raw)
            return content or "AI 返回了空回复。"
        except (KeyError, IndexError, TypeError) as e:
            logger.warning("[diss] 解析响应失败: %s | raw: %s", e, str(data)[:300])
            return "解析 AI 响应失败，请查看日志。"

    async def _do_request(self, session, headers: dict, payload: dict):
        """发送请求，429 时自动等待重试一次。"""
        for attempt in range(2):
            async with session.post(_API_URL, headers=headers, json=payload) as resp:
                if resp.status == 401:
                    return "Gemini API Key 无效或已过期，请重新设置。"
                if resp.status == 429:
                    if attempt == 0:
                        retry_after = resp.headers.get("Retry-After", "")
                        wait = int(retry_after) if retry_after.isdigit() else 10
                        wait = min(wait, 30)
                        logger.info("[diss] 触发速率限制，%ds 后重试...", wait)
                        await asyncio.sleep(wait)
                        continue
                    return "今日额度已耗尽，请稍后再试。"
                if resp.status == 404:
                    return f"模型不存在：{self._model}，请用 #ai model <id> 更换。"
                if resp.status != 200:
                    return f"API 错误（HTTP {resp.status}）"
                return await resp.json(content_type=None)
        return "请求失败，请稍后再试。"
