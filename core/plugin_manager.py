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
                sys.modules[module_name] = mod
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
        prefix = self.config.get("CMD_PREFIX", "#")

        @self.client.on(events.NewMessage(outgoing=True))
        async def _help_handler(event):
            try:
                text = event.raw_text.strip()
                if not text.startswith(prefix):
                    return
                rest = text[len(prefix):]
                parts = rest.split(None, 1)
                if len(parts) != 2:
                    return
                name, subcmd = parts[0].lower(), parts[1].lower()
                if subcmd != "help":
                    return
                plugin = self._plugins.get(name)
                if not plugin:
                    return
                mod_name = "plugins." + name
                mod = sys.modules.get(mod_name)
                doc = (mod.__doc__ or "").strip() if mod else ""
                lines = ["<b>#" + html.escape(name) + "</b>"]
                if plugin.description:
                    lines.append(html.escape(plugin.description))
                if doc:
                    lines.append("")
                    lines.append(doc)
                text_out = "\n".join(lines)
                await event.delete()
                msg = await self.client.send_message(event.chat_id, text_out, parse_mode='html')
                async def _cleanup():
                    await asyncio.sleep(30)
                    try:
                        await msg.delete()
                    except Exception:
                        pass
                asyncio.create_task(_cleanup())
                logger.info("[plugin_manager] help handler: #%s help", name)
                raise events.StopPropagation
            except events.StopPropagation:
                raise
            except Exception as e:
                logger.warning("[plugin_manager] help handler error: %s", e)

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

