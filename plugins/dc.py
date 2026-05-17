"""
插件: dc —— 发送 #dc 测试 Bot 经代理到 Telegram DC1~DC5 的完整路径延迟

用法:
  #dc  —— 发送 HTTP 探测并等待真实响应，展示平均/最小/最大 RTT

原理:
  TCP connect 在 Clash TUN 等透明代理下会被立即伪造 SYN-ACK，
  只测到本机→代理（~4ms）。本插件在建连后发送实际数据，
  代理必须将数据转发至 Telegram 并中继响应，
  从而测得 本机 → 代理 → DC → 代理 → 本机 的完整往返时间。
"""
import asyncio
import logging
import re
import time

from telethon import events

from core.plugin_base import BasePlugin

logger = logging.getLogger(__name__)

_DC_HOSTS: dict[int, str] = {
    1: "149.154.175.53",
    2: "149.154.167.51",
    3: "149.154.175.100",
    4: "149.154.167.91",
    5: "91.108.56.130",
}

# DC 地理位置
_DC_LOCATION: dict[int, str] = {
    1: "Miami, US",
    2: "Amsterdam, NL",
    3: "Miami, US",
    4: "Amsterdam, NL",
    5: "Singapore, SG",
}
_PORT    = 443
_COUNT   = 4      # 每个 DC 测试次数
_TIMEOUT = 6.0    # 单次超时（秒）

# 发给服务端的探测载荷（HTTP 请求，Telegram 会以 RST 或数据响应）
_PROBE = b"GET / HTTP/1.0\r\n\r\n"


async def _probe_rtt(host: str, port: int, timeout: float) -> float | None:
    """
    建立 TCP 连接后发送探测数据，等待服务端任意响应（数据/RST/EOF）。
    代理无法伪造远端响应，因此反映真实全链路 RTT。
    """
    t0 = time.monotonic()
    writer = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
        writer.write(_PROBE)
        await writer.drain()
        # 等待任意响应（Telegram 通常以 RST 断开无效请求）
        await asyncio.wait_for(reader.read(256), timeout=timeout)
        return (time.monotonic() - t0) * 1000
    except asyncio.TimeoutError:
        return None
    except ConnectionResetError:
        # Telegram 以 RST 响应——仍是有效的往返计时
        return (time.monotonic() - t0) * 1000
    except OSError:
        return None
    finally:
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass


async def _test_dc(dc_id: int) -> dict:
    host = _DC_HOSTS[dc_id]
    samples: list[float] = []
    for _ in range(_COUNT):
        ms = await _probe_rtt(host, _PORT, _TIMEOUT)
        if ms is not None:
            samples.append(ms)
    if not samples:
        return dict(dc=dc_id, host=host, avg=None, lo=None, hi=None, loss=100.0)
    loss = (1 - len(samples) / _COUNT) * 100
    return dict(
        dc=dc_id, host=host,
        avg=sum(samples) / len(samples),
        lo=min(samples),
        hi=max(samples),
        loss=loss,
    )


async def _test_all_dcs() -> list[dict]:
    return await asyncio.gather(*[_test_dc(dc_id) for dc_id in _DC_HOSTS])


class DcPlugin(BasePlugin):
    name        = "dc"
    description = "#dc 测试 Bot 经代理到 Telegram DC1~DC5 的完整路径延迟"
    version     = "3.0.0"

    async def on_startup(self):
        logger.info("[dc] 插件就绪")

    async def setup(self):
        prefix = re.escape(self.config.get("CMD_PREFIX", "#"))

        @self.client.on(events.NewMessage(
            outgoing=True,
            pattern=rf"(?i)^{prefix}dc$"
        ))
        async def handler(event):
            await event.delete()
            msg = await event.client.send_message(event.chat_id, "测试 DC 延迟中...")

            results = await _test_all_dcs()
            results.sort(key=lambda r: r["dc"])

            lines = ["**Telegram DC 延迟**", "```"]
            for r in results:
                dc_id, host = r["dc"], r["host"]
                loc = _DC_LOCATION[dc_id]
                if r["avg"] is None:
                    lines.append(f"DC{dc_id}  {host:<18}  {loc:<16}  超时")
                else:
                    avg, lo, hi, loss = r["avg"], r["lo"], r["hi"], r["loss"]
                    loss_str = f"  丢包{loss:.0f}%" if loss > 0 else ""
                    lines.append(
                        f"DC{dc_id}  {host:<18}  {loc:<16}  {avg:6.1f} ms"
                        f"  ({lo:.1f}/{hi:.1f}){loss_str}"
                    )
            lines.append("```")

            await msg.edit("\n".join(lines))
