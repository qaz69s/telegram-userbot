"""
main.py —— Telethon Userbot 主入口
"""
import asyncio
import logging
import logging.handlers
import os
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient

from core import Database, PluginManager

# ── 日志 ─────────────────────────────────────────────────────
os.makedirs("data", exist_ok=True)
_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
_fh = logging.handlers.RotatingFileHandler(
    "data/bot.log", maxBytes=3 * 1024 * 1024, backupCount=1, encoding="utf-8"
)
_fh.setFormatter(_fmt)
_sh = logging.StreamHandler(sys.stdout)
_sh.setFormatter(_fmt)
logging.basicConfig(level=logging.INFO, handlers=[_sh, _fh])
logging.getLogger("telethon.network.mtprotostate").setLevel(logging.ERROR)
logger = logging.getLogger(__name__)

load_dotenv()

CONNECT_TIMEOUT = 20  # connect() 超时秒数


def load_config() -> dict:
    raw_id = os.getenv("API_ID", "").strip()
    api_hash = os.getenv("API_HASH", "").strip()

    if not raw_id or not api_hash:
        logger.error("请先运行 python scripts/auth.py 完成登录")
        sys.exit(1)

    try:
        api_id = int(raw_id)
    except ValueError:
        logger.error("API_ID 必须为纯数字，当前值: %s", raw_id)
        sys.exit(1)

    # 多账号：优先读 PHONES（逗号分隔），回退旧字段 PHONE
    phones_raw = os.getenv("PHONES", "").strip()
    if phones_raw:
        phones = [p.strip() for p in phones_raw.split(",") if p.strip()]
    else:
        single = os.getenv("PHONE", "").strip()
        phones = [single] if single else []

    if not phones:
        logger.error(".env 中未找到账号，请运行: python scripts/auth.py")
        sys.exit(1)

    return {
        "API_ID":      api_id,
        "API_HASH":    api_hash,
        "PHONES":      phones,
        "DB_PATH":     os.getenv("DB_PATH", "data/bot.db"),
        "SESSION_DIR": os.getenv("SESSION_DIR", "sessions"),
        "PLUGIN_DIR":  "plugins",
        "CMD_PREFIX":  os.getenv("CMD_PREFIX", "#"),
    }


def get_session_path(config: dict, phone: str) -> str:
    safe = phone.replace("+", "").replace(" ", "")
    return str(Path(config["SESSION_DIR"]) / safe)


def _wipe_update_state(session_path: str):
    """清除 Telethon session 中的 update_state，避免旧消息洪泛导致 connect 卡死"""
    try:
        if not os.path.exists(session_path):
            return
        conn = sqlite3.connect(session_path)
        cur = conn.cursor()
        # 检查表是否存在（Telethon 1.36+ 才有）
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='update_state'")
        if cur.fetchone():
            cur.execute("DELETE FROM update_state")
            cur.execute("DELETE FROM sent_files")
            conn.commit()
            logger.info("[session] 已清除 update_state")
        conn.close()
    except Exception as e:
        logger.warning("[session] 清除 update_state 失败: %s", e)


async def _safe_connect(client: TelegramClient, session_path: str) -> bool:
    """带超时和自动修复 session 的 connect()"""
    try:
        await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
        return True
    except asyncio.TimeoutError:
        logger.warning("connect() 超时，清除 update_state 后重试...")
        _wipe_update_state(session_path)
        await client.disconnect()
        try:
            await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
            return True
        except asyncio.TimeoutError:
            logger.error("connect() 重试仍然超时，session 已完全失效，请重新登录")
            return False


async def run_account(config: dict, db, phone: str):
    """启动单个账号的完整生命周期"""
    tag = phone
    session_path = get_session_path(config, phone)
    client = TelegramClient(session_path, config["API_ID"], config["API_HASH"])

    if not await _safe_connect(client, session_path):
        logger.error("[%s] 无法连接 Telegram，跳过此账号", tag)
        await client.disconnect()
        return

    if not await client.is_user_authorized():
        logger.error("[%s] Session 无效，请运行: python scripts/auth.py --reauth", tag)
        await client.disconnect()
        return

    me = await client.get_me()
    uname = f"@{me.username}" if me.username else f"ID:{me.id}"
    logger.info("[%s] 已连接: %s (%s)", tag, me.first_name, uname)

    # ── 每个账号独立的插件管理器 ────────────────────────────
    pm = PluginManager(client, db, config, plugin_dir=config["PLUGIN_DIR"])
    loaded = pm.load_all()
    logger.info("[%s] 已加载 %d 个插件: %s", tag, len(loaded), loaded)

    if not loaded:
        logger.warning("[%s] 没有加载任何插件，请检查 plugins/ 目录", tag)

    await pm.startup_all()
    await pm.setup_all()

    logger.info("[%s] 开始监听消息...", tag)
    try:
        await client.run_until_disconnected()
    finally:
        await pm.shutdown_all()
        await client.disconnect()
        logger.info("[%s] 已关闭", tag)


async def main():
    config = load_config()
    phones = config["PHONES"]
    logger.info("共加载 %d 个账号: %s", len(phones), phones)

    db = Database(config["DB_PATH"])
    await db.connect()

    try:
        results = await asyncio.gather(
            *[run_account(config, db, phone) for phone in phones],
            return_exceptions=True,
        )
        for phone, result in zip(phones, results):
            if isinstance(result, Exception):
                logger.error("[%s] 账号异常退出: %s", phone, result)
    finally:
        await db.close()
        logger.info("Userbot 已关闭")


if __name__ == "__main__":
    asyncio.run(main())
