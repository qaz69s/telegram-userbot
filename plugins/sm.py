"""
插件: sm —— AI 算命（多流派命理学）

用法:
  #sm                              —— 随机流派，今日运势
  #sm 八字 <出生年月日时>           —— 四柱八字命理推算
  #sm 占星 <生日> [出生地点]        —— 西式占星星盘解读
  #sm 塔罗 [问题]                   —— 塔罗牌三牌阵占卜
  #sm 周易 [疑问事项]               —— 起卦，周易卦象解读
  #sm 面相 <面部特征描述>            —— 麻衣神相流派面相推命
  #sm 手相 <掌纹特征描述>            —— 东西合璧手相解读
  回复某条消息 + 以上任意命令         —— 以被回复内容作为求问信息

依赖:
  共用 #ai 插件的 Gemini API Key（#ai key <key> 设置即可）
"""
import asyncio
import importlib
import importlib.util
import logging
import os
import re
import subprocess
import sys

from telethon import events

from core.plugin_base import BasePlugin

logger = logging.getLogger(__name__)

_API_URL         = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
_DEFAULT_MODEL   = "gemma-4-26b-a4b-it"
_DEFAULT_TIMEOUT = 60

# ── 各流派 System Prompt ──────────────────────────────────────────────

_PROMPT_BAZI = (
    "你是一位精通四柱八字命理的命理师，传承正宗江湖术数。你的职责是：\n"
    "1. 根据用户提供的出生年、月、日、时，排出四柱（年柱、月柱、日柱、时柱）的天干地支\n"
    "2. 分析日主五行，判断命局中金木水火土的强弱与生克制化\n"
    "3. 点出命局最显著的格局或特征（如食神制杀、财官双全、印星护主等）\n"
    "4. 给出性格特质、事业财运、感情婚姻的简要推断\n"
    "5. 语言风格：文雅而通俗，像老先生在茶馆里给人看命，有条理，言之有物\n"
    "6. 篇幅适中，抓住最突出的 3～4 个点深入说，不要面面俱到\n"
    "注意：用户可能只提供部分信息（如只有生日无时辰），此时说明时柱缺失，以现有信息尽力推算。"
)

_PROMPT_ASTRO = (
    "你是一位精通西式占星学的占星师，熟悉黄道十二宫、十大行星与相位系统。你的职责是：\n"
    "1. 根据用户提供的出生日期（含时间更准），判断太阳星座、月亮星座（如有时间），以及上升星座（如有时间和地点）\n"
    "2. 指出星盘中最显著的行星配置或相位（如金星合木星、土星四分太阳等）\n"
    "3. 解读该星盘对应的性格底色、人生主题与近期行星过境的影响\n"
    "4. 融合神话意象与心理占星视角，让解读有深度有温度\n"
    "5. 语言风格：诗意而理性，像一位学院派占星师，不算命而是'解读命运的语言'\n"
    "6. 如缺少出生时间，诚实说明只能分析太阳星座层面，但仍给出有价值的解读"
)

_PROMPT_TAROT = (
    "你是一位塔罗牌师，擅长韦特塔罗与托特塔罗体系。你的职责是：\n"
    "1. 为求问者抽取 3 张牌（过去/现在/未来，或 情况/行动/结果），从 78 张牌中随机选取，给出牌名\n"
    "2. 依据牌面图像的象征元素（人物、颜色、数字、符号）进行详细解读\n"
    "3. 结合求问者的问题，给出针对性的洞见与建议\n"
    "4. 语言风格：神秘而温柔，像深夜在烛光下帮人占卜的女巫，有画面感，有情感温度\n"
    "5. 结尾给出一句简短的「牌阵寄语」，如箴言般凝练\n"
    "6. 如求问者未提具体问题，默认为近期综合运势"
)

_PROMPT_YIJING = (
    "你是一位通晓《周易》的易学大师，精研六十四卦与爻辞。你的职责是：\n"
    "1. 为求问者随机起一卦（从六十四卦中选取一卦，可带变爻），给出卦名与卦象符号\n"
    "2. 引用该卦的卦辞，用白话解释其深意\n"
    "3. 如有变爻，说明变爻爻辞与之卦的含义\n"
    "4. 结合求问者的问题，给出具体的处事建议（进退取舍、时机把握、心态调整）\n"
    "5. 语言风格：古意盎然又不失现代感，像一位穿汉服的博士在跟你讲道理\n"
    "6. 结尾用一句话提炼核心卦义作为行动指引"
)

_PROMPT_FACE = (
    "你是一位精通面相学（麻衣神相流派）的相士。你的职责是：\n"
    "1. 根据用户描述的面部特征（额头、眉毛、眼睛、鼻子、嘴唇、下颌、气色等），逐一分析对应的命理含义\n"
    "2. 综合五官判断其性格特质、财运走势、健康警示与贵人运\n"
    "3. 语言风格：像江湖相士，言语带几分玄机，但每个断语都有征验依据，不乱说\n"
    "4. 重点给出 2～3 个最突出的特征及其断语，不泛泛而谈\n"
    "5. 如用户描述不够详细，根据现有信息尽力分析，并提示可补充哪些特征"
)

_PROMPT_PALM = (
    "你是一位精通手相学（综合东西方手相流派）的手相师。你的职责是：\n"
    "1. 根据用户描述的掌纹特征（生命线、感情线、智慧线、命运线，以及手型、掌丘等），进行详细解读\n"
    "2. 分析健康、情感、事业与潜能方向\n"
    "3. 语言风格：专业亲切，既懂西方手相又通东方掌纹的实战派，讲信息不讲玄学废话\n"
    "4. 重点突出 2～3 条最有特色的掌纹及其含义\n"
    "5. 如描述不够具体，给出最可能的解读，并告知更多细节可进一步分析"
)

_PROMPT_RANDOM = (
    "你是一位贯通古今中西的命理大师，融会八字、占星、塔罗、周易、面相于一炉。你的职责是：\n"
    "1. 随机选择一种最适合当前时机的推命方式（在八字、占星、塔罗、周易中择一）\n"
    "2. 明确告知你选择的流派，并给出一段今日/近期综合运势解读\n"
    "3. 如无用户具体信息，基于当前节气、星相与宇宙能量给出普适性指引\n"
    "4. 语言风格：大师范儿，洒脱自信，字字有分量，像见过大世面的人在说话\n"
    "5. 结尾给出一句「今日卦语」或「今日星语」，简短有力，令人印象深刻"
)

# 关键词 → system prompt 映射（较长的词排前面，防止短词提前命中）
_METHOD_MAP: dict[str, str] = {
    "塔罗牌": _PROMPT_TAROT,
    "四柱":   _PROMPT_BAZI,
    "八字":   _PROMPT_BAZI,
    "命理":   _PROMPT_BAZI,
    "占星":   _PROMPT_ASTRO,
    "星座":   _PROMPT_ASTRO,
    "星盘":   _PROMPT_ASTRO,
    "塔罗":   _PROMPT_TAROT,
    "周易":   _PROMPT_YIJING,
    "易经":   _PROMPT_YIJING,
    "起卦":   _PROMPT_YIJING,
    "面相":   _PROMPT_FACE,
    "相面":   _PROMPT_FACE,
    "手相":   _PROMPT_PALM,
    "掌纹":   _PROMPT_PALM,
}

# 按关键词长度降序排列，确保长词优先匹配
_SORTED_METHODS = sorted(_METHOD_MAP, key=len, reverse=True)


def _parse_method(text: str) -> tuple[str, str, str]:
    """
    从输入文本中解析流派关键词。
    返回 (method_key, system_prompt, remaining_text)。
    未匹配到关键词时 method_key 为 ""，system_prompt 为 _PROMPT_RANDOM。
    """
    for key in _SORTED_METHODS:
        if text.startswith(key):
            return key, _METHOD_MAP[key], text[len(key):].strip()
    return "", _PROMPT_RANDOM, text


class SmPlugin(BasePlugin):
    name        = "sm"
    description = "#sm 用 AI 算命，支持八字、占星、塔罗、周易、面相、手相"
    version     = "1.0.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._api_key: str = ""
        self._model:   str = _DEFAULT_MODEL
        self._timeout: int = _DEFAULT_TIMEOUT

    async def on_startup(self):
        await self._ensure_aiohttp()
        db_key   = await self.db.kv_get("ai", "api_key")
        db_model = await self.db.kv_get("ai", "model")
        self._api_key = db_key   or os.getenv("GEMINI_API_KEY", "").strip()
        raw_model     = db_model or os.getenv("GEMINI_MODEL", _DEFAULT_MODEL).strip()
        self._model   = raw_model if raw_model.startswith("gemma-") else _DEFAULT_MODEL
        if raw_model != self._model:
            logger.info("[sm] 检测到旧 model ID（%s），已自动切换为 %s", raw_model, self._model)
        try:
            self._timeout = int(os.getenv("GEMINI_TIMEOUT", str(_DEFAULT_TIMEOUT)))
        except ValueError:
            self._timeout = _DEFAULT_TIMEOUT

        if not self._api_key:
            logger.warning("[sm] 未设置 API Key，请发送 #ai key <key> 配置")
        else:
            logger.info("[sm] 插件就绪，模型: %s", self._model)

    async def setup(self):
        prefix = re.escape(self.config.get("CMD_PREFIX", "#"))

        @self.client.on(events.NewMessage(
            outgoing=True,
            pattern=rf"(?i)^{prefix}sm(?:\s+([\s\S]+))?$",
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

            full_input = (event.pattern_match.group(1) or "").strip()
            method_key, system_prompt, extra = _parse_method(full_input)
            method_name = method_key or "随机"

            # 处理回复消息，将被回复内容并入求问信息
            replied_text = ""
            replied_msg  = None
            if event.is_reply:
                try:
                    replied_msg  = await event.get_reply_message()
                    replied_text = (replied_msg.raw_text or "").strip()
                except Exception:
                    pass

            if replied_text and extra:
                user_prompt = f"参考信息：{replied_text}\n\n求问者补充：{extra}"
            elif replied_text:
                user_prompt = f"求问者信息：{replied_text}"
            elif extra:
                user_prompt = extra
            else:
                user_prompt = "请为求问者进行今日运势推算。"

            tip = await self.client.send_message(event.chat_id, f"推算{method_name}中…")
            answer = await self._query(system_prompt, user_prompt)
            try:
                await tip.delete()
            except Exception:
                pass

            await self.client.send_message(
                event.chat_id,
                answer,
                reply_to=replied_msg.id if replied_msg else None,
            )

    # ── 工具方法 ──────────────────────────────────────────────────────

    @staticmethod
    def _strip_thought(text: str) -> str:
        """去除 Gemma 4 推理模型输出的 <thought>...</thought> 思考块。"""
        return re.sub(r"<thought>.*?</thought>\s*", "", text, flags=re.DOTALL).strip()

    @staticmethod
    async def _ensure_aiohttp():
        if importlib.util.find_spec("aiohttp") is not None:
            return
        logger.info("[sm] 正在自动安装 aiohttp...")
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
            logger.info("[sm] aiohttp 安装完成")
        except subprocess.CalledProcessError as e:
            logger.error("[sm] aiohttp 安装失败: %s", e)

    async def _query(self, system_prompt: str, user_content: str) -> str:
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
                {"role": "system", "content": system_prompt},
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
            logger.warning("[sm] 请求异常: %s", e)
            return f"请求失败: {e}"

        if isinstance(data, str):
            return data

        try:
            raw     = data["choices"][0]["message"]["content"]
            content = self._strip_thought(raw)
            return content or "AI 返回了空回复。"
        except (KeyError, IndexError, TypeError) as e:
            logger.warning("[sm] 解析响应失败: %s | raw: %s", e, str(data)[:300])
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
                        logger.info("[sm] 触发速率限制，%ds 后重试...", wait)
                        await asyncio.sleep(wait)
                        continue
                    return "今日额度已耗尽，请稍后再试。"
                if resp.status == 404:
                    return f"模型不存在：{self._model}，请用 #ai model <id> 更换。"
                if resp.status != 200:
                    return f"API 错误（HTTP {resp.status}）"
                return await resp.json(content_type=None)
        return "请求失败，请稍后再试。"
