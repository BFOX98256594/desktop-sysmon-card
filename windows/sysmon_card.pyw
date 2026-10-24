#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""系统状态卡片 —— Windows 10/11 版。

贴在桌面上的状态卡片：磁盘、内存、CPU、网络。
窗口会被挂到资源管理器的 WorkerW（桌面层），因此被所有应用窗口盖住、
只在桌面上显示；可拖动，双击固定/解锁。

依赖：Python 3.8+、Pillow（pip install pillow）
用法：双击运行本文件（.pyw 不会弹黑框），或按 install/windows-autostart.ps1 配置开机自启。

注意：本脚本在 Linux 上编写，未在真实 Windows 上实测。
"""

import ctypes
import ctypes.wintypes as wt
import json
import os
import re
import sys
import time

from PIL import Image, ImageDraw, ImageFilter, ImageFont
import tkinter as tk

# ---------- 尺寸与配色 ----------

CARD_W = 322
SHADOW = 16
INNER_PAD = 22
BAR_W = CARD_W - INNER_PAD * 2
RADIUS = 26
TICK_MS = 1000
DISK_EVERY = 15
DRAG_THRESHOLD = 4
TRANSPARENT = '#010203'   # 色键：这个颜色在窗口里会被抠成透明

COLOR = {
    'disk': (10, 132, 255),
    'mem': (48, 209, 88),
    'cpu': (255, 159, 10),
    'battery': (191, 90, 242),
    'net': (100, 210, 255),
}

FONT_CANDIDATES = [
    r'C:\Windows\Fonts\msyh.ttc',      # 微软雅黑
    r'C:\Windows\Fonts\msyhbd.ttc',
    r'C:\Windows\Fonts\simhei.ttf',
]

STATE_FILE = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')),
                          'sysmon-card.json')

BATTERY_STATUS = {
    'Charging': '充电中',
    'Discharging': '放电中',
    'Full': '已充满',
    'Not charging': '未充电',
    'Unknown': '状态未知',
}


def font(size, bold=False):
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


def disk_usage(path=None):
    path = path or os.environ.get('SystemDrive', 'C:') + '\\'
    try:
        import shutil
        total, used, free = shutil.disk_usage(path)
        avail = free
        return used / total * 100, f'已用 {fmt_size(used)} · 剩 {fmt_size(avail)}'
    except OSError as e:
        return None, f'读取失败 {e}'


def memory_usage():
    try:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [('dwLength', wt.DWORD), ('dwMemoryLoad', wt.DWORD),
                        ('ullTotalPhys', ctypes.c_ulonglong),
                        ('ullAvailPhys', ctypes.c_ulonglong),
                        ('ullTotalPageFile', ctypes.c_ulonglong),
                        ('ullAvailPageFile', ctypes.c_ulonglong),
                        ('ullTotalVirtual', ctypes.c_ulonglong),
                        ('ullAvailVirtual', ctypes.c_ulonglong),
                        ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return None, '读取失败'
        total = stat.ullTotalPhys
        avail = stat.ullAvailPhys
        used = total - avail
        return used / total * 100, f'{fmt_size(used)} / {fmt_size(total)}'
    except Exception as e:
        return None, f'读取失败 {e}'


class _FILETIME(ctypes.Structure):
    _fields_ = [('dwLowDateTime', wt.DWORD), ('dwHighDateTime', wt.DWORD)]


def _filetime_to_int(ft):
    return (ft.dwHighDateTime << 32) | ft.dwLowDateTime


def cpu_times():
    """返回 (idle, kernel, user) 累计时间，用于差值算占用率。"""
    idle, kernel, user = _FILETIME(), _FILETIME(), _FILETIME()
    if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle),
                                                 ctypes.byref(kernel),
                                                 ctypes.byref(user)):
        return None
    return _filetime_to_int(idle), _filetime_to_int(kernel), _filetime_to_int(user)


def net_totals():
    """遍历 GetIfTable2 太麻烦，这里用性能计数器读取总字节数。"""
    try:
        import subprocess
        out = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             "(Get-Counter '\\Network Interface(*)\\Bytes Total/sec' -ErrorAction SilentlyContinue)"
             ".CounterSamples | Measure-Object -Property CookedValue -Sum | "
             "Select-Object -ExpandProperty Sum"],
            capture_output=True, text=True, timeout=3,
            creationflags=0x08000000)  # CREATE_NO_WINDOW
        value = float((out.stdout or '0').strip() or 0)
        return value
    except Exception:
        return None


def battery_info():
    try:
        class SYSTEM_POWER_STATUS(ctypes.Structure):
            _fields_ = [('ACLineStatus', wt.BYTE), ('BatteryFlag', wt.BYTE),
                        ('BatteryLifePercent', wt.BYTE), ('Reserved1', wt.BYTE),
                        ('BatteryLifeTime', wt.DWORD), ('BatteryFullLifeTime', wt.DWORD)]

        st = SYSTEM_POWER_STATUS()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):
            return None, '读取失败'
        if st.BatteryLifePercent == 255:
            return None, '未检测到电池'
        if st.ACLineStatus == 1:
            status = '充电中'
        else:
            status = '放电中'
        return int(st.BatteryLifePercent), status
    except Exception as e:
        return None, f'读取失败 {e}'


# ---------- 绘制 ----------

def round_rect(draw, box, radius, **kw):
    draw.rounded_rectangle(box, radius=radius, **kw)


def render_card(size, state):
    """把整张卡片画成一张 RGBA 图。"""
    w, h = size
    img = Image.new('RGBA', (w, h), (0, 0, 0, 0))

    # 阴影
    sh = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    sd.rounded_rectangle([SHADOW - 2, SHADOW + 2, w - SHADOW + 2, h - SHADOW + 4],
                         radius=RADIUS + 4, fill=(0, 0, 0, 120))
    sh = sh.filter(ImageFilter.GaussianBlur(9))
    img = Image.alpha_composite(img, sh)

    # 卡片底色：固定后描边消失，未固定时黄色描边
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

    f14 = font(14)
    f16 = font(16)
    f16b = font(16, True)
    f13 = font(13)

    # 标题行
    d.ellipse([x0, y + 5, x0 + 9, y + 14], fill=(255, 159, 10, 255) if not state['locked']
              else (48, 209, 88, 255))
    d.text((x0 + 19, y), '系统状态', font=f14, fill=(168, 168, 172, 255))
    d.text((x1, y), state['clock'], font=f14, fill=(112, 112, 116, 255), anchor='ra')
    y += 30

    # 指标
    for key, label, pct, sub, has_bar in state['rows']:
        d.text((x0, y), label, font=f16, fill=(224, 224, 228, 255))
        if key == 'net':
            d.text((x1, y), state['net_down'], font=f16b, fill=COLOR['net'], anchor='ra')
        elif pct is None:
            d.text((x1, y), '—', font=f16b, fill=COLOR.get(key, (200, 200, 200)), anchor='ra')
        else:
            d.text((x1, y), f'{round(pct)}%', font=f16b,
                   fill=COLOR.get(key, (200, 200, 200)), anchor='ra')

        y += 24
        if has_bar:
            track = [x0, y, x0 + BAR_W, y + 6]
            round_rect(d, track, 3, fill=(255, 255, 255, 40))
            if pct:
                fw = max(6, int(BAR_W * pct / 100))
                round_rect(d, [x0, y, x0 + fw, y + 6], 3, fill=COLOR.get(key, (200, 200, 200)))
            y += 14

        if sub:
            d.text((x0, y), sub, font=f13, fill=(150, 150, 155, 255))
            y += 20
        y += 4

    return img


# ---------- Windows 桌面层（WorkerW） ----------

user32 = ctypes.windll.user32


def find_workerw():
    """让 Explorer 生成 WorkerW，并返回桌面层窗口句柄。"""
    progman = user32.FindWindowW('Progman', None)
    result = wt.DWORD()
    # 0x052C: 让 Progman 创建 WorkerW
    user32.SendMessageTimeoutW(progman, 0x052C, 0, 0, 0x0, 1000, ctypes.byref(result))

    workerw = wt.HWND(0)
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def _enum(hwnd, _lparam):
        if user32.FindWindowExW(hwnd, 0, 'SHELLDLL_DefView', None):
            found.append(user32.FindWindowExW(0, hwnd, 'WorkerW', None))
        return True

    user32.EnumWindows(_enum, 0)
    if found and found[0]:
        workerw = found[0]
    return workerw or None


def bind_to_desktop(hwnd):
    """把窗口挂进桌面层；失败就退化成「压到所有窗口底部」。"""
    workerw = find_workerw()
    GWL_EXSTYLE = -20
    WS_EX_NOACTIVATE = 0x08000000
    WS_EX_TOOLWINDOW = 0x00000080
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)

    if workerw:
        user32.SetParent(hwnd, workerw)
        return True

    HWND_BOTTOM = 1
    SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
    user32.SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0,
                        SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
    return False


# ---------- 主窗口 ----------

class CardApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', False)
        self.root.configure(bg=TRANSPARENT)
        try:
            self.root.wm_attributes('-transparentcolor', TRANSPARENT)
        except tk.TclError:
            pass

        self.locked = False
        self._press = None
        self._prev_cpu = None
        self._prev_net = None
        self._last_clock = None
        self._tick_index = 0
        self._card_w = CARD_W + 2 * SHADOW
        self._card_h = 440

        self.label = tk.Label(self.root, bd=0, bg=TRANSPARENT)
        self.label.pack()

        self._load_state()
        self.root.geometry(f'{self._card_w}x{self._card_h}+{self._pos[0]}+{self._pos[1]}')

        self.label.bind('<Button-1>', self._on_press)
        self.label.bind('<B1-Motion>', self._on_motion)
        self.label.bind('<ButtonRelease-1>', self._on_release)
        self.label.bind('<Double-Button-1>', self._on_double)

        self.root.update_idletasks()
        hwnd = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
        self.hwnd = hwnd
        try:
            bind_to_desktop(hwnd)
        except Exception as e:
            print(f'桌面层绑定失败: {e}', file=sys.stderr)

        self.tick()

    def _on_press(self, event):
        self._press = {'x': event.x_root, 'y': event.y_root,
                       'ax': self.root.winfo_x(), 'ay': self.root.winfo_y(),
                       'moved': False}

    def _on_motion(self, event):
        if not self._press or self.locked:
            return
        dx = event.x_root - self._press['x']
        dy = event.y_root - self._press['y']
        if not self._press['moved']:
            if abs(dx) <= DRAG_THRESHOLD and abs(dy) <= DRAG_THRESHOLD:
                return
            self._press['moved'] = True
        self.root.geometry(f'+{self._press["ax"] + dx}+{self._press["ay"] + dy}')

    def _on_release(self, _event):
        if self._press and self._press['moved']:
            self._save_state()
        self._press = None

    def _on_double(self, _event):
        self.locked = not self.locked
        self._save_state()
        self.tick()

    def _load_state(self):
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.locked = bool(data.get('locked'))
            self._pos = (int(data.get('x', 40)), int(data.get('y', 40)))
        except (OSError, ValueError):
            sw = self.root.winfo_screenwidth()
            self._pos = (sw - self._card_w - 6, 6)

    def _save_state(self):
        try:
            data = {'x': self.root.winfo_x(), 'y': self.root.winfo_y(),
                    'locked': self.locked}
            with open(STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f)
        except OSError:
            pass

    def tick(self):
        self._tick_index += 1

        # 磁盘
        disk_pct, disk_sub = disk_usage()

        # 内存
        mem_pct, mem_sub = memory_usage()

        # CPU
        cpu_pct = 0
        sample = cpu_times()
        if sample:
            idle, kernel, user = sample
            if self._prev_cpu:
                d_idle = idle - self._prev_cpu[0]
                d_all = (kernel - self._prev_cpu[1]) + (user - self._prev_cpu[2])
                if d_all > 0:
                    cpu_pct = max(0.0, min(100.0, (1 - d_idle / d_all) * 100))
            self._prev_cpu = sample

        # 网络（总速率）
        net_rate = net_totals()
        net_down = f'↓ {fmt_rate(net_rate)}' if net_rate is not None else '↓ —'

        # 电池
        cap, status = battery_info()
        if cap is None:
            bat_sub = f'{status} · 电量计无读数'
        else:
            bat_sub = status

        clock = time.strftime('%H:%M')

        import os as _os
        rows = [
            ('disk', '磁盘', disk_pct, disk_sub, True),
            ('mem', '内存', mem_pct, mem_sub, True),
            ('cpu', 'CPU', cpu_pct, f'{_os.cpu_count() or 0} 核', True),
            ('net', '网络', None, '', False),
            ('battery', '电池', cap, bat_sub, True),
        ]
        state = {'locked': self.locked, 'clock': clock, 'rows': rows,
                 'net_down': net_down}

        img = render_card((self._card_w, self._card_h), state)
        try:
            from PIL import ImageTk
            self._photo = ImageTk.PhotoImage(img)
            self.label.configure(image=self._photo)
        except Exception as e:
            print(f'绘制失败: {e}', file=sys.stderr)

        self.root.after(TICK_MS, self.tick)

    def run(self):
        self.root.mainloop()


if __name__ == '__main__':
    CardApp().run()
