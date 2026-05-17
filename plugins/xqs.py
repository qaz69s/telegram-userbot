"""
插件: xqs —— 肯德基疯狂星期四文学生成器

用法:
  #xqs                  —— 随机风格，随机剧情，自动反转
  #xqs <主题/场景>       —— 指定主题，AI 创作反转剧情
  #xqs 风格             —— 查看可用风格列表
  #xqs <风格名> <主题>   —— 指定风格 + 主题
  回复某条消息 + #xqs    —— 以被回复内容为素材创作

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
_DEFAULT_TIMEOUT = 120

# ── 风格定义 ──────────────────────────────────────────────────────────

_STYLES = {
    "武侠": (
        "扭转乾坤型",
        (
            "你是一位深谙中国武侠小说创作的高手，笔力深厚，擅长金庸古龙风格。"
            "请创作一篇疯狂星期四文学，要求：\n"
            "1. 以武侠江湖为背景，人物有名有姓，武功招式有名有实\n"
            "2. 故事高潮迭起，必须有至少一次出人意料的大反转\n"
            "3. 反转须自然流畅，不可强行，且反转之后读者会有原来如此的顿悟感\n"
            "4. 语言风格：古朴苍劲，偶有白话，节奏紧凑，金句穿插\n"
            "5. 结尾必须以某种出其不意的方式引出【肯德基疯狂星期四】，并留下钱数（V我50或其他面值）\n"
            "6. 篇幅：400～600字，故事完整，开头、发展、反转、结尾缺一不可"
        ),
    ),
    "玄幻": (
        "洪荒气运型",
        (
            "你是一位洪荒流、无限流玄幻文学的老作者，擅长大场面大格局。"
            "请创作一篇疯狂星期四文学，要求：\n"
            "1. 设定宏大：可涉及诸天万界、天道轮回、气运争夺等概念\n"
            "2. 主角历经极致逆境——天道压制、被废丹田、本命灵宝被夺——看似走投无路\n"
            "3. 关键反转：隐藏的逆天机缘在最后一刻降临，但机缘的内容正是【肯德基疯狂星期四】\n"
            "4. 语言风格：磅礴霸气，动辄【诸天震颤】【大道共鸣】，夸张而自洽\n"
            "5. 结尾主角顿悟：原来肯德基才是这方世界的本源，并呼唤读者V钱\n"
            "6. 篇幅：400～600字，叙事完整，反转震撼"
        ),
    ),
    "言情": (
        "虐恋生死型",
        (
            "你是一位擅写虐恋、豪门、总裁文的言情作者，深得读者催泪。"
            "请创作一篇疯狂星期四文学，要求：\n"
            "1. 男女主角感情深厚，开篇却面临生死离别或重大误会\n"
            "2. 故事发展中无限虐心，读者以为要CP分离\n"
            "3. 关键反转：离别或绝境的背后，其实是某个与【肯德基疯狂星期四】相关的秘密\n"
            "4. 语言风格：细腻柔情，心理描写丰富，一句话能戳心\n"
            "5. 结尾用爱情的名义请求读者V钱，让人又好笑又感动\n"
            "6. 篇幅：400～600字，情感层次分明"
        ),
    ),
    "恐怖": (
        "午夜惊魂型",
        (
            "你是一位擅写悬疑恐怖短篇的作者，熟悉日式惊悚与国产志怪风格。"
            "请创作一篇疯狂星期四文学，要求：\n"
            "1. 开头营造强烈的恐怖氛围：鬼屋、灵异事件、诡异邻居、不合理的时间循环等\n"
            "2. 恐怖逐渐升级，主角陷入极度绝望，读者毛骨悚然\n"
            "3. 关键反转：揭示所有恐怖事件的真相，直接指向【肯德基疯狂星期四】——比如鬼魂饿了、诡异符文是优惠券等\n"
            "4. 语言风格：克制而压抑，细节恐怖，结尾反差越大越好\n"
            "5. 结尾用恐怖的方式索要V钱，令人喷饭\n"
            "6. 篇幅：400～600字，节奏先压后放"
        ),
    ),
    "历史": (
        "史册留名型",
        (
            "你是一位精通中国历史与野史的说书人，口才极佳，知古论今。"
            "请创作一篇疯狂星期四文学，要求：\n"
            "1. 选取一个历史或架空历史场景（皇朝更迭、名将决战、文人风骨等）\n"
            "2. 以严肃正史的笔法叙述，考据感拉满，令人信以为真\n"
            "3. 关键反转：某个改变历史走向的关键原因，竟是【肯德基疯狂星期四】\n"
            "4. 语言风格：文白夹杂，书卷气浓，偶有犀利点评\n"
            "5. 结尾以史家笔法记录：某某年某月某日，V我XX两白银，折合肯德基若干桶\n"
            "6. 篇幅：400～600字，历史感与反差感并重"
        ),
    ),
    "科幻": (
        "宇宙终局型",
        (
            "你是一位硬核科幻作家，擅长刘慈欣式宏大叙事与反人类直觉的推演。"
            "请创作一篇疯狂星期四文学，要求：\n"
            "1. 背景宏大：可涉及宇宙文明、费米悖论、暗黑森林、时间旅行等\n"
            "2. 叙事严肃，以第一或第三人称讲述文明级别的危机\n"
            "3. 关键反转：宇宙中所有高等文明的核心技术或信仰，统一归结为【肯德基疯狂星期四】\n"
            "4. 语言风格：理性克制、逻辑自洽，偶有末日诗意\n"
            "5. 结尾以星际广播的形式请求V钱，同频率覆盖可观测宇宙\n"
            "6. 篇幅：400～600字，硬科幻质感在线"
        ),
    ),
    "职场": (
        "社畜逆袭型",
        (
            "你是一位深度观察职场众生相的写实作家，擅写打工人的辛酸与智慧。"
            "请创作一篇疯狂星期四文学，要求：\n"
            "1. 以真实职场场景开始：连续加班、被甩锅、被PIP、项目黄了……\n"
            "2. 主角陷入绝境，情绪写到极致，读者共情拉满\n"
            "3. 关键反转：剧情峰回路转，转折点直接由【恰好是疯狂星期四】触发，彻底改变局面\n"
            "4. 语言风格：现代白话，接地气，职场暗语信手拈来，带着一丝打工人的苦中作乐\n"
            "5. 结尾打工人深刻领悟：没有什么绝境是一顿肯德基解决不了的，V我50\n"
            "6. 篇幅：400～600字，共鸣感强烈"
        ),
    ),
    "哲学": (
        "存在主义型",
        (
            "你是一位融贯东西方哲学的思想者，尼采、加缪、庄子信手拈来。"
            "请创作一篇疯狂星期四文学，要求：\n"
            "1. 从一个宏大的哲学命题出发：存在的意义、西西弗斯的巨石、虚无主义的深渊……\n"
            "2. 用哲学语言进行严肃推演，读者感到被灵魂扣问\n"
            "3. 关键反转：所有哲学追问最终指向【肯德基疯狂星期四】作为终极答案\n"
            "4. 语言风格：高度凝练，警句迭出，字字有重量，但结尾荒诞\n"
            "5. 以先贤之名索要V钱，赋予行为以形而上意义\n"
            "6. 篇幅：350～500字，密度高，读后有余音"
        ),
    ),
    "古风": (
        "词赋流觞型",
        (
            "你是一位精通诗词歌赋的古典文学工作者，七律、词牌、骈文无一不通。"
            "请创作一篇疯狂星期四文学，要求：\n"
            "1. 以古典文体写作：可选词牌（如《念奴娇》《水调歌头》）、赋文、或文言短章\n"
            "2. 意象典雅，情感真挚，前半段意境悠远，令人动容\n"
            "3. 关键反转：后半段或结句，古典意境突然被【肯德基疯狂星期四】破功，反差感极强\n"
            "4. 语言风格：前段古雅，后段反差暴击\n"
            "5. 结尾附白话翻译（可选），说明V钱面额\n"
            "6. 篇幅：古典部分200字左右即可，反差要足够猛"
        ),
    ),
    "童话": (
        "格林式反转型",
        (
            "你是一位擅写成人童话与暗黑童话的作家，一手温情一手刀。"
            "请创作一篇疯狂星期四文学，要求：\n"
            "1. 以经典童话设定开场：王子公主、恶龙城堡、魔法森林……用甜美语气叙述\n"
            "2. 故事走向黑暗：公主被困、王子牺牲、世界末日即将到来……读者以为坏结局\n"
            "3. 关键反转：拯救世界的神器或咒语，竟是【疯狂星期四】\n"
            "4. 语言风格：前段稚嫩温柔，后段反转要么荒诞要么暗黑，形成强烈撕裂感\n"
            "5. 结尾王子（或堂堂恶龙）向读者请求金币，折算为V钱\n"
            "6. 篇幅：350～500字，童趣与毒点并存"
        ),
    ),
}

_STYLE_ALIASES = {
    "武侠":   "武侠",
    "江湖":   "武侠",
    "玄幻":   "玄幻",
    "洪荒":   "玄幻",
    "言情":   "言情",
    "总裁":   "言情",
    "虐恋":   "言情",
    "恐怖":   "恐怖",
    "鬼故事": "恐怖",
    "惊悚":   "恐怖",
    "历史":   "历史",
    "正史":   "历史",
    "科幻":   "科幻",
    "宇宙":   "科幻",
    "职场":   "职场",
    "打工":   "职场",
    "社畜":   "职场",
    "哲学":   "哲学",
    "存在":   "哲学",
    "古风":   "古风",
    "诗词":   "古风",
    "童话":   "童话",
    "王子":   "童话",
}

# 通用补充 prompt，附加在所有风格后面，确保质量下限
_COMMON_SUFFIX = (
    "\n\n【创作铁律】\n"
    "- 反转要真实可信，不能是梦、穿越、突然死亡等廉价手段\n"
    "- 结尾肯德基疯狂星期四必须出现，V钱面额可创意发挥（V我50 / V我100 / V我一个鸡腿等）\n"
    "- 禁止在文中直接说这是一个反转故事，反转要让读者自己悟到\n"
    "- 语言流畅自然，零AI腔，像人写的\n"
    "- 直接给出故事正文，不要加任何前言、标题或后记"
)

# ── 解析用户输入 ──────────────────────────────────────────────────────

def _parse_input(text):
    """
    解析 #xqs 后面的参数。
    返回 (style_key, style_system_prompt, theme)。
    style_key 为空字符串表示随机风格。
    """
    if not text:
        return "", _random_style_prompt(), ""

    # 尝试匹配风格词
    for alias, key in _STYLE_ALIASES.items():
        if text.startswith(alias):
            remaining = text[len(alias):].strip()
            _, sys_p = _STYLES[key]
            return key, sys_p + _COMMON_SUFFIX, remaining

    # 没有匹配到风格，整体作为主题，随机风格
    return "", _random_style_prompt(), text


def _random_style_prompt():
    """构造随机风格 system prompt。"""
    styles_desc = "\n".join(
        "- %s（%s）" % (k, label) for k, (label, _) in _STYLES.items()
    )
    return (
        "你是一位精通各类文学风格的疯狂星期四文学大师，擅长在不同类型的故事中制造惊天大反转。\n"
        "可用风格清单：\n"
        + styles_desc + "\n\n"
        "请求：\n"
        "1. 随机选择上述风格之一（优先选最出人意料的那个），并在开头用一行标注【风格：XX】\n"
        "2. 创作一篇对应风格的、有完整剧情的疯狂星期四文（疯狂星期四 V我50 或其他创意版本结尾）\n"
        "3. 反转必须有，且出乎意料、在情理之中\n"
        "4. 洒脱自由，不拘一格，字里行间有那股子我就是要写给你看的气势\n"
        "5. 篇幅：400～600字\n"
        + _COMMON_SUFFIX
    )


class XqsPlugin(BasePlugin):
    name        = "xqs"
    description = "#xqs 肯德基疯狂星期四文学生成器，AI驱动的反转剧情"
    version     = "1.0.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._api_key = ""
        self._model   = _DEFAULT_MODEL
        self._timeout = _DEFAULT_TIMEOUT

    async def on_startup(self):
        await self._ensure_aiohttp()
        db_key   = await self.db.kv_get("ai", "api_key")
        db_model = await self.db.kv_get("ai", "model")
        self._api_key = db_key   or os.getenv("GEMINI_API_KEY", "").strip()
        raw_model     = db_model or os.getenv("GEMINI_MODEL", _DEFAULT_MODEL).strip()
        self._model   = raw_model if raw_model.startswith("gemma-") else _DEFAULT_MODEL
        try:
            self._timeout = int(os.getenv("GEMINI_TIMEOUT", str(_DEFAULT_TIMEOUT)))
        except ValueError:
            self._timeout = _DEFAULT_TIMEOUT

        if not self._api_key:
            logger.warning("[xqs] 未设置 API Key，请发送 #ai key <key> 配置")
        else:
            logger.info("[xqs] 插件就绪，模型: %s", self._model)

    async def setup(self):
        prefix = re.escape(self.config.get("CMD_PREFIX", "#"))

        @self.client.on(events.NewMessage(
            outgoing=True,
            pattern=rf"(?i)^{prefix}xqs(?:\s+([\s\S]+))?$",
        ))
        async def cmd_handler(event):
            await event.delete()

            arg = (event.pattern_match.group(1) or "").strip()

            # ── 查看风格列表 ──────────────────────────────────────
            if arg.lower() in ("风格", "帮助", "help"):
                lines = ["**疯狂星期四文学风格列表：**\n"]
                for k, (label, _) in _STYLES.items():
                    lines.append("- `%s` — %s" % (k, label))
                lines.append("\n用法示例：`#xqs 武侠 有人背叛了我`")
                tip = await self.client.send_message(event.chat_id, "\n".join(lines))
                await asyncio.sleep(15)
                try:
                    await tip.delete()
                except Exception:
                    pass
                return

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

            # ── 解析风格 & 主题 ────────────────────────────────────
            style_key, system_prompt, theme = _parse_input(arg)

            # 处理回复消息
            replied_text = ""
            replied_msg  = None
            if event.is_reply:
                try:
                    replied_msg  = await event.get_reply_message()
                    replied_text = (replied_msg.raw_text or "").strip()
                except Exception:
                    pass

            # 构造 user prompt
            if replied_text and theme:
                user_prompt = "故事素材（来自聊天记录）：%s\n\n指定主题：%s" % (replied_text, theme)
            elif replied_text:
                user_prompt = "请以以下内容为素材，创作疯狂星期四文：\n\n%s" % replied_text
            elif theme:
                user_prompt = "指定主题/场景：%s\n\n请围绕此主题创作。" % theme
            else:
                user_prompt = "请随机选择一个有意思的场景，自由发挥，创作一篇疯狂星期四文学。"

            # 发送等待提示
            style_display = "「%s」风格" % style_key if style_key else "随机风格"
            tip = await self.client.send_message(
                event.chat_id, "正在创作%s疯狂星期四文学，稍等…" % style_display
            )

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
    def _strip_thought(text):
        return re.sub(r"<thought>.*?</thought>\s*", "", text, flags=re.DOTALL).strip()

    async def _ensure_aiohttp(self):
        if importlib.util.find_spec("aiohttp") is not None:
            return
        logger.info("[xqs] 正在自动安装 aiohttp...")
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
            logger.info("[xqs] aiohttp 安装完成")
        except subprocess.CalledProcessError as e:
            logger.error("[xqs] aiohttp 安装失败: %s", e)

    async def _query(self, system_prompt, user_content):
        if importlib.util.find_spec("aiohttp") is None:
            return "aiohttp 未安装，请手动运行 pip install aiohttp"

        import aiohttp

        headers = {
            "Authorization": "Bearer %s" % self._api_key,
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
            return "请求超时（%ds），请稍后再试。" % self._timeout
        except Exception as e:
            logger.warning("[xqs] 请求异常: %s", e)
            return "请求失败: %s" % e

        if isinstance(data, str):
            return data

        try:
            raw     = data["choices"][0]["message"]["content"]
            content = self._strip_thought(raw)
            return content or "AI 返回了空回复。"
        except (KeyError, IndexError, TypeError) as e:
            logger.warning("[xqs] 解析响应失败: %s | raw: %s", e, str(data)[:300])
            return "解析 AI 响应失败，请查看日志。"

    async def _do_request(self, session, headers, payload):
        for attempt in range(2):
            async with session.post(_API_URL, headers=headers, json=payload) as resp:
                if resp.status == 401:
                    return "Gemini API Key 无效或已过期，请重新设置。"
                if resp.status == 429:
                    if attempt == 0:
                        retry_after = resp.headers.get("Retry-After", "")
                        wait = int(retry_after) if retry_after.isdigit() else 10
                        wait = min(wait, 30)
                        logger.info("[xqs] 触发速率限制，%ds 后重试...", wait)
                        await asyncio.sleep(wait)
                        continue
                    return "今日额度已耗尽，请明天再发疯吧。"
                if resp.status == 404:
                    return "模型不存在：%s，请用 #ai model <id> 更换。" % self._model
                if resp.status != 200:
                    return "API 错误（HTTP %d）" % resp.status
                return await resp.json(content_type=None)
        return "请求失败，请稍后再试。"
