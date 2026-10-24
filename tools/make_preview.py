#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 README 用的预览图（示意图，非真实截图）。

    python3 tools/make_preview.py
"""
import os
from PIL import Image, ImageDraw, ImageFilter, ImageFont

CARD_W, SHADOW, INNER_PAD, RADIUS = 322, 16, 22, 26
BAR_W = CARD_W - INNER_PAD * 2
W = CARD_W + 2 * SHADOW
H = 386

COLOR = {'disk': (10, 132, 255), 'mem': (48, 209, 88), 'cpu': (255, 159, 10),
         'battery': (191, 90, 242), 'net': (100, 210, 255)}

FONTS = ['/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
         '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc',
         '/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf']


def font(size):
    for p in FONTS:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def card(locked=False):
    img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    sh = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    sd.rounded_rectangle([SHADOW - 2, SHADOW + 2, W - SHADOW + 2, H - SHADOW + 4],
                         radius=RADIUS + 4, fill=(0, 0, 0, 140))
    img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(10)))

    c = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    cd = ImageDraw.Draw(c)
    box = [SHADOW, SHADOW, W - SHADOW, H - SHADOW]
    if locked:
        cd.rounded_rectangle(box, radius=RADIUS, fill=(30, 30, 32, 245))
    else:
        cd.rounded_rectangle(box, radius=RADIUS, fill=(30, 30, 32, 238),
                             outline=(255, 159, 10, 200), width=2)
    img = Image.alpha_composite(img, c)

    d = ImageDraw.Draw(img)
    x0, x1 = SHADOW + INNER_PAD, W - SHADOW - INNER_PAD
    y = SHADOW + 17
    f14, f16, f13 = font(14), font(16), font(13)

    d.ellipse([x0, y + 5, x0 + 9, y + 14], fill=(255, 159, 10, 255))
    d.text((x0 + 19, y), '系统状态', font=f14, fill=(168, 168, 172, 255))
    d.text((x1, y), '13:31', font=f14, fill=(112, 112, 116, 255), anchor='ra')
    y += 30

    rows = [
        ('disk', '磁盘', 3, '已用 15.9 GB · 剩 427.6 GB', True),
        ('mem', '内存', 10, '3.0 GB / 30.6 GB', True),
        ('cpu', 'CPU', 4, '20 核', True),
        ('net', '网络', None, '↓ 338 B/s', False),
        ('battery', '电池', None, '未充电 · 电量计无读数', True),
    ]
    for key, label, pct, sub, bar in rows:
        d.text((x0, y), label, font=f16, fill=(224, 224, 228, 255))
        if key == 'net':
            d.text((x1, y), sub, font=f16, fill=COLOR['net'], anchor='ra')
        elif pct is None:
            d.text((x1, y), '—', font=f16, fill=COLOR[key], anchor='ra')
        else:
            d.text((x1, y), f'{pct}%', font=f16, fill=COLOR[key], anchor='ra')
        y += 24
        if bar:
            d.rounded_rectangle([x0, y, x0 + BAR_W, y + 6], radius=3, fill=(255, 255, 255, 40))
            if pct:
                d.rounded_rectangle([x0, y, x0 + int(BAR_W * pct / 100), y + 6],
                                    radius=3, fill=COLOR[key])
            y += 14
        if key != 'net':
            d.text((x0, y), sub, font=f13, fill=(150, 150, 155, 255))
            y += 20
        y += 4
    return img


def backdrop(w, h):
    img = Image.new('RGB', (w, h), (14, 16, 24))
    d = ImageDraw.Draw(img)
    for i in range(h):
        t = i / h
        d.line([(0, i), (w, i)], fill=(int(20 + 12 * t), int(22 + 10 * t), int(34 + 14 * t)))
    glow = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([w * 0.35, -h * 0.25, w * 1.05, h * 0.9], fill=(70, 80, 130, 90))
    glow = glow.filter(ImageFilter.GaussianBlur(80))
    return Image.alpha_composite(img.convert('RGBA'), glow)


def main():
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'screenshots', 'preview.png')
    bw, bh = 1120, H + 120
    bg = backdrop(bw, bh)
    c1 = card(locked=False)
    c2 = card(locked=True)
    gap = 60
    total = W * 2 + gap
    left = (bw - total) // 2
    bg.alpha_composite(c1, (left, 60))
    bg.alpha_composite(c2, (left + W + gap, 60))
    bg.convert('RGB').save(out, 'PNG')
    print('已生成', out)


if __name__ == '__main__':
    main()
