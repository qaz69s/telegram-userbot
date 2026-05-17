#!/usr/bin/env python3
"""
scripts/auth.py —— 交互式 Telethon 登录管理
支持:
  python scripts/auth.py           首次登录 / 检查会话状态
  python scripts/auth.py --reauth  强制重新登录（保留旧 session 备份）
  python scripts/auth.py --switch  切换到另一个账号
  python scripts/auth.py --list    列出已保存的所有 session
  python scripts/auth.py --logout  退出当前账号（删除 session）
"""
import asyncio
import os
import sys
import shutil
import argparse
from datetime import datetime
from pathlib import Path

# 把项目根目录加入 PATH，以便 import dotenv
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv, set_key
from telethon import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    PasswordHashInvalidError,
    FloodWaitError,
)

load_dotenv(ROOT / ".env")

# ── ANSI 颜色 ────────────────────────────────────────────────
G  = "\033[32m";  R = "\033[31m"; Y = "\033[33m"
B  = "\033[34m";  C = "\033[36m"; W = "\033[1m";  X = "\033[0m"
OK   = f"{G}✔{X}"
ERR  = f"{R}✘{X}"
ASK  = f"{C}?{X}"
INFO = f"{B}ℹ{X}"


def env_path() -> Path:
    return ROOT / ".env"


def session_dir() -> Path:
    d = ROOT / os.getenv("SESSION_DIR", "sessions")
    d.mkdir(parents=True, exist_ok=True)
    return d


def session_file(phone: str) -> Path:
    """把手机号转成合法文件名"""
    safe = phone.replace("+", "").replace(" ", "")
    return session_dir() / safe


def get_credentials() -> tuple[int, str]:
    api_id   = os.getenv("API_ID", "").strip()
    api_hash = os.getenv("API_HASH", "").strip()

    if not api_id or not api_hash or api_id == "12345678":
        print(f"\n{W}需要 Telegram API 凭据{X}")
        print(f"{INFO} 前往 {C}https://my.telegram.org/apps{X} 创建应用获取\n")
        while True:
            api_id = input(f"  {ASK} API_ID  : ").strip()
            if api_id.isdigit():
                break
            print(f"  {ERR} API_ID 必须是纯数字")
        api_hash = input(f"  {ASK} API_HASH: ").strip()

        # 写入 .env
        env_file = env_path()
        if not env_file.exists():
            shutil.copy(ROOT / ".env.example", env_file)
        set_key(str(env_file), "API_ID",   api_id)
        set_key(str(env_file), "API_HASH", api_hash)
        print(f"  {OK} 已保存到 .env\n")

    return int(api_id), api_hash


async def do_login(api_id: int, api_hash: str, phone: str | None = None) -> tuple[TelegramClient, str]:
    """执行完整登录流程，返回 (client, phone)"""

    # ── 输入手机号 ───────────────────────────────────────────
    if not phone:
        print(f"\n{INFO} 手机号格式示例: +8613812345678")
        phone = input(f"  {ASK} 手机号: ").strip()

    sess = str(session_file(phone))
    client = TelegramClient(sess, api_id, api_hash)
    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"\n  {OK} Session 有效，当前账号: {W}{me.first_name}{X}"
              + (f" (@{me.username})" if me.username else ""))
        return client, phone

    # ── 发送验证码 ────────────────────────────────────────────
    print(f"\n  {INFO} 正在向 {phone} 发送验证码...")
    try:
        await client.send_code_request(phone)
    except FloodWaitError as e:
        print(f"\n  {ERR} 请求过于频繁，需等待 {e.seconds} 秒后重试")
        await client.disconnect()
        sys.exit(1)

    # ── 输入验证码（最多重试 3 次） ───────────────────────────
    for attempt in range(3):
        code = input(f"  {ASK} 验证码 (Telegram 消息): ").strip()
        try:
            await client.sign_in(phone, code)
            break
        except PhoneCodeInvalidError:
            left = 2 - attempt
            if left:
                print(f"  {ERR} 验证码错误，还可重试 {left} 次")
            else:
                print(f"  {ERR} 验证码已用尽，请重新运行脚本")
                await client.disconnect()
                sys.exit(1)
        except PhoneCodeExpiredError:
            print(f"  {ERR} 验证码已过期，请重新运行脚本")
            await client.disconnect()
            sys.exit(1)
        except SessionPasswordNeededError:
            # ── 2FA 密码 ────────────────────────────────────
            await _handle_2fa(client)
            break

    # ── 验证成功 ─────────────────────────────────────────────
    if not await client.is_user_authorized():
        print(f"  {ERR} 登录失败，请重试")
        await client.disconnect()
        sys.exit(1)

    me = await client.get_me()
    print(f"\n  {OK} 登录成功！欢迎, {W}{me.first_name}{X}"
          + (f" (@{me.username})" if me.username else "")
          + f"  ID: {me.id}")
    return client, phone


async def _handle_2fa(client: TelegramClient):
    """处理两步验证密码"""
    print(f"\n  {INFO} 该账号已开启两步验证 (2FA)")
    for attempt in range(3):
        import getpass
        pwd = getpass.getpass(f"  {ASK} 2FA 密码: ")
        try:
            await client.sign_in(password=pwd)
            print(f"  {OK} 2FA 验证通过")
            return
        except PasswordHashInvalidError:
            left = 2 - attempt
            if left:
                print(f"  {ERR} 密码错误，还可重试 {left} 次")
            else:
                print(f"  {ERR} 密码错误次数过多，请重新运行")
                await client.disconnect()
                sys.exit(1)


def add_phone_to_env(phone: str):
    """将手机号追加到 PHONES 列表（不重复），并迁移旧 PHONE 字段"""
    env_file = env_path()
    if not env_file.exists():
        shutil.copy(ROOT / ".env.example", env_file)

    # 先加载（确保读到最新值）
    load_dotenv(env_file, override=True)

    phones_str = os.getenv("PHONES", "").strip()
    phone_list = [p.strip() for p in phones_str.split(",") if p.strip()] if phones_str else []

    # 迁移旧 PHONE 字段
    old_phone = os.getenv("PHONE", "").strip()
    if old_phone and old_phone not in phone_list:
        phone_list.append(old_phone)

    if phone not in phone_list:
        phone_list.append(phone)

    set_key(str(env_file), "PHONES", ",".join(phone_list))
    set_key(str(env_file), "PHONE", "")  # 清除旧字段
    load_dotenv(env_file, override=True)


def remove_phone_from_env(phone: str):
    """从 PHONES 列表中移除指定手机号"""
    env_file = env_path()
    load_dotenv(env_file, override=True)

    phones_str = os.getenv("PHONES", "").strip()
    phone_list = [p.strip() for p in phones_str.split(",") if p.strip()] if phones_str else []
    phone_list = [p for p in phone_list if p != phone]

    set_key(str(env_file), "PHONES", ",".join(phone_list))
    load_dotenv(env_file, override=True)


# ── 子命令实现 ────────────────────────────────────────────────

async def cmd_login(args):
    """首次登录 / 检查 session"""
    print(f"\n{W}═══ Telegram 账号登录 ═══{X}")
    api_id, api_hash = get_credentials()

    # 读取已有账号列表，若只有一个则作为默认提示
    phones_str = os.getenv("PHONES", "").strip()
    existing = [p.strip() for p in phones_str.split(",") if p.strip()] if phones_str else []
    phone = existing[0] if len(existing) == 1 else (os.getenv("PHONE", "").strip() or None)
    client, phone = await do_login(api_id, api_hash, phone)
    add_phone_to_env(phone)
    await client.disconnect()
    print(f"\n  {OK} 配置已保存，运行 {C}python main.py{X} 启动 Userbot\n")


async def cmd_reauth(args):
    """强制重新登录，备份旧 session"""
    print(f"\n{W}═══ 重新登录 ═══{X}")
    api_id, api_hash = get_credentials()

    # 列出 PHONES 中的账号供选择
    phones_str = os.getenv("PHONES", "").strip()
    phone_list = [p.strip() for p in phones_str.split(",") if p.strip()] if phones_str else []
    if not phone_list:
        old = os.getenv("PHONE", "").strip()
        phone_list = [old] if old else []

    phone = ""
    if phone_list:
        print(f"\n  {INFO} 当前账号列表:")
        for i, p in enumerate(phone_list, 1):
            print(f"    {C}{i}.{X} {p}")
        choice = input(f"  {ASK} 选择要重新登录的编号（回车选第一个）: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(phone_list):
            phone = phone_list[int(choice) - 1]
        else:
            phone = phone_list[0]

    if not phone:
        phone = input(f"  {ASK} 手机号: ").strip()

    sess = session_file(phone)
    if sess.with_suffix(".session").exists():
        backup = sess.with_suffix(f".session.bak-{datetime.now():%Y%m%d%H%M%S}")
        shutil.copy(str(sess.with_suffix(".session")), str(backup))
        sess.with_suffix(".session").unlink()
        print(f"  {INFO} 旧 session 已备份: {backup.name}")

    client, phone = await do_login(api_id, api_hash, phone)
    add_phone_to_env(phone)
    await client.disconnect()
    print(f"\n  {OK} 重新登录完成\n")


async def cmd_switch(args):
    """添加新账号 / 激活已保存的 session"""
    print(f"\n{W}═══ 添加 / 切换账号 ═══{X}")

    # 读取当前 PHONES 列表
    load_dotenv(ROOT / ".env", override=True)
    phones_str = os.getenv("PHONES", "").strip()
    active_list = [p.strip() for p in phones_str.split(",") if p.strip()] if phones_str else []

    # 列出已有 session 文件
    sessions = list(session_dir().glob("*.session"))
    if sessions:
        print(f"\n  {INFO} 已保存的 session:")
        for i, s in enumerate(sessions, 1):
            phone_str = "+" + s.stem
            running = f" {G}[已启用]{X}" if phone_str in active_list else f" {Y}[未启用]{X}"
            print(f"    {C}{i}.{X} {phone_str}{running}")
        print(f"    {C}n.{X} 登录全新账号\n")
        choice = input(f"  {ASK} 选择编号或 n: ").strip().lower()
        if choice == "n":
            phone = None
        elif choice.isdigit() and 1 <= int(choice) <= len(sessions):
            phone = "+" + sessions[int(choice) - 1].stem
        else:
            print(f"  {ERR} 无效选项"); return
    else:
        phone = None

    api_id, api_hash = get_credentials()
    client, phone = await do_login(api_id, api_hash, phone)
    add_phone_to_env(phone)
    await client.disconnect()
    print(f"\n  {OK} 账号 {phone} 已添加到 PHONES 列表\n")


async def cmd_list(args):
    """列出所有已保存的 session"""
    print(f"\n{W}═══ 已保存的账号 ═══{X}\n")
    sessions = list(session_dir().glob("*.session"))
    if not sessions:
        print(f"  {INFO} 暂无保存的 session\n")
        return

    api_id, api_hash = get_credentials()
    load_dotenv(ROOT / ".env", override=True)
    phones_str = os.getenv("PHONES", "").strip()
    active_set = {p.strip().replace("+", "") for p in phones_str.split(",") if p.strip()} if phones_str else set()
    # 兼容旧 PHONE
    old = os.getenv("PHONE", "").strip().replace("+", "")
    if old:
        active_set.add(old)

    for s in sessions:
        phone_str = "+" + s.stem
        mark = f" {G}[已启用]{X}" if s.stem in active_set else ""
        size = s.stat().st_size
        mtime = datetime.fromtimestamp(s.stat().st_mtime).strftime("%Y-%m-%d %H:%M")

        # 尝试读取账号名
        try:
            client = TelegramClient(str(s.with_suffix("")), api_id, api_hash)
            await client.connect()
            if await client.is_user_authorized():
                me = await client.get_me()
                name = f"{me.first_name}" + (f" @{me.username}" if me.username else "")
            else:
                name = f"{R}session 已失效{X}"
            await client.disconnect()
        except Exception:
            name = f"{Y}无法读取{X}"

        print(f"  {C}•{X} {W}{phone_str}{X}{mark}")
        print(f"      {name}  |  {size}B  |  {mtime}")
    print()


async def cmd_logout(args):
    """退出指定账号并删除 session"""
    # 列出 PHONES 中的账号
    load_dotenv(ROOT / ".env", override=True)
    phones_str = os.getenv("PHONES", "").strip()
    phone_list = [p.strip() for p in phones_str.split(",") if p.strip()] if phones_str else []
    if not phone_list:
        old = os.getenv("PHONE", "").strip()
        phone_list = [old] if old else []

    phone = ""
    if phone_list:
        print(f"\n  {INFO} 当前启用的账号:")
        for i, p in enumerate(phone_list, 1):
            print(f"    {C}{i}.{X} {p}")
        choice = input(f"  {ASK} 选择要退出的编号: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(phone_list):
            phone = phone_list[int(choice) - 1]
        else:
            print(f"  {ERR} 无效选项"); return
    if not phone:
        phone = input(f"  {ASK} 要退出的手机号: ").strip()

    print(f"\n{W}═══ 退出账号: {phone} ═══{X}")
    confirm = input(f"  {ASK} 确认退出并删除 session？[y/N] ").strip().lower()
    if confirm != "y":
        print(f"  {INFO} 已取消\n"); return

    api_id, api_hash = get_credentials()
    sess = session_file(phone)
    client = TelegramClient(str(sess), api_id, api_hash)
    try:
        await client.connect()
        await client.log_out()
    except Exception:
        pass
    finally:
        await client.disconnect()

    for ext in [".session", ".session-journal"]:
        f = sess.with_suffix(ext)
        if f.exists():
            f.unlink()

    # 从 PHONES 列表中移除
    remove_phone_from_env(phone)
    print(f"  {OK} 已退出并清除 session\n")


# ── 入口 ─────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        prog="auth.py",
        description="Telegram Userbot 账号管理",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
命令:
  (无参数)   首次登录 / 检查当前 session
  --reauth   强制重新登录（备份旧 session）
  --switch   切换账号
  --list     列出所有已保存账号
  --logout   退出当前账号
        """,
    )
    parser.add_argument("--reauth",  action="store_true", help="强制重新登录")
    parser.add_argument("--switch",  action="store_true", help="切换账号")
    parser.add_argument("--list",    action="store_true", help="列出账号")
    parser.add_argument("--logout",  action="store_true", help="退出账号")
    args = parser.parse_args()

    if args.reauth:
        asyncio.run(cmd_reauth(args))
    elif args.switch:
        asyncio.run(cmd_switch(args))
    elif args.list:
        asyncio.run(cmd_list(args))
    elif args.logout:
        asyncio.run(cmd_logout(args))
    else:
        asyncio.run(cmd_login(args))


if __name__ == "__main__":
    main()
