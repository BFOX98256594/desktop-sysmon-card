#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""系统状态卡片 —— macOS 版。

贴在桌面上的状态卡片：磁盘、内存、CPU、网络、电池。
窗口层级设在桌面层（kCGDesktopWindowLevel），因此被所有应用窗口盖住，
只出现在桌面上；可拖动，双击固定/解锁。

依赖：
    pip3 install pillow pyobjc-framework-Cocoa psutil

用法：
    python3 sysmon_card.py

注意：本脚本在 Linux 上编写，未在真实 macOS 上实测。
"""

import io
import json
import math
import os
import time

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import AppKit
import Foundation
import Quartz
import objc

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

# ---------- 尺寸与配色 ----------

CARD_W = 322
SHADOW = 16
INNER_PAD = 22
BAR_W = CARD_W - INNER_PAD * 2
RADIUS = 26
TICK_MS = 1.0
DRAG_THRESHOLD = 4

COLOR = {
    'disk': (10, 132, 255),
    'mem': (48, 209, 88),
    'cpu': (255, 159, 10),
    'battery': (191, 90, 242),
    'net': (100, 210, 255),
}

STATE_FILE = os.path.expanduser('~/Library/Application Support/sysmon-card.json')

FONT_CANDIDATES = [
    '/System/Library/Fonts/PingFang.ttc',
    '/System/Library/Fonts/STHeiti Light.ttc',
    '/System/Library/Fonts/Hiragino Sans GB.ttc',
]

BATTERY_STATUS = {'充电中': '充电中', '放电中': '放电中', '已充满': '已充满'}


def font(size):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


# ---------- 数据 ----------

def fmt_size(n):
    k = 1024
    if n >= k ** 4:
        return f'{n / k ** 4:.2f} TB'
    if n >= k ** 3:
        return f'{n / k ** 3:.1f} GB'
    if n >= k ** 2:
        return f'{n / k ** 2:.0f} MB'
    return f'{n / k:.0f} KB'


def fmt_rate(n):
    if n >= 1024 ** 2:
        return f'{n / 1024 ** 2:.1f} MB/s'
    if n >= 1024:
        return f'{n / 1024:.0f} KB/s'
    return f'{round(n)} B/s'


def disk_usage():
    try:
        st = os.statvfs('/')
        total = st.f_blocks * st.f_frsize
        free = st.f_bfree * st.f_frsize
        avail = st.f_bavail * st.f_frsize
        used = total - free
        return used / total * 100, f'已用 {fmt_size(used)} · 剩 {fmt_size(avail)}'
    except OSError as e:
        return None, f'读取失败 {e}'


def memory_usage():
    if psutil is None:
        return None, '需要 psutil'
    vm = psutil.virtual_memory()
    used = vm.total - vm.available
    return used / vm.total * 100, f'{fmt_size(used)} / {fmt_size(vm.total)}'


def battery_info():
    if psutil is None:
        return None, '需要 psutil'
    try:
        bat = psutil.sensors_battery()
        if bat is None:
            return None, '未检测到电池'
        status = '充电中' if bat.power_plugged else '放电中'
        return int(bat.percent), status
    except Exception as e:
        return None, f'读取失败 {e}'


# ---------- 绘制 ----------

def render_card(size, state):
    w, h = size
    img = Image.new('RGBA', (w, h), (0, 0, 0, 0))

    sh = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    sd.rounded_rectangle([SHADOW - 2, SHADOW + 2, w - SHADOW + 2, h - SHADOW + 4],
                         radius=RADIUS + 4, fill=(0, 0, 0, 120))
    sh = sh.filter(ImageFilter.GaussianBlur(9))
    img = Image.alpha_composite(img, sh)

    card = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    box = [SHADOW, SHADOW, w - SHADOW, h - SHADOW]
    if state['locked']:
        cd.rounded_rectangle(box, radius=RADIUS, fill=(30, 30, 32, 245))
    else:
        cd.rounded_rectangle(box, radius=RADIUS, fill=(30, 30, 32, 238),
                             outline=(255, 159, 10, 200), width=2)
    img = Image.alpha_composite(img, card)

    d = ImageDraw.Draw(img)
    x0 = SHADOW + INNER_PAD
    x1 = w - SHADOW - INNER_PAD
    y = SHADOW + 17

    f14, f16b, f13 = font(14), font(16), font(13)

    d.ellipse([x0, y + 5, x0 + 9, y + 14],
              fill=(255, 159, 10, 255) if not state['locked'] else (48, 209, 88, 255))
    d.text((x0 + 19, y), '系统状态', font=f14, fill=(168, 168, 172, 255))
    d.text((x1, y), state['clock'], font=f14, fill=(112, 112, 116, 255), anchor='ra')
    y += 30

    for key, label, pct, sub, has_bar in state['rows']:
        d.text((x0, y), label, font=f16b, fill=(224, 224, 228, 255))
        if key == 'net':
            d.text((x1, y), state['net_down'], font=f16b, fill=COLOR['net'], anchor='ra')
        elif pct is None:
            d.text((x1, y), '—', font=f16b, fill=COLOR.get(key, (200, 200, 200)), anchor='ra')
        else:
            d.text((x1, y), f'{round(pct)}%', font=f16b,
                   fill=COLOR.get(key, (200, 200, 200)), anchor='ra')
        y += 24

        if has_bar:
            d.rounded_rectangle([x0, y, x0 + BAR_W, y + 6], radius=3,
                                fill=(255, 255, 255, 40))
            if pct:
                fw = max(6, int(BAR_W * pct / 100))
                d.rounded_rectangle([x0, y, x0 + fw, y + 6], radius=3,
                                    fill=COLOR.get(key, (200, 200, 200)))
            y += 14

        if sub:
            d.text((x0, y), sub, font=f13, fill=(150, 150, 155, 255))
            y += 20
        y += 4

    return img


def pil_to_nsimage(img):
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    data = buf.getvalue()
    nsdata = Foundation.NSData.dataWithBytes_length_(data, len(data))
    return AppKit.NSImage.alloc().initWithData_(nsdata)


# ---------- 视图（处理拖动与双击） ----------

class CardView(AppKit.NSView):
    def initWithFrame_(self, frame):
        self = objc.super(CardView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._press = None
        return self

    def acceptsFirstMouse_(self, _event):
        return True

    def mouseDown_(self, event):
        loc = AppKit.NSEvent.mouseLocation()
        frame = self.window().frame()
        self._press = {
            'x': loc.x,
            'y': loc.y,
            'wx': frame.origin.x,
            'wy': frame.origin.y,
            'moved': False,
            'double': event.clickCount() == 2,
        }

    def mouseDragged_(self, event):
        if not self._press or self._locked():
            return
        loc = AppKit.NSEvent.mouseLocation()
        dx = loc.x - self._press['x']
        dy = loc.y - self._press['y']
        if not self._press['moved']:
            if abs(dx) <= DRAG_THRESHOLD and abs(dy) <= DRAG_THRESHOLD:
                return
            self._press['moved'] = True
        self.window().setFrameOrigin_(
            (self._press['wx'] + dx, self._press['wy'] + dy))

    def mouseUp_(self, _event):
        if not self._press:
            return
        moved = self._press['moved']
        double = self._press['double']
        self._press = None
        if moved:
            self._on_move()
        elif double:
            self._on_toggle()

    # 由 Python 侧注入的回调
    def _locked(self):
        return getattr(self, '_is_locked', False)

    def _on_move(self):
        cb = getattr(self, '_move_cb', None)
        if cb:
            cb()

    def _on_toggle(self):
        cb = getattr(self, '_toggle_cb', None)
        if cb:
            cb()


# ---------- 主应用 ----------

class AppDelegate(AppKit.NSObject):
    def applicationDidFinishLaunching_(self, _notification):
        pass


class CardApp:
    def __init__(self):
        self.locked = False
        self.ttl = 0
        self.size = (CARD_W + 2 * SHADOW, 440)

        frame = Foundation.NSMakeRect(0, 0, *self.size)
        self.window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, AppKit.NSWindowStyleMaskBorderless,
            AppKit.NSBackingStoreBuffered, False)
        self.window.setOpaque_(False)
        self.window.setBackgroundColor_(AppKit.NSColor.clearColor())
        self.window.setHasShadow_(False)
        self.window.setLevel_(Quartz.CGWindowLevelForKey(Quartz.kCGDesktopWindowLevelKey))
        self.window.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces |
            AppKit.NSWindowCollectionBehaviorStationary)
        self.window.setIgnoresMouseEvents_(False)

        self.view = CardView.alloc().initWithFrame_(frame)
        self.image_view = AppKit.NSImageView.alloc().initWithFrame_(frame)
        self.image_view.setImageScaling_(AppKit.NSImageScaleProportionallyUpOrDown)
        self.view.addSubview_(self.image_view)
        self.window.setContentView_(self.view)

        self.view._is_locked = False
        self.view._move_cb = self._save_state
        self.view._toggle_cb = self._toggle

        self._restore_position()
        self.window.orderFrontRegardless()

        self.tick()

    def _restore_position(self):
        x, y = None, None
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.locked = bool(data.get('locked'))
            self.view._is_locked = self.locked
            x, y = data.get('x'), data.get('y')
        except (OSError, ValueError):
            pass

        screen = AppKit.NSScreen.mainScreen().visibleFrame()
        if x is None or y is None:
            x = screen.origin.x + screen.size.width - self.size[0] - 6
            y = screen.origin.y + screen.size.height - self.size[1] - 6
        self.window.setFrameOrigin_((x, y))

    def _toggle(self):
        self.locked = not self.locked
        self.view._is_locked = self.locked
        self._save_state()
        self._render()

    def _save_state(self):
        frame = self.window.frame()
        try:
            with open(STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump({'x': frame.origin.x, 'y': frame.origin.y,
                           'locked': self.locked}, f)
        except OSError:
            pass

    def _render(self):
        state = self._collect()
        img = render_card(self.size, state)
        self.image_view.setImage_(pil_to_nsimage(img))

    def _collect(self):
        disk_pct, disk_sub = disk_usage()
        mem_pct, mem_sub = memory_usage()
        cpu_pct = None
        if psutil is not None:
            cpu_pct = psutil.cpu_percent(interval=None)
        net_down = '↓ —'
        if psutil is not None:
            try:
                io1 = psutil.net_io_counters()
                if self.ttl:
                    io0 = self._prev_io
                    dt = max(0.001, time.time() - self._prev_t)
                    rate = max(0, io1.bytes_recv - io0.bytes_recv) / dt
                    net_down = f'↓ {fmt_rate(rate)}'
                self._prev_io = io1
                self._prev_t = time.time()
            except Exception:
                pass
        cap, status = battery_info()
        bat_sub = f'{status} · 电量计无读数' if cap is None else status

        rows = [
            ('disk', '磁盘', disk_pct, disk_sub, True),
            ('mem', '内存', mem_pct, mem_sub, True),
            ('cpu', 'CPU', cpu_pct, f'{os.cpu_count() or 0} 核', True),
            ('net', '网络', None, '', False),
            ('battery', '电池', cap, bat_sub, True),
        ]
        self.ttl += 1
        return {'locked': self.locked, 'clock': time.strftime('%H:%M'),
                'rows': rows, 'net_down': net_down}

    def tick(self):
        try:
            self._render()
        except Exception as e:
            print(f'刷新失败: {e}')
        Foundation.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            TICK_MS, self, b'tick', None, False)

    def run(self):
        app = AppKit.NSApplication.sharedApplication()
        app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        self.tick()
        app.run()


def main():
    CardApp().run()


if __name__ == '__main__':
    main()
