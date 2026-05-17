"""
插件基类（Telethon 版）
"""
from abc import ABC, abstractmethod
from telethon import TelegramClient


class BasePlugin(ABC):
    name:        str  = ""
    description: str  = ""
    version:     str  = "1.0.0"

    def __init__(self, client: TelegramClient, db, config: dict):
        self.client = client
        self.db     = db
        self.config = config

    @abstractmethod
    async def setup(self) -> None:
        """在 self.client 上注册 Telethon 事件处理器"""
        ...

    async def on_startup(self) -> None:
        """Bot 启动后回调（可选，用于建表等）"""
        pass

    async def on_shutdown(self) -> None:
        """Bot 关闭前回调（可选）"""
        pass

    def __repr__(self) -> str:
        return f"<Plugin:{self.name} v{self.version}>"
