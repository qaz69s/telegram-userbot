"""
插件: mjx —— 发送随机买家秀图片（可按关键词筛选）

用法:
    #mjx                —— 发送3张随机买家秀
    #mjx add 关键词     —— 添加过滤关键词
    #mjx del 关键词     —— 删除过滤关键词
    #mjx list           —— 查看所有关键词
    #mjx clear          —— 清空关键词
"""
import asyncio
import json
import logging
import re
import urllib.request
import urllib.error
from telethon import events
from core.plugin_base import BasePlugin

logger = logging.getLogger(__name__)

API_URL = "https://mjx.t-o.workers.dev/"
_DB_NAMESPACE = "mjx_config"
_DB_KEY_KEYWORDS = "filter_keywords"


def _fetch_mjx_data_sync() -> dict:
    """同步方式从 API 获取单条买家秀数据（在 executor 中运行）"""
    try:
        req = urllib.request.Request(
            API_URL,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data
    except urllib.error.URLError as e:
        logger.warning(f"API URLError: {e}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to fetch MJX data: {e}")
        return None


class MjxPlugin(BasePlugin):
    name = "mjx"
    description = "买家秀图片展示 - 发送随机买家秀图片及评价（支持关键词筛选）"
    version = "1.1.0"

    async def setup(self) -> None:
        """注册 Telethon 事件处理器"""
        prefix = re.escape(self.config.get("CMD_PREFIX", "#"))

        @self.client.on(events.NewMessage(outgoing=True, pattern=rf"(?i)^{prefix}mjx"))
        async def mjx_handler(event):
            await event.delete()
            args = event.text.strip().split(maxsplit=2)
            
            if len(args) == 1:
                # #mjx 发送图片
                await self.handle_mjx_command(event)
            elif len(args) >= 2:
                cmd = args[1].lower()
                if cmd == "add" and len(args) == 3:
                    await self.handle_add_keyword(event, args[2])
                elif cmd == "del" and len(args) == 3:
                    await self.handle_del_keyword(event, args[2])
                elif cmd == "list":
                    await self.handle_list_keywords(event)
                elif cmd == "clear":
                    await self.handle_clear_keywords(event)
                else:
                    await event.respond("❌ 用法错误。\n#mjx - 发送图片\n#mjx add 关键词 - 添加过滤词\n#mjx list - 查看关键词")

    async def fetch_mjx_data(self) -> dict:
        """从 API 获取单条买家秀数据（异步包装）"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _fetch_mjx_data_sync)

    async def _get_keywords(self) -> list:
        """从数据库获取过滤关键词列表"""
        try:
            data = await self.db.kv_get(_DB_NAMESPACE, _DB_KEY_KEYWORDS)
            return data or []
        except Exception:
            return []

    async def _set_keywords(self, keywords: list) -> None:
        """保存过滤关键词到数据库"""
        await self.db.kv_set(_DB_NAMESPACE, _DB_KEY_KEYWORDS, keywords)

    def _match_keywords(self, text: str, keywords: list) -> bool:
        """检查文本是否包含任何关键词"""
        if not keywords:
            return True
        return any(kw in text for kw in keywords)

    async def handle_add_keyword(self, event, keyword: str):
        """处理 #mjx add 命令"""
        try:
            keywords = await self._get_keywords()
            if keyword in keywords:
                await event.respond(f"[Warning] 关键词 '{keyword}' 已存在")
                return
            
            keywords.append(keyword)
            await self._set_keywords(keywords)
            await event.respond(f"[OK] 已添加关键词 '{keyword}'\n当前过滤词: {', '.join(keywords)}")
        except Exception as e:
            logger.error(f"Failed to add keyword: {e}")
            await event.respond(f"[Error] 添加失败: {e}")

    async def handle_del_keyword(self, event, keyword: str):
        """处理 #mjx del 命令"""
        try:
            keywords = await self._get_keywords()
            if keyword not in keywords:
                await event.respond(f"[Warning] 关键词 '{keyword}' 不存在")
                return
            
            keywords.remove(keyword)
            await self._set_keywords(keywords)
            if keywords:
                await event.respond(f"[OK] 已删除关键词 '{keyword}'\n当前过滤词: {', '.join(keywords)}")
            else:
                await event.respond(f"[OK] 已删除关键词 '{keyword}'\n[Info] 现在将发送所有买家秀")
        except Exception as e:
            logger.error(f"Failed to delete keyword: {e}")
            await event.respond(f"[Error] 删除失败: {e}")

    async def handle_list_keywords(self, event):
        """处理 #mjx list 命令"""
        try:
            keywords = await self._get_keywords()
            if keywords:
                kw_text = "\n".join(f"  - {kw}" for kw in keywords)
                await event.respond(f"当前过滤关键词:\n{kw_text}")
            else:
                await event.respond("未设置过滤关键词\n使用 #mjx add 关键词 来添加")
        except Exception as e:
            logger.error(f"Failed to list keywords: {e}")
            await event.respond(f"[Error] 获取失败: {e}")

    async def handle_clear_keywords(self, event):
        """处理 #mjx clear 命令"""
        try:
            await self._set_keywords([])
            await event.respond("[OK] 已清空所有过滤关键词\n[Info] 现在将发送所有买家秀")
        except Exception as e:
            logger.error(f"Failed to clear keywords: {e}")
            await event.respond(f"[Error] 清空失败: {e}")

    async def handle_mjx_command(self, event):
        """处理 #mjx 命令（发送图片）"""
        try:
            keywords = await self._get_keywords()
            
            # 发送提示消息
            hint_msg = await event.respond("[Loading] 正在加载买家秀图片，请稍候...")

            mjx_items = []
            failed_count = 0
            attempts = 0
            max_attempts = 15  # 最多尝试 15 次请求，确保获得 3 张

            # 逐个获取，直到得到 3 张符合条件的图片
            while len(mjx_items) < 3 and attempts < max_attempts:
                attempts += 1
                result = await self.fetch_mjx_data()
                
                if isinstance(result, dict) and "url" in result and "des" in result:
                    # 检查是否匹配过滤关键词
                    if self._match_keywords(result["des"], keywords):
                        mjx_items.append(result)
                else:
                    failed_count += 1

            if not mjx_items:
                await hint_msg.delete()
                msg = "[Error] 无法获取买家秀数据"
                if keywords:
                    msg += "\n[Info] 未找到符合条件的内容"
                await event.respond(msg)
                return

            # 删除提示消息
            await hint_msg.delete()

            # 发送 3 张图片
            for idx, item in enumerate(mjx_items, 1):
                try:
                    # 拼接消息：序号 + 评价
                    caption = f"第 {idx}/{len(mjx_items)} 张\n\n评价: {item['des']}"

                    # 发送图片
                    await event.respond(
                        file=item["url"],
                        message=caption,
                    )
                    # 发送间隔
                    if idx < len(mjx_items):
                        await asyncio.sleep(0.2)
                except Exception as e:
                    logger.error(f"Failed to send MJX image {idx}: {e}")
                    await event.respond(f"[Error] 发送第 {idx} 张图片失败")

            if failed_count > 0:
                await event.respond(
                    f"[Info] 成功加载 {len(mjx_items)} 张图片（失败 {failed_count} 张）"
                )

        except Exception as e:
            logger.error(f"MJX command error: {e}")
            await event.respond(f"[Error] 执行出错: {str(e)[:100]}")
