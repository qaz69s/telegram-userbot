"""
插件: help —— 显示所有已安装插件的帮助信息

用法:
  #help     —— 列出全部插件名称与描述
"""
import asyncio
import importlib.util
import inspect
import logging
import re
from pathlib import Path

from telethon import events

from core.plugin_base import BasePlugin

logger = logging.getLogger(__name__)


class HelpPlugin(BasePlugin):
    name        = "help"
    description = "#help 显示所有已安装插件的帮助信息"
    version     = "1.0.0"

    async def on_startup(self):
        logger.info("[help] 插件就绪")

    async def setup(self):
        prefix = re.escape(self.config.get("CMD_PREFIX", "#"))
        actual_prefix = self.config.get("CMD_PREFIX", "#")

        @self.client.on(events.NewMessage(
            outgoing=True,
            pattern=rf"(?i)^{prefix}help$",
        ))
        async def cmd_handler(event):
            await event.delete()

            plugins = self._scan_plugins()
            if not plugins:
                tip = await self.client.send_message(event.chat_id, "未找到任何插件。")
                await asyncio.sleep(4)
                try:
                    await tip.delete()
                except Exception:
                    pass
                return

            total = len(plugins)
            lines = [f"**插件列表**  _{total} 个已安装_\n"]
            for name, desc, ver in plugins:
                m = re.search(r"#[A-Za-z0-9_]+", desc)
                if m:
                    # 把描述里的 # 前缀替换成用户配置的实际前缀
                    cmd  = actual_prefix + m.group(0)[1:]
                    rest = (desc[:m.start()] + desc[m.end():]).strip().strip("，,、 ")
                    lines.append(f"● {name}  `{cmd}`  {rest}")
                else:
                    lines.append(f"● {name}  {desc}")

            msg = await self.client.send_message(
                event.chat_id,
                "\n".join(lines),
                parse_mode="md",
            )
            await asyncio.sleep(15)
            try:
                await msg.delete()
            except Exception:
                pass

    def _scan_plugins(self) -> list[tuple[str, str, str]]:
        """扫描 plugins/ 目录，返回 [(name, description, version), ...]。"""
        plugin_dir = Path(self.config.get("PLUGIN_DIR", "plugins"))
        result = []

        for py_file in sorted(plugin_dir.glob("*.py")):
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
                            and obj.__module__ == module_name
                            and obj.name):
                        result.append((obj.name, obj.description or "—", obj.version))
            except Exception as e:
                logger.debug("[help] 扫描 %s 失败: %s", py_file.name, e)

        return result
