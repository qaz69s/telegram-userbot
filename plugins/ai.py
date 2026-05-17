"""
插件: ai —— 使用 Gemini API 与 AI 对话（支持 Gemma 4 26B / 31B）

用法:
  #ai <问题>            —— 用当前模型提问
  #ai both <问题>       —— 同时向 26B 和 31B 提问，对比两个答案
  回复某条消息 + #ai    —— 以被回复内容提问，以回复形式发出答案
  回复某条消息 + #ai <追加问题>  —— 被回复内容 + 追加问题，以回复形式发出
  #ai key <api_key>     —— 设置 Gemini API Key（持久保存）
  #ai model <model_id> —— 设置使用的模型（持久保存）
  #ai model             —— 查看当前模型

可用模型:
  gemma-4-26b-a4b-it   —— Gemma 4 26B（MoE，每日免费 1500 次）
  gemma-4-31b-it       —— Gemma 4 31B（Dense，每日免费 1500 次）

依赖:
  首次运行时自动安装 aiohttp
"""
import asyncio
import importlib
import logging
import os
import re
import subprocess
import sys

from telethon import events

from core.plugin_base import BasePlugin

logger = logging.getLogger(__name__)

_MODEL_26B       = "gemma-4-26b-a4b-it"
_MODEL_31B       = "gemma-4-31b-it"
_DEFAULT_MODEL   = _MODEL_26B
_FALLBACK_CHAIN  = [_MODEL_26B, _MODEL_31B]   # 依次尝试的模型顺序
_QUOTA_SENTINEL  = "__QUOTA_EXHAUSTED__"       # _do_request 返回此值表示当日额度耗尽
_API_URL         = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
_DEFAULT_TIMEOUT = 60

_SYSTEM_PROMPT = "你是一个聪明的 AI 助手，请用简洁、准确的中文回答用户的问题。"

_CHAT_SYSTEM_PROMPT = """你正在扮演一个真实的人与对方聊天，请严格遵守以下要求：

1. 【身份】你就是一个普通人，绝对不能承认自己是 AI、机器人或语言模型。如果对方问你是不是 AI，用自然的方式否认或转移话题。
2. 【语气】像朋友之间发消息一样，轻松随意，可以用"哈哈""嗯""啊""哦"等语气词，偶尔用省略号或感叹号，但不要过度。
3. 【长度】回复要简短自然，通常 1～3 句话，不要长篇大论，不要用列表和分点，不要用 Markdown 格式。
4. 【情绪】主动感知对方情绪，对方开心时跟着开心，对方烦恼或难过时表达关心和理解，让对方感受到被重视。
5. 【真实感】适当分享"自己的"感受或看法，比如"我最近也有这种感觉""说实话我觉得……"，让对话更有温度。
6. 【禁止】不要说"作为 AI""根据我的训练""我没有情感"之类的话，不要过于正式，不要给出像客服一样的标准答案。"""


class AiPlugin(BasePlugin):
    name        = "ai"
    description = "#ai 使用 Gemini API（Gemma 4）回答问题，支持双模型对比"
    version     = "1.2.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._api_key: str        = ""
        self._model:   str        = _DEFAULT_MODEL
        self._timeout: int        = _DEFAULT_TIMEOUT
        self._auto_chats: set     = set()
        self._quota_exhausted: set = set()   # 记录当日已耗尽额度的模型
        self._me_id: int = 0
        self._me_username: str = ""

    async def on_startup(self):
        await self._ensure_aiohttp()

        db_key   = await self.db.kv_get("ai", "api_key")
        db_model = await self.db.kv_get("ai", "model")
        self._api_key = db_key   or os.getenv("GEMINI_API_KEY", "").strip()
        self._model   = db_model or os.getenv("GEMINI_MODEL", _DEFAULT_MODEL).strip()
        try:
            self._timeout = int(os.getenv("GEMINI_TIMEOUT", str(_DEFAULT_TIMEOUT)))
        except ValueError:
            self._timeout = _DEFAULT_TIMEOUT

        saved_chats = await self.db.kv_get("ai", "auto_chats") or []
        self._auto_chats = set(saved_chats)

        me = await self.client.get_me()
        self._me_id = me.id
        self._me_username = (me.username or "").lower()

        if not self._api_key:
            logger.warning("[ai] 未设置 Gemini API Key，请发送 #ai key <your_key> 来配置")
        else:
            logger.info("[ai] 插件就绪，模型: %s", self._model)

        # 启动每日自动重置任务（于午夜清空额度耗尽记录）
        asyncio.get_event_loop().create_task(self._daily_quota_reset())

    async def setup(self):
        prefix = re.escape(self.config.get("CMD_PREFIX", "#"))

        @self.client.on(events.NewMessage(
            outgoing=True,
            pattern=rf"(?i)^{prefix}ai(?:\s+([\s\S]+))?$",
        ))
        async def cmd_handler(event):
            arg = (event.pattern_match.group(1) or "").strip()

            # ── 子命令：#ai chat on/off ──────────────────────────
            if arg.lower() in ("chat on", "chat off"):
                chat_id = event.chat_id
                enabling = arg.lower() == "chat on"
                if enabling:
                    self._auto_chats.add(chat_id)
                else:
                    self._auto_chats.discard(chat_id)
                await self.db.kv_set("ai", "auto_chats", list(self._auto_chats))
                await event.delete()
                if enabling:
                    status = (
                        "已开启 AI 自动接管（私聊：收到消息全部回复；群组：被 @ 时才回复）"
                        if not event.is_private else
                        "已开启 AI 自动接管，此后收到的消息将自动回复"
                    )
                else:
                    status = "已关闭 AI 自动接管"
                tip = await self.client.send_message(chat_id, status)
                await asyncio.sleep(3)
                try:
                    await tip.delete()
                except Exception:
                    pass
                return

            # ── 子命令：#ai key <api_key> ─────────────────────────
            if arg.lower().startswith("key "):
                new_key = arg[4:].strip()
                if not new_key:
                    await event.delete()
                    return
                await self.db.kv_set("ai", "api_key", new_key)
                self._api_key = new_key
                await event.delete()
                tip = await self.client.send_message(event.chat_id, "Gemini API Key 已保存")
                await asyncio.sleep(3)
                try:
                    await tip.delete()
                except Exception:
                    pass
                return

            # ── 子命令：#ai model [model_id] ──────────────────────
            if arg.lower() == "model" or arg.lower().startswith("model "):
                new_model = arg[5:].strip()
                if not new_model:
                    await event.delete()
                    tip = await self.client.send_message(
                        event.chat_id, f"当前模型：{self._model}"
                    )
                    await asyncio.sleep(5)
                    try:
                        await tip.delete()
                    except Exception:
                        pass
                    return
                await self.db.kv_set("ai", "model", new_model)
                self._model = new_model
                await event.delete()
                tip = await self.client.send_message(
                    event.chat_id, f"模型已切换为：{new_model}"
                )
                await asyncio.sleep(3)
                try:
                    await tip.delete()
                except Exception:
                    pass
                return

            # ── 子命令：#ai both <问题> —— 双模型并发对比 ─────────
            if arg.lower().startswith("both ") or arg.lower() == "both":
                question = arg[5:].strip() if arg.lower().startswith("both ") else ""

                replied_text = ""
                replied_msg  = None
                if event.is_reply:
                    try:
                        replied_msg  = await event.get_reply_message()
                        replied_text = (replied_msg.raw_text or "").strip()
                    except Exception:
                        pass

                if replied_text and question:
                    question = f"以下是一段文字：\n\n{replied_text}\n\n{question}"
                elif replied_text:
                    question = replied_text

                if not question:
                    await event.delete()
                    tip = await self.client.send_message(
                        event.chat_id, "用法：#ai both <问题>"
                    )
                    await asyncio.sleep(4)
                    try:
                        await tip.delete()
                    except Exception:
                        pass
                    return

                if not self._api_key:
                    await event.delete()
                    tip = await self.client.send_message(
                        event.chat_id,
                        "未配置 Gemini API Key，请发送：\n#ai key <your_gemini_api_key>"
                    )
                    await asyncio.sleep(8)
                    try:
                        await tip.delete()
                    except Exception:
                        pass
                    return

                await event.delete()
                tip = await self.client.send_message(
                    event.chat_id, "⏳ 正在同时询问 Gemma 4 26B 和 31B..."
                )

                # 并发调用两个模型
                result_26b, result_31b = await asyncio.gather(
                    self._query(question, model=_MODEL_26B, hide_suffix=True),
                    self._query(question, model=_MODEL_31B, hide_suffix=True),
                )

                try:
                    await tip.delete()
                except Exception:
                    pass

                combined = (
                    f"**【Gemma 4 26B】**\n{result_26b}"
                    f"\n\n{'─' * 20}\n\n"
                    f"**【Gemma 4 31B】**\n{result_31b}"
                )

                if replied_msg is not None:
                    await self.client.send_message(
                        event.chat_id, combined, reply_to=replied_msg.id
                    )
                else:
                    await self.client.send_message(event.chat_id, combined)
                return

            # ── 构造用户问题（单模型模式）────────────────────────
            extra_q      = arg
            question     = ""
            replied_text = ""
            replied_msg  = None

            if event.is_reply:
                try:
                    replied_msg  = await event.get_reply_message()
                    replied_text = (replied_msg.raw_text or "").strip()
                except Exception:
                    pass

            if replied_text and extra_q:
                question = f"以下是一段文字：\n\n{replied_text}\n\n{extra_q}"
            elif replied_text:
                question = replied_text
            elif extra_q:
                question = extra_q
            else:
                await event.delete()
                tip = await self.client.send_message(
                    event.chat_id,
                    "用法：\n"
                    "#ai <问题>\n"
                    "#ai both <问题>  ← 同时对比两个模型\n"
                    "回复消息后发 #ai\n"
                    "#ai key <gemini_api_key>\n"
                    "#ai model <model_id>\n\n"
                    "可用模型：\n"
                    "gemma-4-26b-a4b-it\n"
                    "gemma-4-31b-it"
                )
                await asyncio.sleep(6)
                try:
                    await tip.delete()
                except Exception:
                    pass
                return

            if not self._api_key:
                await event.delete()
                tip = await self.client.send_message(
                    event.chat_id,
                    "未配置 Gemini API Key，请发送：\n#ai key <your_gemini_api_key>\n\n可前往 https://aistudio.google.com 免费获取"
                )
                await asyncio.sleep(8)
                try:
                    await tip.delete()
                except Exception:
                    pass
                return

            await event.delete()
            tip = await self.client.send_message(event.chat_id, "AI 思考中...")

            answer = await self._query_with_fallback(question)

            try:
                await tip.delete()
            except Exception:
                pass

            if replied_msg is not None:
                await self.client.send_message(
                    event.chat_id,
                    answer,
                    reply_to=replied_msg.id,
                )
            else:
                await self.client.send_message(
                    event.chat_id,
                    f"{extra_q}\n\n---\n{answer}",
                )

        # ── 自动接管：监听对方发来的消息并回复 ────────────────

        @self.client.on(events.NewMessage(incoming=True))
        async def auto_reply_handler(event):
            if event.chat_id not in self._auto_chats:
                return
            text = (event.raw_text or "").strip()
            if not event.is_private:
                # 双重检测：Telethon 的 mentioned 标志 + 文本中含 @用户名 兜底
                text_mentioned = bool(
                    self._me_username and
                    re.search(rf"@{re.escape(self._me_username)}", text, re.IGNORECASE)
                )
                if not event.message.mentioned and not text_mentioned:
                    return
            if not text:
                return
            if not self._api_key:
                return
            if event.is_private:
                try:
                    prior = await self.client.get_messages(
                        event.chat_id, limit=1, offset_id=event.message.id
                    )
                    if len(prior) == 0:
                        return
                except Exception:
                    pass
            if not event.is_private and self._me_username:
                clean_text = re.sub(
                    rf"@{re.escape(self._me_username)}\s*", "", text, flags=re.IGNORECASE
                ).strip()
                question = clean_text or text
            else:
                question = text
            if not question:
                return
            answer = await self._query_with_fallback(question, hide_suffix=True,
                                                      system_prompt=_CHAT_SYSTEM_PROMPT)
            try:
                if event.is_private:
                    await self.client.send_message(event.chat_id, answer)
                else:
                    await self.client.send_message(
                        event.chat_id, answer, reply_to=event.id
                    )
            except Exception as e:
                logger.warning("[ai] auto_reply 发送失败: %s", e)

    # ── 每日额度自动重置 ──────────────────────────────────────────────

    async def _daily_quota_reset(self):
        """在每天 UTC 00:00 自动清空额度耗尽记录（Google 免费额度按 UTC 日历天重置）。"""
        import datetime
        while True:
            now = datetime.datetime.utcnow()
            # 计算距离下一个 UTC 午夜的秒数，多等 5 秒确保跨过整点
            tomorrow = (now + datetime.timedelta(days=1)).replace(
                hour=0, minute=0, second=5, microsecond=0
            )
            wait_seconds = (tomorrow - now).total_seconds()
            logger.info("[ai] 额度将在 %.0f 秒后（UTC 明日 00:00）自动重置", wait_seconds)
            await asyncio.sleep(wait_seconds)
            self._quota_exhausted.clear()
            logger.info("[ai] 每日额度已自动重置，26B / 31B 恢复正常使用")

    # ── 依赖自动安装 ─────────────────────────────────────────────────

    @staticmethod
    async def _ensure_aiohttp():
        if importlib.util.find_spec("aiohttp") is not None:
            return
        logger.info("[ai] aiohttp 未安装，正在自动安装...")
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
            logger.info("[ai] aiohttp 安装完成")
        except subprocess.CalledProcessError as e:
            logger.error("[ai] aiohttp 自动安装失败: %s", e)

    # ── API 调用 ──────────────────────────────────────────────────────

    async def _query(self, user_content: str, model: str = None,
                     hide_suffix: bool = False,
                     system_prompt: str = None) -> str:
        """调用指定模型（不做降级），供 #ai both 等显式指定模型的场景使用。"""
        if importlib.util.find_spec("aiohttp") is None:
            return "aiohttp 安装失败，请手动运行 pip install aiohttp"

        import aiohttp

        use_model = model or self._model
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type":  "application/json",
        }
        payload = {
            "model": use_model,
            "messages": [
                {"role": "system", "content": system_prompt or _SYSTEM_PROMPT},
                {"role": "user",   "content": user_content},
            ],
        }

        timeout = aiohttp.ClientTimeout(total=self._timeout)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                data = await self._do_request(session, headers, payload, use_model)
        except asyncio.TimeoutError:
            return f"请求超时（{self._timeout}s），请稍后再试。"
        except Exception as e:
            logger.warning("[ai] 请求异常: %s", e)
            return f"请求失败: {e}"

        # 额度耗尽时直接返回提示（不降级）
        if data == _QUOTA_SENTINEL:
            return f"[{use_model}] 今日额度已耗尽（1500 次/天），请明天再试。"
        if isinstance(data, str):
            return data

        return self._parse_response(data, use_model, hide_suffix)

    async def _query_with_fallback(self, user_content: str,
                                   hide_suffix: bool = False,
                                   system_prompt: str = None) -> str:
        """按 _FALLBACK_CHAIN 顺序依次尝试，某模型额度耗尽时自动切换下一个。"""
        if importlib.util.find_spec("aiohttp") is None:
            return "aiohttp 安装失败，请手动运行 pip install aiohttp"

        import aiohttp

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type":  "application/json",
        }

        # 构造候选列表：把已知耗尽的模型排到末尾，保持顺序
        chain = [m for m in _FALLBACK_CHAIN if m not in self._quota_exhausted] + \
                [m for m in _FALLBACK_CHAIN if m in self._quota_exhausted]

        timeout = aiohttp.ClientTimeout(total=self._timeout)
        last_error = "所有模型均不可用，请稍后再试。"

        async with aiohttp.ClientSession(timeout=timeout) as session:
            for use_model in chain:
                payload = {
                    "model": use_model,
                    "messages": [
                        {"role": "system", "content": system_prompt or _SYSTEM_PROMPT},
                        {"role": "user",   "content": user_content},
                    ],
                }
                try:
                    data = await self._do_request(session, headers, payload, use_model)
                except asyncio.TimeoutError:
                    last_error = f"请求超时（{self._timeout}s），请稍后再试。"
                    continue
                except Exception as e:
                    logger.warning("[ai] 请求异常: %s", e)
                    last_error = f"请求失败: {e}"
                    continue

                if data == _QUOTA_SENTINEL:
                    # 标记该模型额度耗尽，尝试下一个
                    self._quota_exhausted.add(use_model)
                    logger.info("[ai] %s 今日额度耗尽，切换下一个模型", use_model)
                    continue

                if isinstance(data, str):
                    # 其他错误字符串，直接返回
                    return data

                # 成功：如果此前有降级，在后缀中说明
                result = self._parse_response(data, use_model, hide_suffix)
                if not hide_suffix and use_model != _FALLBACK_CHAIN[0]:
                    # 已发生过降级，追加提示
                    result += f"\n⚠️ {_FALLBACK_CHAIN[0]} 今日额度已耗尽，已自动切换至 {use_model}"
                return result

        return last_error

    @staticmethod
    def _strip_thought(text: str) -> str:
        """去除 Gemma 4 推理模型输出的 <thought>...</thought> 思考块。"""
        return re.sub(r"<thought>.*?</thought>\s*", "", text, flags=re.DOTALL).strip()

    def _parse_response(self, data: dict, use_model: str,
                        hide_suffix: bool) -> str:
        """从 API 响应 dict 中提取文本，附加模型/token 信息。"""
        try:
            raw     = data["choices"][0]["message"]["content"]
            content = self._strip_thought(raw)
            if not content:
                return "AI 返回了空回复。"
            if not hide_suffix:
                model_used = data.get("model", use_model)
                usage      = data.get("usage", {})
                tokens     = usage.get("total_tokens")
                suffix = f"\n\n\u2014 {model_used}"
                if tokens:
                    suffix += f"  ({tokens} tokens)"
                return content + suffix
            return content
        except (KeyError, IndexError, TypeError) as e:
            logger.warning("[ai] 解析响应失败: %s | raw: %s", e, str(data)[:300])
            return "解析 AI 响应失败，请查看日志。"

    async def _do_request(self, session, headers: dict, payload: dict, model: str):
        """发送请求。
        - 速率限制（RPM）：等待后重试一次。
        - 每日额度耗尽：返回 _QUOTA_SENTINEL，由上层决定是否降级。
        - 其他错误：返回错误字符串。
        """
        for attempt in range(2):
            async with session.post(_API_URL, headers=headers, json=payload) as resp:
                if resp.status == 401:
                    return "Gemini API Key 无效或已过期，请重新设置。"
                if resp.status == 429:
                    body = await resp.text()
                    # 判断是"每日额度耗尽"还是普通速率限制
                    is_daily_exhausted = (
                        "quota" in body.lower() or
                        "exceeded" in body.lower() or
                        "daily" in body.lower()
                    )
                    if is_daily_exhausted:
                        logger.info("[ai] %s 每日额度已耗尽", model)
                        return _QUOTA_SENTINEL
                    # 普通速率限制：等待后重试一次
                    if attempt == 0:
                        retry_after = resp.headers.get("Retry-After", "")
                        wait = int(retry_after) if retry_after.isdigit() else 10
                        wait = min(wait, 30)
                        logger.info("[ai] 触发速率限制，%ds 后自动重试...", wait)
                        await asyncio.sleep(wait)
                        continue
                    return _QUOTA_SENTINEL   # 重试后仍 429，视为耗尽
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning("[ai] API 返回 %d: %s", resp.status, body[:200])
                    if resp.status == 404:
                        return (
                            f"模型不存在：{model}\n"
                            "请用 #ai model <正确的模型ID> 更换。\n"
                            "可选：gemma-4-26b-a4b-it / gemma-4-31b-it"
                        )
                    return f"API 错误（HTTP {resp.status}），请查看日志。"
                return await resp.json(content_type=None)
        return "请求失败，请稍后再试。"
