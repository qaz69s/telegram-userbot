"""
插件: sys_info —— 发送 #sys 查看系统运行状态

用法:
  #sys  —— 显示 CPU、内存、磁盘、网络流量、运行天数、CPU 温度等信息
"""
import asyncio
import logging
import platform
import re
import time

import psutil
from telethon import events

from core.plugin_base import BasePlugin

logger = logging.getLogger(__name__)

# 记录 Bot 启动时间，用于计算运行时长
_BOT_START_TIME = time.time()


def _fmt_bytes(n: float) -> str:
    """将字节数格式化为人类可读形式。"""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _fmt_uptime(seconds: float) -> str:
    """将秒数格式化为 X天 X时 X分。"""
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append(f"{days} 天")
    if hours:
        parts.append(f"{hours} 小时")
    parts.append(f"{minutes} 分钟")
    return " ".join(parts)


def _get_cpu_temp() -> str:
    """获取 CPU 温度（Linux 有效；Windows/Mac 返回"不支持"）。"""
    try:
        temps = psutil.sensors_temperatures()
        if not temps:
            return "不支持"
        # 优先取 coretemp / k10temp / cpu-thermal
        for key in ("coretemp", "k10temp", "cpu-thermal", "cpu_thermal"):
            if key in temps:
                entries = temps[key]
                avg = sum(e.current for e in entries) / len(entries)
                return f"{avg:.1f} °C"
        # 取第一个传感器的第一个读数
        first = next(iter(temps.values()))
        return f"{first[0].current:.1f} °C"
    except (AttributeError, Exception):
        return "不支持"


def _get_os_pretty() -> str:
    """读取 /etc/os-release 获取发行版名称，如 Debian GNU/Linux 12 (bookworm)。"""
    try:
        with open("/etc/os-release") as f:
            info = {}
            for line in f:
                line = line.strip()
                if "=" in line:
                    k, v = line.split("=", 1)
                    info[k] = v.strip('"')
        return info.get("PRETTY_NAME") or info.get("NAME", "未知")
    except Exception:
        return platform.system()


def _get_cpu_model() -> str:
    """从 /proc/cpuinfo / lscpu 读取 CPU 型号（支持 ARM64）。"""
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    # ARM64: lscpu 的 Model name 行
    try:
        import subprocess
        out = subprocess.run(["lscpu"], capture_output=True, text=True, timeout=5).stdout
        for line in out.split("\n"):
            if "Model name" in line:
                m = line.split(":", 1)
                if len(m) > 1:
                    return m[1].strip()
    except Exception:
        pass
    return platform.processor() or "未知"


def _build_report() -> str:
    """采集系统信息并构建报告文本。"""
    # ── CPU ──────────────────────────────────────────────────
    cpu_model = _get_cpu_model()
    cpu_pct = psutil.cpu_percent(interval=0.5)
    cpu_cores_phys = psutil.cpu_count(logical=False) or 1
    cpu_cores_logi = psutil.cpu_count(logical=True)
    cpu_freq = psutil.cpu_freq()
    if cpu_freq:
        freq_str = f"{cpu_freq.current:.0f} MHz"
    else:
        # VM / ARM: 尝试从 lscpu 取
        try:
            import subprocess
            out = subprocess.run(["lscpu"], capture_output=True, text=True, timeout=5).stdout
            for line in out.split("\n"):
                if "CPU max MHz" in line or "CPU min MHz" in line or "CPU dynamic" in line:
                    continue
                if "CPU" in line and "MHz" in line or "GHz" in line:
                    m = re.search(r"[\d.]+(?:GHz|MHz)", line)
                    if m:
                        freq_str = m.group(0)
                        break
        except Exception:
            pass
        if not freq_str:
            freq_str = "未知"
    cpu_temp = _get_cpu_temp()

    # ── 内存 ─────────────────────────────────────────────────
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()

    # ── 磁盘 ─────────────────────────────────────────────────
    disk = psutil.disk_usage("/")

    # ── 网络流量（累计） ──────────────────────────────────────
    net = psutil.net_io_counters()

    # ── 系统运行时长 ──────────────────────────────────────────
    sys_boot = psutil.boot_time()
    sys_uptime = time.time() - sys_boot

    # ── Bot 运行时长 ──────────────────────────────────────────
    bot_uptime = time.time() - _BOT_START_TIME

    # ── 系统平台 ──────────────────────────────────────────────
    uname = platform.uname()
    os_pretty = _get_os_pretty()
    kernel = uname.release

    lines = [
        "**系统状态**",
        "--------------------",
        f"系统     {os_pretty}",
        f"内核     {kernel}",
        f"开机     {_fmt_uptime(sys_uptime)}",
        f"Bot 运行 {_fmt_uptime(bot_uptime)}",
        "",
        "**CPU**",
        f"型号     {cpu_model}",
        f"核心     {cpu_cores_phys} 物理 / {cpu_cores_logi} 逻辑",
        f"频率     {freq_str}",
        f"使用率   {cpu_pct}%",
        f"温度     {cpu_temp}",
        "",
        "**内存**",
        f"内存     {_fmt_bytes(mem.used)} / {_fmt_bytes(mem.total)}  ({mem.percent}%)",
        f"Swap     {_fmt_bytes(swap.used)} / {_fmt_bytes(swap.total)}  ({swap.percent}%)",
        "",
        "**磁盘 /**",
        f"已用     {_fmt_bytes(disk.used)} / {_fmt_bytes(disk.total)}  ({disk.percent}%)",
        "",
        "**网络流量（开机累计）**",
        f"上传     {_fmt_bytes(net.bytes_sent)}",
        f"下载     {_fmt_bytes(net.bytes_recv)}",
    ]
    return "\n".join(lines)


class SysInfoPlugin(BasePlugin):
    name        = "sys_info"
    description = "#sys 查看 CPU、内存、磁盘、网络流量、运行时长等系统信息"
    version     = "1.0.0"

    async def on_startup(self):
        logger.info("[sys_info] 插件就绪")

    async def setup(self):
        prefix = re.escape(self.config.get("CMD_PREFIX", "#"))

        @self.client.on(events.NewMessage(outgoing=True, pattern=rf"(?i)^{prefix}sys$"))
        async def handler(event):
            try:
                await event.delete()
            except Exception as e:
                logger.warning("[sys_info] 删除指令消息失败: %s", e)

            tip = await self.client.send_message(event.chat_id, "采集系统信息中...")
            try:
                report = await asyncio.get_running_loop().run_in_executor(
                    None, _build_report
                )
                await tip.edit(report, parse_mode="markdown")
            except Exception as e:
                logger.exception("[sys_info] 采集失败: %s", e)
                await tip.edit(f"采集失败：{e}")
