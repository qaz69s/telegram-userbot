"""
插件管理器（Telethon 版）
自动扫描 plugins/ 目录，加载所有 BasePlugin 子类
"""
import asyncio
import importlib.util
import inspect
import logging
from pathlib import Path
from telethon import TelegramClient
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

    async def setup_all(self):
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
