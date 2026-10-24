#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桌面系统状态卡片 —— 桌面窗口（XWayland + DESKTOP 类型 + 主动 lower）。

和桌面图标同层：窗口栈最底、被应用窗口盖住、能拖动、单击固定。
背景与进度条全部用 Cairo 自绘（不依赖 GTK CSS 的 background 绘制）。
"""

import json
import math
import os
import re
import sys

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402
from PIL import Image, ImageDraw, ImageFilter  # noqa: E402

try:
    from PIL import Image as _PILImage
    _PIL_OK = True
except ImportError:  # pragma: no cover
    _PIL_OK = False

CARD_W = 322
SHADOW = 16
INNER_PAD = 22
BAR_W = CARD_W - INNER_PAD * 2
RADIUS = 26
TICK_MS = 1000
DISK_EVERY = 15
DRAG_THRESHOLD = 4

STATE_FILE = os.path.join(GLib.get_user_config_dir(), 'sysmon-card.json')

COLOR = {
    'disk': (10, 132, 255),
    'mem': (48, 209, 88),
    'cpu': (255, 159, 10),
    'battery': (191, 90, 242),
    'net': (100, 210, 255),
}

CSS = """
.title { font-size: 14px; font-weight: bold; color: rgba(255,255,255,0.66); }
.clock { font-size: 14px; font-weight: bold; color: rgba(255,255,255,0.40); }
.row-label { font-size: 16px; color: rgba(255,255,255,0.86); }
.row-value { font-size: 16px; font-weight: bold; }
.row-sub   { font-size: 13px; color: rgba(255,255,255,0.55); }
.val-disk { color: #0a84ff; }
.val-mem { color: #30d158; }
.val-cpu { color: #ff9f0a; }
.val-battery { color: #bf5af2; }
.val-net { color: #64d2ff; }
"""

BATTERY_STATUS = {
    'Charging': '充电中',
    'Discharging': '放电中',
    'Full': '已充满',
    'Not charging': '未充电',
    'Unknown': '状态未知',
}


# ---------- 数据 ----------

def read_text(path):
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()
    except OSError:
        return None


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
    text = read_text('/proc/meminfo')
    if not text:
        return None, '读取失败'
    info = {}
    for line in text.splitlines():
        m = re.match(r'^(\w+):\s+(\d+)\s*kB', line)
        if m:
            info[m.group(1)] = int(m.group(2)) * 1024
    total = info.get('MemTotal', 0)
    avail = info.get('MemAvailable', info.get('MemFree', 0))
    if total <= 0:
        return None, '读取失败'
    used = total - avail
    return used / total * 100, f'{fmt_size(used)} / {fmt_size(total)}'


def cpu_sample():
    text = read_text('/proc/stat')
    if not text:
        return None
    nums = [int(x) for x in text.splitlines()[0].split()[1:]]
    idle = (nums[3] if len(nums) > 3 else 0) + (nums[4] if len(nums) > 4 else 0)
    return idle, sum(nums)


def net_sample():
    text = read_text('/proc/net/dev')
    if not text:
        return None
    rx = tx = 0
    for line in text.splitlines()[2:]:
        if ':' not in line:
            continue
        name, rest = line.split(':', 1)
        if name.strip() == 'lo':
            continue
        f = rest.split()
        if len(f) < 9:
            continue
        rx += int(f[0])
        tx += int(f[8])
    return rx, tx


def battery_info():
    base = '/sys/class/power_supply'
    try:
        for name in os.listdir(base):
            path = os.path.join(base, name)
            if (read_text(os.path.join(path, 'type')) or '').strip() != 'Battery':
                continue
            cap_raw = (read_text(os.path.join(path, 'capacity')) or '').strip()
            status_raw = (read_text(os.path.join(path, 'status')) or '').strip()
            status = BATTERY_STATUS.get(status_raw, status_raw or '状态未知')
            cap = int(cap_raw) if cap_raw.isdigit() else None
            return cap, status
    except OSError:
        pass
    return None, '未检测到电池'


# ---------- 绘图（PIL 生成位图，这台机器没有 gi-cairo） ----------

def to_pixbuf(img):
    data = img.tobytes()
    return GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(data), GdkPixbuf.Colorspace.RGB, True, 8,
        img.width, img.height, img.width * 4)


def make_card_bg(w, h, locked=False, scale=1):
    """圆角卡片 + 柔和阴影，返回 RGBA 图（w/h 为物理像素）。"""
    if w <= (2 * SHADOW + 8) * scale or h <= (2 * SHADOW + 8) * scale:
        return Image.new('RGBA', (max(w, 1), max(h, 1)), (0, 0, 0, 0))

    pad = SHADOW * scale
    radius = RADIUS * scale
    img = Image.new('RGBA', (w, h), (0, 0, 0, 0))

    # 阴影：先画一块，再高斯模糊
    sh = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    sd.rounded_rectangle(
        [pad - 2 * scale, pad + 2 * scale, w - pad + 2 * scale, h - pad + 4 * scale],
        radius=radius + 4 * scale, fill=(0, 0, 0, 120))
    sh = sh.filter(ImageFilter.GaussianBlur(9 * scale))
    img = Image.alpha_composite(img, sh)

    card = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    fill = (30, 30, 32, 245) if locked else (30, 30, 32, 238)
    box = [pad, pad, w - pad, h - pad]
    if locked:
        # 固定了：描边消失，只剩干净的卡片
        cd.rounded_rectangle(box, radius=radius, fill=fill)
    else:
        # 还没固定：黄色描边提醒「这个还能拖」
        cd.rounded_rectangle(box, radius=radius, fill=fill,
                             outline=(255, 159, 10, 200), width=2 * scale)
    return Image.alpha_composite(img, card)


def make_bar(width, height, rgb, pct):
    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = height // 2
    d.rounded_rectangle([0, 0, width - 1, height - 1], radius=r,
                        fill=(255, 255, 255, 40))
    if pct and pct > 0:
        fw = max(height, int(width * pct / 100.0))
        fw = min(fw, width)
        d.rounded_rectangle([0, 0, fw - 1, height - 1], radius=r,
                            fill=(rgb[0], rgb[1], rgb[2], 255))
    return img


class Bar(Gtk.Image):
    """进度条：PIL 画轨道 + 填充，尺寸恒定。"""

    def __init__(self, rgb):
        super().__init__()
        self.rgb = rgb
        self.pct = 0
        self.set_size_request(BAR_W, 6)
        self.set_halign(Gtk.Align.START)
        self._render()

    def set_pct(self, pct):
        new = 0 if pct is None else max(0.0, min(100.0, pct))
        if abs(new - self.pct) < 0.5:
            return
        self.pct = new
        self._render()

    def _render(self):
        img = make_bar(BAR_W, 6, self.rgb, self.pct)
        self.set_from_pixbuf(to_pixbuf(img))


# ---------- 界面 ----------

class Meter:
    def __init__(self, key, label, with_bar):
        self.key = key
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.box.set_margin_top(10)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.label = Gtk.Label(label=label)
        self.label.set_xalign(0)
        self.label.get_style_context().add_class('row-label')
        head.pack_start(self.label, True, True, 0)

        self.value = Gtk.Label(label='—')
        self.value.set_xalign(1)
        self.value.get_style_context().add_class('row-value')
        self.value.get_style_context().add_class(f'val-{key}')
        head.pack_end(self.value, False, False, 0)
        self.box.pack_start(head, False, False, 0)

        self.bar = None
        if with_bar:
            self.bar = Bar(COLOR[key])
            self.bar.set_margin_top(7)
            self.box.pack_start(self.bar, False, False, 0)

        self.sub = Gtk.Label(label='')
        self.sub.set_xalign(0)
        self.sub.get_style_context().add_class('row-sub')
        self.sub.set_margin_top(3)
        self.box.pack_start(self.sub, False, False, 0)

    def set(self, pct, sub=None, raw_value=None):
        if raw_value is not None:
            self.value.set_text(raw_value)
        elif pct is None:
            self.value.set_text('—')
        else:
            self.value.set_text(f'{round(pct)}%')
        if self.bar is not None:
            self.bar.set_pct(pct)
        if sub is not None:
            self.sub.set_text(sub)


class CardApp(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.locked = False
        self._press = None
        self._prev_cpu = None
        self._prev_net = None
        self._last_clock = None
        self._tick_index = 0
        self._lower_counter = 0

        self._setup_window()
        self._build_ui()
        self._restore_position()

        GLib.timeout_add(TICK_MS, self._on_tick)
        self.tick()

    # -- 窗口 --

    def _setup_window(self):
        self.set_decorated(False)
        self.set_type_hint(Gdk.WindowTypeHint.DESKTOP)
        self.set_keep_below(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.set_resizable(False)

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)
        self.set_app_paintable(True)

        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK |
                        Gdk.EventMask.BUTTON_RELEASE_MASK |
                        Gdk.EventMask.POINTER_MOTION_MASK)
        self.connect('button-press-event', self._on_press)
        self.connect('button-release-event', self._on_release)
        self.connect('motion-notify-event', self._on_motion)
        self.connect('map-event', self._on_map)
        self.connect('size-allocate', self._on_allocate)

    def _on_map(self, *_):
        GLib.idle_add(self._lower_window)
        return False

    def _lower_window(self):
        """X11 下把窗口压到栈底；ding 的桌面图标就是靠这招待在最底。"""
        gdk_win = self.get_window()
        if gdk_win is not None:
            try:
                gdk_win.lower()
            except Exception:
                pass
        return False

    def _build_ui(self):
        # 用 Fixed 做层叠：先放背景（画在下面），再放内容（画在上面）。
        # 之前用 Overlay 把背景放在叠加层，结果背景把文字盖住了。
        fixed = Gtk.Fixed()
        self.add(fixed)
        self.fixed = fixed

        self.bg = Gtk.Image()
        fixed.put(self.bg, 0, 0)
        self._bg_size = (0, 0)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        fixed.put(root, 0, 0)
        self.root = root

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inner.set_margin_top(SHADOW + 17)
        inner.set_margin_bottom(SHADOW + 18)
        inner.set_margin_start(SHADOW + INNER_PAD)
        inner.set_margin_end(SHADOW + INNER_PAD)
        root.pack_start(inner, True, True, 0)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.title = Gtk.Label(label='系统状态')
        self.title.set_xalign(0)
        self.title.get_style_context().add_class('title')
        header.pack_start(self.title, True, True, 0)

        self.clock = Gtk.Label(label='--:--')
        self.clock.set_xalign(1)
        self.clock.get_style_context().add_class('clock')
        header.pack_end(self.clock, False, False, 0)
        inner.pack_start(header, False, False, 0)

        self.meters = {
            'disk': Meter('disk', '磁盘', True),
            'mem': Meter('mem', '内存', True),
            'cpu': Meter('cpu', 'CPU', True),
        }
        for m in self.meters.values():
            inner.pack_start(m.box, False, False, 0)

        d = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        d.set_margin_top(14)
        inner.pack_start(d, False, False, 0)

        self.meters['net'] = Meter('net', '网络', False)
        self.meters['battery'] = Meter('battery', '电池', True)
        inner.pack_start(self.meters['net'].box, False, False, 0)
        inner.pack_start(self.meters['battery'].box, False, False, 0)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode('utf-8'))
        Gtk.StyleContext.add_provider_for_screen(
            self.get_screen(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _on_allocate(self, _w, alloc):
        if (alloc.width, alloc.height) == self._bg_size or alloc.width <= 0:
            return
        self._bg_size = (alloc.width, alloc.height)
        self._refresh_bg()
        self.bg.set_size_request(alloc.width, alloc.height)

    def _scale(self):
        win = self.get_window()
        try:
            return win.get_scale_factor() if win is not None else 1
        except Exception:
            return 1

    def _refresh_bg(self):
        w, h = self._bg_size
        if w <= 0 or h <= 0:
            return
        try:
            # 没有 gi-cairo，只能用 pixbuf；按逻辑尺寸出图，避免撑大窗口
            self.bg.set_from_pixbuf(to_pixbuf(make_card_bg(w, h, self.locked, 1)))
        except Exception as e:  # 背景画不出来也不能让卡片崩掉
            print(f'背景绘制失败: {e}', file=sys.stderr)

    # -- 拖动 / 固定 --

    def _on_press(self, _w, event):
        if event.button != 1:
            return False
        is_double = event.type == Gdk.EventType._2BUTTON_PRESS
        x, y = self.get_position()
        self._press = {'rx': event.x_root, 'ry': event.y_root,
                       'wx': x, 'wy': y, 'moved': False, 'double': is_double}
        return True

    def _on_motion(self, _w, event):
        if not self._press or self.locked:
            return False
        dx = event.x_root - self._press['rx']
        dy = event.y_root - self._press['ry']
        if not self._press['moved']:
            if abs(dx) <= DRAG_THRESHOLD and abs(dy) <= DRAG_THRESHOLD:
                return True
            self._press['moved'] = True
        self.move(int(self._press['wx'] + dx), int(self._press['wy'] + dy))
        return True

    def _on_release(self, _w, _event):
        if not self._press:
            return False
        moved = self._press['moved']
        double = self._press['double']
        self._press = None
        if moved:
            self._save_state()          # 拖动：记住位置
        elif double:
            self._set_locked(not self.locked)   # 双击：固定 / 解锁
        return True

    def _set_locked(self, locked):
        self.locked = locked
        self._refresh_bg()
        self._save_state()

    # -- 位置 --

    def _load_state(self):
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _restore_position(self):
        state = self._load_state()
        if state.get('locked'):
            self.locked = True
        if 'x' in state and 'y' in state:
            GLib.idle_add(self._place, int(state['x']), int(state['y']))
        else:
            GLib.idle_add(self._place_default)

    def _place(self, x, y):
        self.move(x, y)
        return False

    def _place_default(self):
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor_at_point(0, 0)
        if monitor:
            geo = monitor.get_workarea()
            w = CARD_W + 2 * SHADOW
            self.move(geo.x + geo.width - w - 6, geo.y + 6)
        return False

    def _save_state(self):
        try:
            x, y = self.get_position()
            with open(STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump({'x': x, 'y': y, 'locked': self.locked}, f)
        except OSError:
            pass

    # -- 刷新 --

    def _on_tick(self):
        self.tick()
        self._lower_counter += 1
        if self._lower_counter % 2 == 0:   # 每 2 秒压一次栈底，防止被挤上来
            self._lower_window()
        return True

    def tick(self):
        self._update_clock()
        self._update_memory()
        self._update_cpu()
        self._update_net()
        self._update_battery()
        if self._tick_index % DISK_EVERY == 0:
            self._update_disk()
        self._tick_index += 1

    def _update_clock(self):
        text = GLib.DateTime.new_now_local().format('%H:%M')
        if text != self._last_clock:
            self._last_clock = text
            self.clock.set_text(text)

    def _update_disk(self):
        pct, sub = disk_usage()
        self.meters['disk'].set(pct, sub)

    def _update_memory(self):
        pct, sub = memory_usage()
        self.meters['mem'].set(pct, sub)

    def _update_cpu(self):
        sample = cpu_sample()
        if sample is None:
            self.meters['cpu'].set(None, '读取失败')
            return
        idle, total = sample
        cores = os.cpu_count() or 0
        if self._prev_cpu is None:
            self._prev_cpu = (idle, total)
            self.meters['cpu'].set(0, f'{cores} 核')
            return
        d_idle = idle - self._prev_cpu[0]
        d_total = total - self._prev_cpu[1]
        self._prev_cpu = (idle, total)
        pct = (1 - d_idle / d_total) * 100 if d_total > 0 else 0
        self.meters['cpu'].set(pct, f'{cores} 核')

    def _update_net(self):
        sample = net_sample()
        if sample is None:
            self.meters['net'].set(None, '读取失败')
            return
        now = GLib.get_monotonic_time() / 1e6
        if self._prev_net is None:
            self._prev_net = (sample[0], sample[1], now)
            self.meters['net'].set(None, '正在采样', raw_value='…')
            return
        prx, ptx, pt = self._prev_net
        self._prev_net = (sample[0], sample[1], now)
        dt = now - pt
        if dt <= 0:
            return
        down = max(0, (sample[0] - prx) / dt)
        up = max(0, (sample[1] - ptx) / dt)
        self.meters['net'].set(None, f'↑ {fmt_rate(up)}', raw_value=f'↓ {fmt_rate(down)}')

    def _update_battery(self):
        cap, status = battery_info()
        if cap is None:
            self.meters['battery'].set(None, f'{status} · 电量计无读数')
        else:
            self.meters['battery'].set(cap, status)


def main():
    ok, _argv = Gtk.init_check([])
    if not ok:
        print('GTK 初始化失败', file=sys.stderr)
        return 1
    app = CardApp()
    app.show_all()
    Gtk.main()
    return 0


if __name__ == '__main__':
    sys.exit(main())
