"""
插件: speedtest —— 测试网络速度（Ookla 官方 CLI）

安装官方 CLI（Debian/Ubuntu）：
  curl -s https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh | sudo bash
  sudo apt-get install speedtest

用法:
  #speedtest           —— 自动选最佳服务器测速
  #speedtest l         —— 列出附近可用服务器（含 ID）
  #speedtest <ID>      —— 指定服务器 ID 测速
"""

import asyncio
import json
import logging
import re
import shutil
import subprocess
import urllib.request
from functools import partial

from telethon import events

from core.plugin_base import BasePlugin

logger = logging.getLogger(__name__)

_BIN = "speedtest"  # Ookla 官方 CLI 二进制名称
# 首次运行需接受许可，加上这两个参数静默同意
_BASE_FLAGS = ["--accept-license", "--accept-gdpr", "--format=json"]


def _check_binary() -> bool:
    return shutil.which(_BIN) is not None


def _do_install_binary():
    """内在 executor 中运行：自动安装 Ookla speedtest CLI"""
    import os
    if not shutil.which("apt-get"):
        raise RuntimeError("当前系统不支持 apt-get，请手动安装: https://www.speedtest.net/apps/cli")

    # 下载仓库配置脚本（避免 curl | bash）
    script_path = "/tmp/_ookla_speedtest_setup.sh"
    urllib.request.urlretrieve(
        "https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh",
        script_path,
    )
    os.chmod(script_path, 0o755)

    # 添加软件源
    subprocess.run(["bash", script_path], check=True, capture_output=True)

    # 安装二进制
    subprocess.run(["apt-get", "install", "-y", "speedtest"], check=True, capture_output=True)


def _fmt_speed(bytes_per_sec: float) -> str:
    """bytes/s → 人类可读（Mbps / Gbps）"""
    mbps = bytes_per_sec * 8 / 1_000_000
    if mbps >= 1000:
        return f"{mbps / 1000:.2f} Gbps"
    return f"{mbps:.2f} Mbps"


def _fmt_ping(ms: float) -> str:
    return f"{ms:.2f} ms"


# ── 同步工作函数（在 executor 中运行）────────────────────────────

def _do_list_servers() -> list[dict]:
    """获取附近服务器列表（同步）"""
    result = subprocess.run(
        [_BIN, "-L"] + _BASE_FLAGS,
        capture_output=True, text=True, timeout=30,
    )
    raw = result.stdout.strip()

    if raw:
        json_start = raw.find("{")
        if json_start > 0:
            raw = raw[json_start:]

    if not raw:
        raise RuntimeError(result.stderr.strip() or "speedtest 命令未返回数据")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("[speedtest] 服务器列表 JSON 解析失败，原始输出: %r", result.stdout)
        raise RuntimeError(f"输出解析失败（{e}）") from e
    # 官方 CLI 返回格式：{"servers": [...]} 或直接为数组
    servers = data if isinstance(data, list) else data.get("servers", [])
    out = []
    for srv in servers[:15]:
        out.append({
            "id":       srv.get("id", ""),
            "name":     srv.get("name", ""),
            "location": srv.get("location", ""),
            "country":  srv.get("country", ""),
            "host":     srv.get("host", ""),
        })
    return out


def _do_speedtest(server_id: int | None) -> dict:
    """执行测速（同步），返回解析后的结果字典"""
    cmd = [_BIN] + _BASE_FLAGS
    if server_id:
        cmd += ["-s", str(server_id)]

    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=120,
    )
    raw = result.stdout.strip()

    # Ookla CLI 有时会在 JSON 前输出 License 提示行，需提取纯 JSON 部分
    if raw:
        # 找到第一个 '{' 开始的位置
        json_start = raw.find("{")
        if json_start > 0:
            raw = raw[json_start:]

    if not raw:
        stderr = result.stderr.strip()
        logger.error("[speedtest] 命令输出为空，stderr: %s", stderr)
        raise RuntimeError(stderr or "speedtest 未返回任何数据")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("[speedtest] JSON 解析失败，原始输出: %r，stderr: %r", result.stdout, result.stderr)
        raise RuntimeError(f"输出解析失败（{e}），请检查 speedtest 版本是否支持 --format=json") from e

    # 官方 CLI 用 type=log 行报告错误
    if data.get("type") == "log" or "error" in data:
        err_msg = data.get("message") or data.get("error", "未知错误")
        raise RuntimeError(f"speedtest 报错：{err_msg}")

    ping_info = data.get("ping", {})
    dl = data.get("download", {}).get("bandwidth", 0)   # bytes/s
    ul = data.get("upload", {}).get("bandwidth", 0)     # bytes/s
    srv = data.get("server", {})

    return {
        "download":   dl,
        "upload":     ul,
        "ping":       ping_info.get("latency", 0),
        "jitter":     ping_info.get("jitter", 0),
        "isp":        data.get("isp", ""),
        "result_url": data.get("result", {}).get("url", ""),
        "server": {
            "id":       srv.get("id", ""),
            "name":     srv.get("name", ""),
            "location": srv.get("location", ""),
            "country":  srv.get("country", ""),
            "host":     srv.get("host", ""),
        },
    }


class SpeedtestPlugin(BasePlugin):
    name        = "speedtest"
    description = "#speedtest 使用 Ookla 官方 CLI 测试网络速度"
    version     = "2.0.0"

    async def on_startup(self):
        if not _check_binary():
            logger.warning("[speedtest] 未找到 speedtest CLI，首次使用 #speedtest 时将自动安装")
        else:
            logger.info("[speedtest] 插件就绪（Ookla 官方 CLI）")

    async def _ensure_binary(self, chat_id: int, tip_msg) -> bool:
        """检查二进制是否存在，不存在则自动安装。返回 True 表示可用。"""
        if _check_binary():
            return True

        if not shutil.which("apt-get"):
            await tip_msg.edit(
                "未找到 `speedtest` 命令，且无法自动安装\n"
                "需要 apt-get\n"
                "请手动安装: https://www.speedtest.net/apps/cli"
            )
            return False

        await tip_msg.edit("正在安装 speedtest CLI...")
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _do_install_binary)
        except Exception as e:
            logger.error("[speedtest] 自动安装失败: %s", e)
            await tip_msg.edit(
                f"自动安装失败：{e}\n"
                "请手动安装: https://www.speedtest.net/apps/cli"
            )
            return False

        if not _check_binary():
            await tip_msg.edit("安装后仍未找到 `speedtest`，请手动检查")
            return False

        logger.info("[speedtest] Ookla speedtest CLI 自动安装完成")
        return True

    async def setup(self):
        prefix = re.escape(self.config.get("CMD_PREFIX", "#"))
        cmd_re = re.compile(rf"^{prefix}speedtest(?:\s+(.*))?$", re.IGNORECASE)

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

            raw_args = (m.group(1) or "").strip().lower()
            parts = raw_args.split()

            if not parts:
                await self._cmd_run(event.chat_id, server_id=None)

            elif parts[0] == "l":
                await self._cmd_list(event.chat_id)

            elif parts[0].isdigit():
                await self._cmd_run(event.chat_id, server_id=int(parts[0]))

            else:
                await self._tip(
                    event.chat_id,
                    "用法：\n"
                    "`#speedtest` — 自动选服务器测速\n"
                    "`#speedtest l` — 列出附近可用服务器\n"
                    "`#speedtest <ID>` — 指定服务器 ID 测速",
                    delay=8,
                )

    # ── 子命令 ────────────────────────────────────────────────

    async def _cmd_list(self, chat_id: int):
        tip = await self.client.send_message(chat_id, "获取服务器列表中...")
        if not await self._ensure_binary(chat_id, tip):
            return
        try:
            loop = asyncio.get_running_loop()
            servers = await loop.run_in_executor(None, _do_list_servers)
        except Exception as e:
            logger.error("[speedtest] 获取服务器列表失败: %s", e)
            await tip.edit(f"获取失败：{e}")
            await asyncio.sleep(5)
            try:
                await tip.delete()
            except Exception:
                pass
            return

        if not servers:
            await tip.edit("未获取到服务器列表")
            return

        lines = ["**附近可用服务器（最近 15 个）**\n"]
        for srv in servers:
            lines.append(
                f"• `{srv['id']}` **{srv['name']}** — {srv['location']}, {srv['country']}"
            )
        lines.append("\n使用 `#speedtest <ID>` 指定服务器测速")
        await tip.edit("\n".join(lines))

    async def _cmd_run(self, chat_id: int, server_id: int | None):
        srv_str = f"服务器 `{server_id}`" if server_id else "自动选择服务器"
        tip = await self.client.send_message(chat_id, f"测速中（{srv_str}）...")
        if not await self._ensure_binary(chat_id, tip):
            return

        try:
            loop = asyncio.get_running_loop()
            res = await loop.run_in_executor(None, partial(_do_speedtest, server_id))
        except Exception as e:
            logger.error("[speedtest] 测速失败: %s", e)
            await tip.edit(f"测速失败：{e}")
            await asyncio.sleep(5)
            try:
                await tip.delete()
            except Exception:
                pass
            return

        srv = res["server"]
        jitter_line = f"\n抖动    `{_fmt_ping(res['jitter'])}`" if res["jitter"] else ""
        isp_line    = f"\nISP     {res['isp']}" if res["isp"] else ""

        caption = (
            f"**测速结果**\n\n"
            f"服务器  `{srv['id']}` {srv['name']}\n"
            f"节点    {srv['location']}, {srv['country']}"
            f"{isp_line}\n\n"
            f"下载    `{_fmt_speed(res['download'])}`\n"
            f"上传    `{_fmt_speed(res['upload'])}`\n"
            f"延迟    `{_fmt_ping(res['ping'])}`"
            f"{jitter_line}"
        )

        # 直接把 .png URL 传给 Telethon，由 Telegram 服务端下载图片
        await tip.delete()
        if res["result_url"]:
            try:
                await self.client.send_file(
                    chat_id,
                    file=f'{res["result_url"]}.png',
                    caption=caption,
                    force_document=False,
                )
                return
            except Exception as e:
                logger.warning("[speedtest] 发送结果图失败，退回文字: %s", e)

        await self.client.send_message(chat_id, caption)

    async def _tip(self, chat_id: int, text: str, delay: int = 5):
        msg = await self.client.send_message(chat_id, text)
        await asyncio.sleep(delay)
        try:
            await msg.delete()
        except Exception:
            pass
