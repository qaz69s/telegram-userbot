"""
插件: ph —— 北京时间整点自动更换头像（双风格）

用法:
  #ph        —— 切换开关（开启/关闭）
  #ph on     —— 开启（立即换为当前风格头像）
  #ph off    —— 关闭
  #ph now    —— 立即更换一次（不受开关影响）
  #ph a      —— 切换到 A 风格（十二时辰，2小时一换）并立即应用
  #ph b      —— 切换到 B 风格（随机渐变光晕，1小时一换）并立即应用

风格说明:
  A: 十二时辰国风头像，每2小时随时辰切换
  B: 随机渐变光晕（类似相机镜头光斑），每1小时自动随机生成
"""
import asyncio
import re
import io
import json
import logging
import math
import random as _random
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

from telethon import events
from telethon.errors import FloodWaitError
from telethon.tl.functions.photos import UploadProfilePhotoRequest, DeletePhotosRequest
from telethon.tl.types import InputPhoto

from core.plugin_base import BasePlugin

logger = logging.getLogger(__name__)

_STATE_FILE      = Path("data/ph_state.json")
_TZ_BEIJING      = timezone(timedelta(hours=8))
_FONT_CACHE_DIR  = Path.home() / ".cache" / "tgbot-fonts"
_FONT_CACHE_PATH = _FONT_CACHE_DIR / "NotoSansSC-Regular.otf"

# ═══════════════════════════════════════════════════════════════
#  风格 A：十二时辰数据
# ═══════════════════════════════════════════════════════════════

_SHICHEN = [
    ("子时", "鼠", "23:00-01:00", (  8, 12,  45), (165, 195, 255), (105, 140, 215), "stars",   "夜半精灵"),
    ("丑时", "牛", "01:00-03:00", (  5,  8,  35), (195, 215, 255), (130, 155, 215), "moon",    "夜深人静"),
    ("寅时", "虎", "03:00-05:00", ( 18, 10,  42), (210, 165, 255), (155, 110, 210), "dawn",    "破晓之声"),
    ("卯时", "兔", "05:00-07:00", (185, 75,  20), (255, 238, 185), (235, 200, 125), "sunrise", "旭日东升"),
    ("辰时", "龙", "07:00-09:00", ( 28, 88, 168), (210, 245, 255), (155, 208, 248), "morning", "朝气满满"),
    ("巳时", "蛇", "09:00-11:00", ( 18,118, 185), (242, 255, 255), (175, 235, 252), "sunny",   "午前能量"),
    ("午时", "马", "11:00-13:00", (190,145,  10), (255, 250, 195), (235, 215, 128), "blazing", "正午高阳"),
    ("未时", "羊", "13:00-15:00", (165,102,  14), (255, 245, 175), (228, 198, 105), "lazy",    "午后闲情"),
    ("申时", "猴", "15:00-17:00", (150, 82,  22), (255, 232, 150), (215, 175,  85), "sunset",  "黄昏来临"),
    ("酉时", "鸡", "17:00-19:00", (170, 42,  10), (255, 212, 158), (228, 158,  88), "dusk",    "霞光满天"),
    ("戌时", "狗", "19:00-21:00", ( 65, 20,  68), (255, 192, 212), (210, 138, 165), "night",   "暮色沉沉"),
    ("亥时", "猪", "21:00-23:00", ( 14, 14,  50), (188, 188, 255), (128, 128, 205), "sleep",   "夜梦安宁"),
]


def _get_shichen_index(hour: int) -> int:
    return ((hour + 1) // 2) % 12


def _find_font(size: int):
    try:
        from PIL import ImageFont
    except ImportError:
        return None
    candidates = [
        str(_FONT_CACHE_PATH),
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _draw_text_centered(draw, y: int, text: str, font, color, canvas_w: int):
    if font is None:
        return
    if hasattr(font, "getbbox"):
        bbox = font.getbbox(text)
        tw = bbox[2] - bbox[0]
        ty = y - bbox[1]
    else:
        tw = len(text) * (font.size if hasattr(font, "size") else 20)
        ty = y
    draw.text(((canvas_w - tw) // 2, ty), text, font=font, fill=color)


def _draw_crescent(draw, cx, cy, r, moon_color, bg_color):
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=moon_color)
    ox, oy = int(r * 0.42), int(r * 0.04)
    ir = int(r * 0.80)
    draw.ellipse([cx - ir + ox, cy - ir - oy, cx + ir + ox, cy + ir - oy], fill=bg_color)


def _draw_decoration(draw, deco: str, bg, fg, fg2, W: int, H: int):
    rng = _random.Random(deco)

    if deco == "stars":
        for _ in range(62):
            x, y = rng.randint(32, W-32), rng.randint(32, H-32)
            r = rng.choice([1, 1, 1, 2, 2, 3])
            a = rng.randint(145, 255)
            draw.ellipse([x-r, y-r, x+r, y+r], fill=(a, a, min(255, a+20)))
        _draw_crescent(draw, 388, 70, 44, (205, 225, 255), bg)

    elif deco == "moon":
        for _ in range(25):
            x, y = rng.randint(32, W-32), rng.randint(32, H-32)
            r = rng.choice([1, 1, 2])
            draw.ellipse([x-r, y-r, x+r, y+r], fill=(160, 175, 220))
        _draw_crescent(draw, 372, 66, 54, (218, 235, 255), bg)

    elif deco == "dawn":
        for i in range(8):
            alpha = int(55 * (1 - i/8))
            c = (min(255, bg[0]+alpha), min(255, bg[1]+alpha//3), min(255, bg[2]+alpha//2))
            draw.line([(35, H-90-i*20), (W-35, H-90-i*20)], fill=c, width=2)
        for _ in range(15):
            x, y = rng.randint(32, W-32), rng.randint(32, H//2)
            draw.ellipse([x-1, y-1, x+1, y+1], fill=(188, 162, 238))

    elif deco == "sunrise":
        sx, sy = W//2, H + 50
        for angle in range(15, 166, 13):
            rad = math.radians(angle)
            x2 = sx + int(508*math.cos(rad))
            y2 = sy - int(508*math.sin(rad))
            c = tuple(min(255, v+35) for v in bg)
            draw.line([(sx, sy), (x2, y2)], fill=c, width=2)
        draw.ellipse([W//2-55, H-92, W//2+55, H-2], fill=(255, 200, 88))

    elif deco == "morning":
        sx, sy = W//2, H + 22
        for angle in range(22, 158, 10):
            rad = math.radians(angle)
            x2 = sx + int(462*math.cos(rad))
            y2 = sy - int(462*math.sin(rad))
            c = tuple(min(255, v+25) for v in bg)
            draw.line([(sx, sy), (x2, y2)], fill=c, width=3)
        draw.ellipse([W//2-44, H-74, W//2+44, H-2], fill=(248, 222, 115))

    elif deco == "sunny":
        sx, sy = W//2, 80
        for angle in range(0, 360, 22):
            rad = math.radians(angle)
            draw.line([(sx+int(46*math.cos(rad)), sy+int(46*math.sin(rad))),
                       (sx+int(80*math.cos(rad)), sy+int(80*math.sin(rad)))],
                      fill=(255, 245, 140), width=3)
        draw.ellipse([sx-36, sy-36, sx+36, sy+36], fill=(255, 235, 70))

    elif deco == "blazing":
        sx, sy = W//2, 76
        for r_off in range(105, 38, -10):
            lgt = int((r_off - 38) / 67 * 48)
            c = tuple(min(255, v+lgt) for v in bg)
            draw.ellipse([sx-r_off, sy-r_off, sx+r_off, sy+r_off], fill=c)
        for angle in range(0, 360, 16):
            rad = math.radians(angle)
            draw.line([(sx+int(50*math.cos(rad)), sy+int(50*math.sin(rad))),
                       (sx+int(108*math.cos(rad)), sy+int(108*math.sin(rad)))],
                      fill=(255, 248, 100), width=5)
        draw.ellipse([sx-40, sy-40, sx+40, sy+40], fill=(255, 242, 45))

    elif deco == "lazy":
        def _cloud(cx2, cy2, sz):
            cf = tuple(min(255, v+58) for v in bg)
            for dx, dy, r in [(-sz, 5, sz), (0, -sz//2, int(sz*0.78)),
                               (sz, 5, sz), (0, sz//3, sz//2)]:
                draw.ellipse([cx2+dx-r, cy2+dy-r, cx2+dx+r, cy2+dy+r], fill=cf)
        _cloud(122, 85, 38); _cloud(388, 108, 30); _cloud(255, 58, 22)

    elif deco == "sunset":
        sx, sy = W//2, H - 52
        for angle in range(12, 169, 13):
            rad = math.radians(angle)
            x2 = sx + int(492*math.cos(rad))
            y2 = sy - int(492*math.sin(rad))
            c = tuple(min(255, v+30) for v in bg)
            draw.line([(sx, sy), (x2, y2)], fill=c, width=2)
        draw.ellipse([sx-50, sy-50, sx+50, sy+50], fill=(255, 145, 42))

    elif deco == "dusk":
        cloud_colors = [(255, 122, 58), (255, 165, 78), (220, 90, 48)]
        for i, cc in enumerate(cloud_colors):
            y0 = 52 + i*40
            for _ in range(3):
                x0 = rng.randint(15, 180); cw = rng.randint(72, 172); ch = rng.randint(12, 24)
                cf = tuple(min(255, v+45) for v in cc)
                draw.ellipse([x0, y0, x0+cw, y0+ch], fill=cf)
                x0b = rng.randint(252, 358); cw2 = rng.randint(72, 142)
                draw.ellipse([x0b, y0+4, x0b+cw2, y0+ch+4], fill=cf)

    elif deco == "night":
        for _ in range(20):
            x, y = rng.randint(32, W-32), rng.randint(32, H//2+28)
            r = rng.choice([1, 1, 2])
            draw.ellipse([x-r, y-r, x+r, y+r], fill=(192, 202, 235))
        _draw_crescent(draw, 365, 66, 42, (238, 228, 255), bg)

    elif deco == "sleep":
        for _ in range(48):
            x, y = rng.randint(32, W-32), rng.randint(32, H-32)
            r = rng.choice([1, 1, 2, 2, 3])
            draw.ellipse([x-r, y-r, x+r, y+r], fill=(175, 180, 228))
        for zx, zy, zs, zw in [(348, 115, 24, 3), (386, 80, 16, 2), (416, 100, 12, 2)]:
            zc = (202, 202, 242)
            draw.line([(zx, zy), (zx+zs, zy)], fill=zc, width=zw)
            draw.line([(zx+zs, zy), (zx, zy+zs)], fill=zc, width=zw)
            draw.line([(zx, zy+zs), (zx+zs, zy+zs)], fill=zc, width=zw)


def _render_avatar_a(shichen_idx: int) -> bytes:
    """风格 A：十二时辰国风头像，512×512 PNG。"""
    from PIL import Image, ImageDraw

    name, animal, time_range, bg, fg, fg2, deco, subtitle = _SHICHEN[shichen_idx]
    W = H = 512

    img = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(img)
    cx, cy = W // 2, H // 2
    for r in range(232, 0, -4):
        light = int(50 * (1 - r / 232))
        c = tuple(min(255, v + light) for v in bg)
        draw.ellipse([cx-r, cy-r, cx+r, cy+r], fill=c)

    _draw_decoration(draw, deco, bg, fg, fg2, W, H)

    ring = tuple(min(255, v + 72) for v in bg)
    draw.ellipse([20, 20, W-20, H-20], outline=ring, width=3)
    draw.ellipse([30, 30, W-30, H-30], outline=ring, width=1)

    font_big  = _find_font(218)
    font_name = _find_font(52)
    font_sub  = _find_font(28)
    font_time = _find_font(22)

    if font_big:
        if hasattr(font_big, "getbbox"):
            bbox = font_big.getbbox(animal)
            tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
            tx = (W - tw) // 2 - bbox[0]
            ty = (H - th) // 2 - 50 - bbox[1]
        else:
            tx, ty = W//2 - 110, H//2 - 165
        draw.text((tx, ty), animal, font=font_big, fill=fg)

    _draw_text_centered(draw, H - 140, name,      font_name, fg,  W)
    _draw_text_centered(draw, H -  88, subtitle,  font_sub,  fg2, W)
    _draw_text_centered(draw, H -  50, time_range, font_time, fg2, W)

    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# ═══════════════════════════════════════════════════════════════
#  风格 B：单锚点彩虹弧渐变 + 磨砂颗粒（每小时不同）
#
#  核心逻辑：从画布角落外一个点出发，以距离为轴做径向色带渐变，
#  最终效果类似参考图：多条彩色弧带从角落扫入，背景接近白色。
# ═══════════════════════════════════════════════════════════════

# 调色盘：颜色从"近端（角落处）"到"远端（画布对角）"排列，尾部渐变到白
# 每条颜色是一个高饱和色，相邻色之间自然过渡
_B_PALETTES = [
    # 红→粉→紫→蓝（参考图风格）
    [(230,  10,  40), (240,  20, 140), (160,  20, 230), (100, 140, 255)],
    # 橙→黄→绿→青
    [(255,  80,  10), (240, 200,   0), ( 60, 210,  60), (  0, 200, 200)],
    # 青→蓝→紫→品红
    [(  0, 210, 230), ( 30,  80, 240), (130,  20, 230), (230,  20, 160)],
    # 橙→品红→紫→蓝紫
    [(255, 100,  10), (230,  20, 130), (150,  10, 230), ( 80, 100, 255)],
    # 黄绿→绿→青→蓝
    [(180, 230,   0), ( 20, 210,  80), (  0, 190, 210), ( 40,  80, 240)],
    # 红→橙→黄→绿
    [(220,  10,  30), (255, 110,   0), (230, 210,   0), ( 50, 200,  80)],
    # 品红→粉→橙→黄
    [(220,   0, 160), (255,  60, 120), (255, 140,  30), (240, 220,   0)],
    # 蓝→青→绿→黄绿
    [( 20,  60, 230), (  0, 180, 230), (  0, 210, 120), (140, 230,  20)],
    # 深紫→紫→粉→橙
    [(100,   0, 200), (200,  20, 200), (255,  80, 130), (255, 160,  40)],
    # 青绿→蓝→紫→红
    [(  0, 200, 160), ( 20, 100, 240), (160,  10, 240), (230,  10,  60)],
    # 金→橙红→品红→紫
    [(240, 190,   0), (240,  60,  20), (200,   0, 140), (120,  10, 220)],
    # 霓虹绿→青→蓝→品
    [( 20, 230,  80), (  0, 200, 200), ( 40,  80, 240), (220,   0, 200)],
]


def _render_avatar_b(seed: int) -> bytes:
    """
    风格 B：单锚点彩虹弧渐变磨砂头像，512×512 PNG。
    seed = 北京时间 epoch_hours。
    """
    from PIL import Image, ImageDraw, ImageFilter

    rng = _random.Random(seed)
    W = H = 512
    WHITE = (255, 255, 255)

    # ── 1. 选调色盘，颜色轻微微扰 ───────────────────────────────────
    colors_base = rng.choice(_B_PALETTES)

    def jitter(c, d=12):
        return tuple(max(0, min(255, v + rng.randint(-d, d))) for v in c)

    colors = [jitter(c) for c in colors_base]
    # 尾部追加白色，让最远处自然淡出
    colors.append(WHITE)

    # ── 2. 随机选锚点（4个角落偏外）────────────────────────────────
    # 角落偏移量：让锚点在画布外 10-30%
    off = int(W * rng.uniform(0.08, 0.28))
    corner_choices = [
        (-off,       -off      ),   # 左上
        (W + off,    -off      ),   # 右上
        (-off,       H + off   ),   # 左下
        (W + off,    H + off   ),   # 右下
    ]
    cx, cy = rng.choice(corner_choices)

    # 对角方向的最远距离（锚点到对角的距离）
    far_x = (W - cx) if cx < W // 2 else -cx
    far_y = (H - cy) if cy < H // 2 else -cy
    max_r  = int(math.sqrt(far_x ** 2 + far_y ** 2)) + 30

    # ── 3. 同心椭圆径向渐变（从外到内绘制，内层覆盖外层）───────────
    img  = Image.new("RGB", (W, H), WHITE)
    draw = ImageDraw.Draw(img)

    # 椭圆轻微形变，增加自然感
    squeeze = rng.uniform(0.82, 1.0)  # y 方向压缩比
    angle_skew = rng.uniform(-0.12, 0.12)  # x 轴轻微平移偏斜（单位：max_r）

    n_steps = 80   # 步数越多，渐变越平滑
    n_colors = len(colors)  # 含尾部白色

    for s in range(n_steps, 0, -1):
        t = s / n_steps        # 1.0 = 最内（近角落），0.0 = 最外（白色）

        # 当前半径
        r = int(max_r * t)

        # t 映射到颜色索引（0→白, 1→colors[0]）
        # 颜色从 colors[0]（内）→ ... → colors[-1]=WHITE（外）
        # 所以 t=1 → 颜色索引 0，t=0 → 颜色索引 n_colors-1（白）
        ci_f = (1.0 - t) * (n_colors - 1)
        ci   = int(ci_f)
        frac = ci_f - ci
        ci   = min(ci, n_colors - 2)
        c1, c2 = colors[ci], colors[ci + 1]
        blend = tuple(int(c1[j] * (1 - frac) + c2[j] * frac) for j in range(3))

        # 椭圆中心随 t 轻微偏移（制造色带非正圆、更自然的弧感）
        skew_x = int(angle_skew * r)
        ry     = int(r * squeeze)
        ex, ey = cx + skew_x, cy

        draw.ellipse([ex - r, ey - ry, ex + r, ey + ry], fill=blend)

    # ── 4. 大半径模糊（核心：让色带柔和衔接，产生散焦感）─────────────
    blur_r = rng.randint(55, 95)
    img = img.filter(ImageFilter.GaussianBlur(radius=blur_r))

    # ── 5. 过曝白光（仅 1 处，在锚点角落，制造"烧穿"高光）─────────
    glow = Image.new("RGB", (W, H), WHITE)
    gdraw = ImageDraw.Draw(glow)
    gr = rng.randint(100, 200)
    gdraw.ellipse([cx - gr, cy - gr, cx + gr, cy + gr], fill=WHITE)
    glow = glow.filter(ImageFilter.GaussianBlur(radius=rng.randint(60, 100)))
    img  = Image.blend(img, glow, alpha=rng.uniform(0.20, 0.38))

    # ── 6. 磨砂颗粒纹理（全分辨率生成，避免放大产生可见点块）─────────
    # 直接在 512×512 生成噪点，再用足够大的模糊半径柔化成"雾面"质感
    grain_strength = rng.randint(18, 26)
    n = W * H * 3
    d = bytearray(n)
    for k in range(0, n, 3):
        v = max(0, min(255, 128 + rng.randint(-grain_strength, grain_strength)))
        d[k] = d[k+1] = d[k+2] = v
    grain = Image.frombytes("RGB", (W, H), bytes(d))
    # 模糊半径 1.5~2.5：消除单像素颗粒感，保留整体磨砂雾面
    grain = grain.filter(ImageFilter.GaussianBlur(radius=rng.uniform(1.5, 2.5)))

    img_px    = list(img.getdata())
    grain_px  = list(grain.getdata())
    fa        = rng.uniform(0.14, 0.20)   # 可见度适中，不盖色

    out = []
    for (r, g, b), (gn, _, _) in zip(img_px, grain_px):
        delta = (gn - 128) * fa
        out.append((max(0, min(255, int(r + delta))),
                    max(0, min(255, int(g + delta))),
                    max(0, min(255, int(b + delta)))))
    img.putdata(out)

    # ── 7. 收尾极轻模糊，颗粒与色带融合 ───────────────────────────
    img = img.filter(ImageFilter.GaussianBlur(radius=0.4))

    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# ═══════════════════════════════════════════════════════════════
#  字体下载
# ═══════════════════════════════════════════════════════════════

async def _ensure_font():
    if _FONT_CACHE_PATH.exists():
        return
    _SYSTEM = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    ]
    for p in _SYSTEM:
        if Path(p).exists():
            logger.info("[ph] 使用系统 CJK 字体: %s", p)
            return
    _URLS = [
        "https://cdn.jsdelivr.net/gh/googlefonts/noto-cjk@main/Sans/SubsetOTF/SC/NotoSansSC-Regular.otf",
        "https://github.com/googlefonts/noto-cjk/raw/main/Sans/SubsetOTF/SC/NotoSansSC-Regular.otf",
    ]
    for url in _URLS:
        try:
            logger.info("[ph] 下载字体中: %s", url)
            _FONT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = _FONT_CACHE_PATH.with_suffix(".tmp")
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, lambda u=url, t=tmp: urllib.request.urlretrieve(u, t)
            )
            tmp.rename(_FONT_CACHE_PATH)
            logger.info("[ph] 字体已保存: %s", _FONT_CACHE_PATH)
            return
        except Exception as e:
            logger.warning("[ph] 字体下载失败 (%s): %s", url, e)
            _FONT_CACHE_PATH.with_suffix(".tmp").unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════
#  插件主体
# ═══════════════════════════════════════════════════════════════

class PhPlugin(BasePlugin):
    name        = "ph"
    description = "#ph a/b 整点自动更换头像（A=十二时辰 B=随机光晕）"
    version     = "2.0.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._enabled: bool       = False
        self._mode:    str        = "a"        # "a" 或 "b"
        self._task:    asyncio.Task | None = None
        self._last_photo_id: int | None   = None

    async def on_startup(self):
        await _ensure_font()
        self._load_state()
        if self._enabled:
            self._start_loop()
        logger.info("[ph] 插件就绪，模式=%s，状态=%s", self._mode, "开启" if self._enabled else "关闭")

    async def on_shutdown(self):
        self._stop_loop()

    async def setup(self):
        prefix = re.escape(self.config.get("CMD_PREFIX", "#"))
        actual_prefix = self.config.get("CMD_PREFIX", "#")

        @self.client.on(events.NewMessage(
            outgoing=True,
            pattern=rf"(?i)^{prefix}ph(?:\s+(on|off|now|a|b))?$",
        ))
        async def cmd_handler(event):
            try:
                await event.delete()
            except Exception:
                pass

            arg = (event.pattern_match.group(1) or "").lower()

            # ── #ph a / #ph b：切换风格 ──────────────────────────────
            if arg in ("a", "b"):
                self._mode = arg
                self._save_state()
                mode_desc = "十二时辰国风（2小时/次）" if arg == "a" else "随机渐变光晕（1小时/次）"
                result = await self._update_avatar()
                msg = await self.client.send_message(
                    event.chat_id,
                    f"切换到风格 {arg.upper()}：{mode_desc}\n{result}",
                )
                await asyncio.sleep(6)
                await msg.delete()
                return

            # ── #ph now：立即换一次 ───────────────────────────────────
            if arg == "now":
                result = await self._update_avatar()
                msg = await self.client.send_message(event.chat_id, result)
                await asyncio.sleep(6)
                await msg.delete()
                return

            # ── #ph / #ph on / #ph off：开关 ─────────────────────────
            if arg == "on":
                self._enabled = True
            elif arg == "off":
                self._enabled = False
            else:
                self._enabled = not self._enabled

            self._save_state()

            if self._enabled:
                self._start_loop()
                result = await self._update_avatar()
                interval = "2小时" if self._mode == "a" else "1小时"
                status = f"自动换头像：已开启（风格 {self._mode.upper()}，每{interval}）\n{result}"
            else:
                self._stop_loop()
                status = "自动换头像：已关闭"

            msg = await self.client.send_message(event.chat_id, status)
            await asyncio.sleep(5)
            await msg.delete()

    # ── 循环调度 ──────────────────────────────────────────────────────

    def _start_loop(self):
        self._stop_loop()
        self._task = asyncio.create_task(self._schedule_loop())
        logger.info("[ph] 换头像任务已启动（模式=%s）", self._mode)

    def _stop_loop(self):
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None

    async def _schedule_loop(self):
        """
        A 模式：每个时辰整点（每2小时）触发
        B 模式：每小时整点触发
        两者都等到"下一个整点"即可，只是 A 模式在非时辰切换点也会运行但图不变。
        为简单起见，统一每小时整点检查一次，A 模式每2小时实际换图，B 模式每小时换图。
        """
        while self._enabled:
            now = datetime.now(_TZ_BEIJING)
            secs = (60 - now.minute) * 60 - now.second
            if secs <= 0:
                secs += 3600
            logger.info("[ph] 下次检查在 %d 秒后（%02d:00 北京时间）", secs, (now.hour + 1) % 24)
            await asyncio.sleep(secs)
            if self._enabled:
                await self._update_avatar()

    async def _update_avatar(self) -> str:
        """根据当前模式渲染并上传头像。返回状态文字（直接可发送）。"""
        try:
            now = datetime.now(_TZ_BEIJING)
            loop = asyncio.get_running_loop()

            if self._mode == "a":
                idx = _get_shichen_index(now.hour)
                sc_name, animal = _SHICHEN[idx][0], _SHICHEN[idx][1]
                png_bytes = await loop.run_in_executor(None, _render_avatar_a, idx)
                logger.info("[ph-a] 头像 -> %s（%s）", sc_name, animal)
            else:
                epoch_hours = int(now.timestamp() // 3600)
                png_bytes = await loop.run_in_executor(None, _render_avatar_b, epoch_hours)
                logger.info("[ph-b] 光晕头像 -> seed=%d (%s)", epoch_hours, now.strftime("%H:00"))

            uploaded = await self.client.upload_file(
                io.BytesIO(png_bytes), file_name="avatar.png"
            )

            try:
                await self.client(UploadProfilePhotoRequest(file=uploaded))
            except FloodWaitError as e:
                hours = e.seconds / 3600
                msg = f"头像更换过于频繁，需等待 {hours:.1f} 小时（{e.seconds} 秒）"
                logger.warning("[ph] %s", msg)
                if self._enabled:
                    self._enabled = False
                    self._save_state()
                    self._stop_loop()
                return msg

            # 分批删除旧头像
            try:
                photos = await self.client.get_profile_photos("me")
                if len(photos) > 1:
                    old_photos = photos[1:]
                    for i in range(0, len(old_photos), 50):
                        batch = old_photos[i:i+50]
                        to_del = [
                            InputPhoto(id=p.id, access_hash=p.access_hash,
                                       file_reference=p.file_reference)
                            for p in batch
                        ]
                        await self.client(DeletePhotosRequest(id=to_del))
                    logger.info("[ph] 已清理 %d 张旧头像", len(old_photos))
            except Exception as e:
                logger.warning("[ph] 清理旧头像失败: %s", e)

            self._save_state()
            return "头像已更换"
        except FloodWaitError as e:
            hours = e.seconds / 3600
            return f"头像更换过于频繁，需等待 {hours:.1f} 小时"
        except Exception as e:
            logger.warning("[ph] 更新头像失败: %s", e)
            return f"头像更换失败：{e}"

    # ── 状态持久化 ────────────────────────────────────────────────────

    def _save_state(self):
        try:
            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _STATE_FILE.write_text(json.dumps({
                "enabled":       self._enabled,
                "mode":          self._mode,
                "last_photo_id": self._last_photo_id,
            }))
        except Exception as e:
            logger.warning("[ph] 保存状态失败: %s", e)

    def _load_state(self):
        try:
            if _STATE_FILE.exists():
                data = json.loads(_STATE_FILE.read_text())
                self._enabled       = data.get("enabled", False)
                self._mode          = data.get("mode", "a")
                self._last_photo_id = data.get("last_photo_id", None)
        except Exception as e:
            logger.warning("[ph] 加载状态失败: %s", e)
