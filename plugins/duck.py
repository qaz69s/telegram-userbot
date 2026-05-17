"""
插件: duck —— 使用 DuckDuckGo 搜索并返回前 10 条结果

用法:
    #duck <关键词>      —— 搜索关键词
    回复某条消息 + #duck  —— 以被回复消息内容作为搜索词
"""

import asyncio
import html
import logging
import re
import urllib.error
import urllib.parse
import urllib.request

from telethon import events

from core.plugin_base import BasePlugin

logger = logging.getLogger(__name__)

_MAX_LEN = 3800
_HTTP_TIMEOUT = 20
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _fetch_text(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"网络错误: {e.reason}") from e


def _duck_search(query: str) -> list[dict]:
    params = urllib.parse.urlencode(
        {
            "q": query,
            "kl": "cn-zh",
        }
    )
    url = f"https://html.duckduckgo.com/html/?{params}"
    raw = _fetch_text(url)

    matches = re.findall(
        r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        raw,
        flags=re.IGNORECASE | re.DOTALL,
    )

    results = []
    seen = set()
    for link, title_html in matches:
        link = html.unescape(link)
        title = _strip_html(title_html)
        if not title:
            continue
        if link.startswith("//"):
            link = f"https:{link}"
        if link.startswith("/"):
            link = urllib.parse.urljoin("https://html.duckduckgo.com/", link)
        if link in seen:
            continue
        seen.add(link)
        results.append({"title": title, "url": link})
        if len(results) >= 10:
            break

    if not results:
        raise RuntimeError("未解析到 DuckDuckGo 搜索结果")
    return results


class DuckPlugin(BasePlugin):
    name = "duck"
    description = "#duck 使用 DuckDuckGo 搜索并返回前 10 条结果"
    version = "1.0.0"

    def __init__(self, client, db, config):
        super().__init__(client, db, config)

    async def on_startup(self):
        logger.info("[duck] 插件就绪")

    async def setup(self):
        prefix = re.escape(self.config.get("CMD_PREFIX", "#"))
        cmd_re = re.compile(rf"^{prefix}duck(?:\s+(.+))?$", re.IGNORECASE | re.DOTALL)

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

            arg = (m.group(1) or "").strip()

            query = arg
            if not query and event.is_reply:
                try:
                    replied = await event.get_reply_message()
                    if replied and (replied.raw_text or "").strip():
                        query = replied.raw_text.strip()
                except Exception as e:
                    logger.warning("[duck] 获取回复消息失败: %s", e)

            if not query:
                await self._tip(
                    event.chat_id,
                    "用法：\n"
                    "`#duck <关键词>` — 使用 DuckDuckGo 搜索\n"
                    "或直接回复某条消息后发送 `#duck`",
                    delay=10,
                )
                return

            await self._ask(event.chat_id, query)

    async def _ask(self, chat_id: int, query: str):
        tip = await self.client.send_message(chat_id, "DuckDuckGo 搜索中...")

        try:
            loop = asyncio.get_running_loop()
            results = await asyncio.wait_for(
                loop.run_in_executor(None, _duck_search, query),
                timeout=_HTTP_TIMEOUT + 5,
            )
            q_preview = query if len(query) <= 150 else query[:150] + "…"
            lines = [
                "**DuckDuckGo 搜索结果**\n",
                f"**关键词：** {q_preview}\n",
            ]
            for idx, item in enumerate(results, start=1):
                lines.append(f"{idx}. [{item['title']}]({item['url']})")

            content = "\n".join(lines)
            if len(content) > _MAX_LEN:
                content = content[:_MAX_LEN] + "…"

            await tip.edit(content, link_preview=False)
        except asyncio.TimeoutError:
            await tip.edit("DuckDuckGo 搜索超时，请稍后重试")
        except Exception as e:
            logger.exception("[duck] 搜索失败: %s", e)
            await tip.edit(f"搜索失败：{e}")

    async def _tip(self, chat_id: int, text: str, delay: int = 5):
        msg = await self.client.send_message(chat_id, text)
        await asyncio.sleep(delay)
        try:
            await msg.delete()
        except Exception:
            pass