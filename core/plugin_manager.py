"""
插件管理器（Telethon 版）
自动扫描 plugins/ 目录，加载所有 BasePlugin 子类
"""
import asyncio
import html
import importlib.util
import inspect
import logging
import re
import sys
from pathlib import Path

from telethon import TelegramClient, events

from core.plugin_base import BasePlugin

logger = logging.getLogger(__name__)


class PluginManager:
    def __init__(self, client: TelegramClient, db, config: dict, plugin_dir: str = "plugins"):
        self.client     = client
        self.db         = db
        self.config     = config
        self.plugin_dir = Path(plugin_dir)
        self._plugins: dict[str, BasePlugin] = {}

    def load_all(self) -> list[str]:
        loaded = []
        for py_file in sorted(self.plugin_dir.glob("*.py")):
            if py_file.stem.startswith("_"):
                continue
            try:
                module_name = f"plugins.{py_file.stem}"
                spec = importlib.util.spec_from_file_location(module_name, py_file)
                mod  = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                for _, obj in inspect.getmembers(mod, inspect.isclass):
                    if (issubclass(obj, BasePlugin)
                            and obj is not BasePlugin
                            and obj.__module__ == module_name):
                        inst = obj(self.client, self.db, self.config)
                        if not inst.name:
                            continue
                        self._plugins[inst.name] = inst
                        loaded.append(inst.name)
                        logger.info("[plugin_manager] 已加载: %s", inst)
            except Exception as e:
                logger.exception("[plugin_manager] 加载 %s 失败: %s", py_file, e)
        return loaded

    def _register_help_handler(self):
        """注册 #<插件名> help 通用说明处理器。

        优先于各插件自身 handler 注册，捕获后 StopPropagation，
        避免插件自身的 handler 收到未知子命令时执行意外操作。
        """
        prefix = re.escape(self.config.get("CMD_PREFIX", "#"))
        names = sorted(self._plugins.keys(), key=len, reverse=True)
        if not names:
            return

        pattern = rf"(?i)^{prefix}({'|'.join(re.escape(n) for n in names)})\s+help$"

        @self.client.on(events.NewMessage(outgoing=True, pattern=pattern))
        async def _help_handler(event):
            name = event.pattern_match.group(1).lower()
            plugin = self._plugins.get(name)
            if not plugin:
                return

            mod = sys.modules.get(f"plugins.{name}")
            doc = (mod.__doc__ or "").strip() if mod else ""

            lines = [f"<b>#{html.escape(name)}</b>"]
            if plugin.description:
                lines.append(html.escape(plugin.description))
            if doc:
                lines.append("")
                lines.append(doc)

            text = "\n".join(lines)

            await event.delete()
            msg = await self.client.send_message(event.chat_id, text, parse_mode="html")
            async def _cleanup():
                await asyncio.sleep(30)
                try:
                    await msg.delete()
                except Exception:
                    pass
            asyncio.create_task(_cleanup())

            raise events.StopPropagation

    async def setup_all(self):
        self._register_help_handler()

        async def _safe_setup(p):
            try:
                await p.setup()
            except Exception as e:
                logger.exception("[plugin_manager] 插件 %s setup() 失败: %s", p.name, e)

        await asyncio.gather(*[_safe_setup(p) for p in self._plugins.values()])

    async def startup_all(self):
        async def _safe_startup(p):
            try:
                await p.on_startup()
            except Exception as e:
                logger.exception("[plugin_manager] 插件 %s on_startup() 失败: %s", p.name, e)

        await asyncio.gather(*[_safe_startup(p) for p in self._plugins.values()])

    async def shutdown_all(self):
        for p in self._plugins.values():
            try:
                await p.on_shutdown()
            except Exception as e:
                logger.exception("[plugin_manager] 插件 %s on_shutdown() 失败: %s", p.name, e)

    @property
    def plugins(self) -> dict[str, BasePlugin]:
        return dict(self._plugins)

