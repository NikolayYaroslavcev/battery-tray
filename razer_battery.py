"""Battery level of wireless Razer headsets, mice and keyboards on the Windows taskbar.

Protocol by what the HID report descriptor offers:
  output report 2    PA frames (Barracuda X 0550, BlackShark V2 Pro 2023)
  output report 1    PA frames, Barracuda Pro variant
  feature report FF  state blob (Barracuda X 0536, Nari)
  feature, no id     classic 90-byte report, class 07 (mice, keyboards, BlackShark V2 Pro 2020)
"""
import ctypes, ctypes.wintypes as wt, json, os, sys, threading, time, tkinter as tk, tkinter.font as tkfont, winreg
from pathlib import Path

import hid

VID = 0x1532
HEADSET_PIDS = {0x0528, 0x0536, 0x053A, 0x0550, 0x0552, 0x0555, 0x0556, 0x0557, 0x1014}
HEADSET_WORDS = ("headset", "barracuda", "blackshark", "kraken", "nari")
# wireless mice and their receivers, from OpenRazer (list taken from HaloBattery, MIT)
MOUSE_PIDS = {0x001F, 0x0024, 0x0025, 0x0032, 0x003E, 0x003F, 0x0044, 0x0045, 0x0059, 0x005A, 0x0062, 0x006F,
              0x0070, 0x0072, 0x0073, 0x0077, 0x0080, 0x007A, 0x007B, 0x007C, 0x007D, 0x0083, 0x0086, 0x0088,
              0x008F, 0x0090, 0x0094, 0x009A, 0x009C, 0x009E, 0x009F, 0x00A4, 0x00A5, 0x00A6, 0x00A7, 0x00A8,
              0x00AA, 0x00AB, 0x00AF, 0x00B0, 0x00B3, 0x00B4, 0x00B6, 0x00B7, 0x00B8, 0x00B9, 0x00BE, 0x00BF,
              0x00C0, 0x00C1, 0x00C2, 0x00C3, 0x00C4, 0x00C5, 0x00C7, 0x00C8, 0x00CC, 0x00CD, 0x00D0, 0x00D1,
              0x00D3, 0x00D4, 0x00D6, 0x00D7}
MOUSE_WORDS = ("mouse", "viper", "deathadder", "basilisk", "naga", "orochi", "mamba", "lancehead", "atheris",
               "pro click", "cobra")
KEYBOARD_WORDS = ("keyboard", "blackwidow", "deathstalker", "ornata", "pro type ultra", "cynosa")
WIRELESS_WORDS = ("hyperspeed", "wireless", "receiver", "dongle", "dock", " pro")
POLL_S = 60
LOW_LEVELS = (20, 10)
APP = "RazerBatteryIndicator"
CFG = Path(os.environ["APPDATA"]) / APP / "config.json"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


# --- protocols: take an open hid.device, return (percent, charging) or None ---

def _drain(h):
    for _ in range(64):
        if not h.read(64, 5):
            break

def _pi_value(r, param):
    # reply: "PI" class seq tick*3 00 04 00 param 01 01 value
    i = bytes(r).find(b"PI")
    if i >= 0 and len(r) > i + 13 and r[i + 10] == param and r[i + 11] == 1:
        return r[i + 13]
    return None

def _pa_read(h, frame, params, before=None):
    def ask(p):
        for _ in range(3):
            if before:
                h.write(before)       # remote mode, also wakes the radio link
                time.sleep(0.035)
            h.write(frame(p))
            end = time.monotonic() + 0.3
            while time.monotonic() < end:
                v = _pi_value(h.read(64, 50), p)
                if v is not None:
                    return v
        return None
    _drain(h)
    level = ask(params[0])
    if level is None:
        return None
    charging = ask(params[1])
    return min(level, 100), None if charging is None else bool(charging)

def _pa2_frame(length, kind, typ, cmd, arg=0):
    b = bytearray(64)
    b[0], b[1], b[2], b[5], b[6], b[7], b[9], b[10], b[11] = 2, 0x80, length, 0x50, 0x41, kind, typ, cmd, arg
    return bytes(b)

def proto_pa2(h):
    try:
        return _pa_read(h, lambda p: _pa2_frame(8, 0x08, 3, p), (0x21, 0x2A), _pa2_frame(7, 0x0E, 2, 0xE1, 1))
    finally:
        try:
            h.write(_pa2_frame(7, 0x0E, 2, 0xE1, 0))
        except (OSError, ValueError):
            pass

def proto_pa1(h):
    return _pa_read(h, lambda p: bytes([1, 0x80, 8, 0x50, 0x41, 8, 8, 3, p, 0, 0]) + bytes(53), (0x21, 0x2A))

BLOB_QUERY = bytes([0xFF, 0x0A, 0x00, 0xFD, 0x04, 0x12, 0xF1, 0x02, 0x05]) + bytes(55)

def proto_blob(h):
    h.send_feature_report(BLOB_QUERY)
    time.sleep(0.05)
    r = h.get_feature_report(0xFF, 64)
    if len(r) > 14 and r[0] == 0xFF and r[14]:          # r[9]: 03 battery, 05 charging, 06 full
        return min(r[14], 100), r[9] == 0x05
    return None

def _std_query(h, tid, cmd):
    req = bytearray(90)
    req[1], req[5], req[6], req[7] = tid, 2, 0x07, cmd
    for b in req[2:88]:
        req[88] ^= b
    h.send_feature_report(b"\x00" + req)
    for _ in range(10):
        time.sleep(0.06)
        r = list(h.get_feature_report(0, 91))
        d = r[1:91] if len(r) >= 91 else r
        if len(d) < 90:
            return None
        if d[0] == 0x01:                                # busy
            continue
        return d[9] if d[0] == 0x02 and d[6] == 0x07 and d[7] == cmd else None
    return None

def proto_std(h):
    for tid in (0x3F, 0x1F, 0xFF):
        raw = _std_query(h, tid, 0x80)
        if raw:
            charging = _std_query(h, tid, 0x84)
            return round(raw * 100 / 255), None if charging is None else bool(charging)
    return None


def report_kinds(h):
    """Set of ('in' | 'out' | 'feat', report_id) found in the report descriptor."""
    try:
        desc = bytes(h.get_report_descriptor())
    except (OSError, ValueError, AttributeError):
        return set()
    kinds, rid, i = set(), 0, 0
    while i < len(desc):
        b = desc[i]
        n = (0, 1, 2, 4)[b & 3]
        val = int.from_bytes(desc[i + 1:i + 1 + n], "little")
        tag = b & 0xFC
        if tag == 0x84:
            rid = val
        elif tag in (0x80, 0x90, 0xB0):
            kinds.add(({0x80: "in", 0x90: "out", 0xB0: "feat"}[tag], rid))
        i += 1 + n
    return kinds



def protocols_for(kinds, kind):
    table = ((proto_pa2, ("out", 2)), (proto_pa1, ("out", 1)), (proto_blob, ("feat", 0xFF)), (proto_std, ("feat", 0)))
    # mice and keyboards get the classic report only
    return [p for p, k in table if k in kinds and (kind == "headset" or p is proto_std)]


# --- device discovery ---

def classify(d):
    pid, name = d["product_id"], (d.get("product_string") or "").lower()
    if pid in HEADSET_PIDS or any(w in name for w in HEADSET_WORDS):
        return "headset"
    if pid in MOUSE_PIDS:
        return "mouse"
    if not any(w in name for w in WIRELESS_WORDS):  # wired devices answer with garbage
        return None
    if any(w in name for w in KEYBOARD_WORDS):
        return "keyboard"
    if any(w in name for w in MOUSE_WORDS) or any(w in name for w in ("receiver", "dongle", "dock")):
        return "mouse"
    return None

def nice_name(d):
    name = (d.get("product_string") or "").replace(" 2.4", "").replace(" Dongle", "").strip()
    return name if name.lower().startswith("razer") else f"Razer {name or 'device'}"


_working = {}     # pid -> (path, protocol) that answered last time


def _try(pid, d, kind, protos=None):
    h = hid.device()
    try:
        h.open_path(d["path"])
    except (OSError, ValueError):
        return None
    try:
        for proto in protos or protocols_for(report_kinds(h), kind):
            try:
                res = proto(h)
            except (OSError, ValueError):
                continue
            if res:
                _working[pid] = (d["path"], proto)
                return res
        return None
    finally:
        h.close()


def read_all():
    """[(pid, kind, name, state)], state is 'offline' or (percent, charging)."""
    groups = {}
    for d in hid.enumerate(VID):
        if classify(d):
            groups.setdefault(d["product_id"], []).append(d)
    out = []
    for pid, cands in groups.items():
        kind, name = classify(cands[0]), nice_name(cands[0])
        known = _working.get(pid)
        cached = next((d for d in cands if known and d["path"] == known[0]), None)
        if cached:                    # don't re-probe everything while the device sleeps
            state = _try(pid, cached, kind, [known[1]]) or "offline"
        else:
            state = next((r for r in (_try(pid, d, kind) for d in cands) if r), "offline")
        out.append((pid, kind, name, state))
    return out


# --- settings, autostart ---

def load_cfg():
    try:
        cfg = json.loads(CFG.read_text("utf-8"))
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}

def save_cfg(cfg):
    try:
        CFG.parent.mkdir(parents=True, exist_ok=True)
        CFG.write_text(json.dumps(cfg), "utf-8")
    except OSError:
        pass

def _run_cmd():
    if getattr(sys, "frozen", False):          # pyinstaller exe
        return f'"{sys.executable}"'
    return f'"{Path(sys.executable).with_name("pythonw.exe")}" "{Path(__file__).resolve()}"'

def autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return winreg.QueryValueEx(k, APP)[0] == _run_cmd()
    except OSError:
        return False

def set_autostart(on):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP, 0, winreg.REG_SZ, _run_cmd())
        else:
            try:
                winreg.DeleteValue(k, APP)
            except OSError:
                pass


# --- win32 ---

u32, gdi = ctypes.windll.user32, ctypes.windll.gdi32
for f in (u32.FindWindowW, u32.FindWindowExW, u32.GetForegroundWindow, u32.GetParent):
    f.restype = wt.HWND
u32.FindWindowExW.argtypes = [wt.HWND, wt.HWND, wt.LPCWSTR, wt.LPCWSTR]
u32.GetParent.argtypes = [wt.HWND]
u32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
u32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
u32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, wt.LONG]
u32.SetWindowPos.argtypes = [wt.HWND, wt.HWND] + [ctypes.c_int] * 4 + [wt.UINT]
u32.GetDC.restype, u32.GetDC.argtypes = wt.HDC, [wt.HWND]
u32.GetAsyncKeyState.restype, u32.GetAsyncKeyState.argtypes = ctypes.c_short, [ctypes.c_int]
u32.GetDpiForWindow.restype, u32.GetDpiForWindow.argtypes = wt.UINT, [wt.HWND]
u32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
gdi.GetPixel.restype, gdi.GetPixel.argtypes = wt.DWORD, [wt.HDC, ctypes.c_int, ctypes.c_int]


def inside(g, pt, margin=0):
    x, y, w, h = g
    return x - margin <= pt.x < x + w + margin and y - margin <= pt.y < y + h + margin

def round_rect(c, x0, y0, x1, y1, r, fill):
    c.create_rectangle(x0 + r, y0, x1 - r, y1, fill=fill, outline="")
    c.create_rectangle(x0, y0 + r, x1, y1 - r, fill=fill, outline="")
    for cx, cy in ((x0, y0), (x1 - 2 * r, y0), (x0, y1 - 2 * r), (x1 - 2 * r, y1 - 2 * r)):
        c.create_oval(cx, cy, cx + 2 * r, cy + 2 * r, fill=fill, outline="")

def rect(hwnd):
    r = wt.RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom

def screen_pixel(x, y):
    dc = u32.GetDC(None)
    c = gdi.GetPixel(dc, x, y)
    u32.ReleaseDC(None, dc)
    return "#000000" if c == 0xFFFFFFFF else f"#{c & 255:02x}{c >> 8 & 255:02x}{c >> 16 & 255:02x}"

def is_light(color):
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.299 * r + 0.587 * g + 0.114 * b > 140

def fullscreen_app_active():
    fg = u32.GetForegroundWindow()
    if not fg:
        return False
    buf = ctypes.create_unicode_buffer(64)
    u32.GetClassNameW(fg, buf, 64)
    if buf.value in ("Progman", "WorkerW", "Shell_TrayWnd"):
        return False
    l, t, r, b = rect(fg)
    return l <= 0 and t <= 0 and r >= u32.GetSystemMetrics(0) and b >= u32.GetSystemMetrics(1)

def overlay_style(hwnd):
    # no taskbar button, no focus stealing; tk drops these bits on some attribute changes
    u32.SetWindowLongW(hwnd, -20, u32.GetWindowLongW(hwnd, -20) | 0x80 | 0x08000000)  # TOOLWINDOW|NOACTIVATE

def make_overlay(win):
    win.update_idletasks()
    hwnd = u32.GetParent(win.winfo_id())
    overlay_style(hwnd)
    return hwnd

def dwm_style(hwnd, dark):
    # rounded corners + dark frame on Windows 11, no-op on 10
    for attr, val in ((33, 2), (20, int(dark))):    # DWMWA_WINDOW_CORNER_PREFERENCE, DWMWA_USE_IMMERSIVE_DARK_MODE
        v = ctypes.c_int(val)
        try:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), 4)
        except OSError:
            pass

def put_on_top(hwnd, g=None):
    flags = 0x10 if g else 0x1 | 0x2 | 0x10                          # SWP_NOACTIVATE [| NOSIZE | NOMOVE]
    u32.SetWindowPos(hwnd, wt.HWND(-1), *(g or (0, 0, 0, 0)), flags)


# --- UI ---

GREEN, GREEN_DIM, AMBER, RED, GREY = "#30d158", "#1f7a37", "#ffd60a", "#ff453a", "#8e8e93"
BOLT = "\ue945"
ICONS = {"headset": "\ue7f6", "mouse": "\ue962", "keyboard": "\ue765"}   # dict order = display order
OFFLINE_TEXT = {"headset": "Наушники выключены или вне зоны", "mouse": "Мышь выключена или спит",
                "keyboard": "Клавиатура выключена или спит"}


def fmt_hours(h):
    return f"~{h:.0f} ч" if h >= 1.5 else f"~{max(1, round(h * 60)):.0f} мин"

def level_color(st, fg):
    if not isinstance(st, tuple):
        return GREY
    level, charging = st
    return fg if charging or level > 35 else AMBER if level > 20 else RED


class Device:
    def __init__(self, pid, kind, name):
        self.pid, self.kind, self.name = pid, kind, name
        self.state, self.hist, self.alerted, self.seen_at = None, [], set(), 0


class App:
    def __init__(self):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            pass
        self.cfg = load_cfg()
        self.root = r = tk.Tk()
        self.s = r.winfo_fpixels("1i") / 96
        fams = set(tkfont.families())
        self.icon_font = "Segoe Fluent Icons" if "Segoe Fluent Icons" in fams else "Segoe MDL2 Assets"
        var = "Segoe UI Variable Text" in fams                  # Win 11 font
        text, semi = ("Segoe UI Variable Text", "Segoe UI Variable Text Semibold") if var else ("Segoe UI", "Segoe UI Semibold")
        big = "Segoe UI Variable Display Semib" if var else "Segoe UI Semibold"
        self.fonts = {"f_text": (text, 13), "f_name": (semi, 14), "f_big": (big, 20), "f_sub": (text, 12),
                      "f_small": (text, 11)}
        for attr, (family, size) in self.fonts.items():
            setattr(self, attr, tkfont.Font(family=family, size=-self.px(size)))
        self.card_theme = self.menu_theme = None
        self.notch_now = False

        r.overrideredirect(True)
        self.c = tk.Canvas(r, highlightthickness=0, bd=0)
        self.c.pack(fill="both", expand=True)
        self.card = tk.Toplevel(r)
        self.card.overrideredirect(True)
        self.cc = tk.Canvas(self.card, highlightthickness=0, bd=0)
        self.cc.pack(fill="both", expand=True)
        self.card.withdraw()
        # keyed-out pixels let clicks through, so a nearly invisible window on top takes them
        self.hit = tk.Toplevel(r, bg="black")
        self.hit.overrideredirect(True)
        self.hit.attributes("-alpha", 0.01)
        # custom menu: the native one can't be styled
        self.pm = tk.Toplevel(r)
        self.pm.overrideredirect(True)
        self.pc = tk.Canvas(self.pm, highlightthickness=0, bd=0)
        self.pc.pack(fill="both", expand=True)
        self.pm.withdraw()
        self.hwnd, self.card_hwnd, self.hit_hwnd = make_overlay(r), make_overlay(self.card), make_overlay(self.hit)
        self.pm_hwnd = make_overlay(self.pm)

        self.devices = {}                                   # pid -> Device
        self.answered = set()                               # pids that ever returned a level
        self.menu_token = 0
        self.result, self.handled, self.updated = None, None, None
        self.alert = None                                   # (pid, text, color, hide_at)
        self.hover, self.menu_open, self.phase = False, False, 0
        self.bg, self.bg_checked = "#1c1c1c", 0
        self.wake = threading.Event()

        self.mode = tk.StringVar(value=self.cfg.get("mode", "taskbar"))
        self.autostart = autostart_enabled()
        # (icon, label, action, checked); None is a separator
        self.menu_items = [
            ("\ue72c", "Обновить", self.wake.set, None),
            None,
            (None, "На панели задач", lambda: self.set_mode("taskbar"), lambda: self.mode.get() == "taskbar"),
            (None, "Чёлка сверху", lambda: self.set_mode("notch"), lambda: self.mode.get() == "notch"),
            None,
            (None, "Запускать с Windows", self.toggle_autostart, lambda: self.autostart),
            ("\ue7e8", "Выход", r.destroy, None),
        ]
        self.menu_rows, self.menu_hover, self.menu_geom, self.card_geom = [], -1, None, None
        self.hit.bind("<Button-1>", lambda e: self.wake.set())
        self.hit.bind("<Button-3>", self.open_menu)
        self.cc.bind("<Button-1>", lambda e: self.wake.set())
        self.cc.bind("<Button-3>", self.open_menu)
        self.pc.bind("<Motion>", lambda e: self.menu_hover_at(e.y))
        self.pc.bind("<Leave>", lambda e: self.menu_hover_at(-1))
        self.pc.bind("<Button-1>", lambda e: self.menu_click(e.y))

        threading.Thread(target=self.poll_loop, daemon=True).start()
        self.tick()

    def px(self, v):
        return int(round(v * self.s))


    def poll_loop(self):
        while True:
            try:
                self.result = read_all()      # picked up by tick() on the UI thread
            except Exception:
                pass
            self.updated = time.strftime("%H:%M")
            self.wake.wait(POLL_S)
            self.wake.clear()

    def apply(self, result):
        seen = set()
        for pid, kind, name, state in result:
            if isinstance(state, tuple):
                self.answered.add(pid)
            elif pid not in self.answered and pid not in HEADSET_PIDS | MOUSE_PIDS:
                continue                      # wired headset, nothing to show
            dev = self.devices.get(pid)
            if dev is None:
                dev = self.devices[pid] = Device(pid, kind, name)
                try:                          # continue the measurement from the previous run
                    t0, l0, last = self.cfg.get("track", {}).get(name, (0, 0, 0))
                    if time.time() - float(last) < 600:
                        dev.hist, dev.seen_at = [(float(t0), int(l0))], float(last)
                except (TypeError, ValueError):
                    pass
            if isinstance(state, tuple) and state[1] is None:   # charging read failed, keep last value
                old = dev.state
                state = (state[0], old[1] if isinstance(old, tuple) else False)
            dev.state = state
            seen.add(pid)
            self.track(dev)
        self.devices = {pid: d for pid, d in sorted(self.devices.items(), key=lambda kv: list(ICONS).index(kv[1].kind))
                        if pid in seen}

    def track(self, dev):
        """Discharge rate learning and low/full alerts."""
        now, st = time.time(), dev.state
        if not isinstance(st, tuple):
            self.save_track(dev, [])          # time switched off isn't discharge
            return
        level, charging = st
        gap = now - dev.seen_at > 600         # PC was asleep
        dev.seen_at = now
        for th in LOW_LEVELS:                 # charged behind our back: re-arm
            if level > th + 2:
                dev.alerted.discard(f"low{th}")
        if charging:
            self.save_track(dev, [])
            dev.alerted.difference_update(f"low{t}" for t in LOW_LEVELS)
            if level >= 100 and "full" not in dev.alerted:
                dev.alerted.add("full")
                self.show_alert(dev, "Заряд 100% — можно отключать", GREEN)
            return
        dev.alerted.discard("full")
        if dev.hist and (gap or level > dev.hist[-1][1] + 1):
            dev.hist = []
        if not dev.hist or level != dev.hist[-1][1]:
            dev.hist.append((now, level))
        t0, l0 = dev.hist[0]
        hours, drop = (now - t0) / 3600, l0 - level
        if hours >= 0.5 and drop >= 2:
            rates = self.cfg.setdefault("rates", {})
            old, rate = rates.get(dev.name), drop / hours
            old = old if isinstance(old, (int, float)) and old > 0 else None
            rates[dev.name] = round(rate if old is None else 0.5 * old + 0.5 * rate, 3)
            dev.hist = [(now, level)]
        self.save_track(dev, dev.hist)
        for th in LOW_LEVELS:
            if level <= th and f"low{th}" not in dev.alerted:
                dev.alerted.update(f"low{t}" for t in LOW_LEVELS if t >= level)
                self.show_alert(dev, f"Осталось {level}% — пора на зарядку", RED)
                break

    def save_track(self, dev, hist):
        # kept in the config so a restart doesn't throw the measurement away
        dev.hist = hist
        track = self.cfg.setdefault("track", {})
        if hist:
            track[dev.name] = [hist[0][0], hist[0][1], time.time()]
        elif track.pop(dev.name, None) is None:
            return
        save_cfg(self.cfg)

    def show_alert(self, dev, text, color):
        self.alert = (dev.pid, text, color, None)         # timer starts when it's actually shown

    def set_mode(self, mode):
        self.mode.set(mode)
        self.cfg["mode"] = mode
        save_cfg(self.cfg)

    def toggle_autostart(self):
        self.autostart = not self.autostart
        set_autostart(self.autostart)

    # --- menu ---

    def open_menu(self, e=None):
        geom = self.place()
        if not geom:
            return
        self.menu_open, self.alert, self.menu_hover = True, None, -1
        self.show(self.card, False)
        self.draw_menu(geom)
        self.show(self.pm, True)
        put_on_top(self.pm_hwnd)
        self.menu_token += 1
        self.root.after(150, self.menu_watch, self.menu_token)   # wait for the right button to come up

    def close_menu(self):
        self.menu_open = False
        self.show(self.pm, False)

    def menu_index(self, y):
        return next((i for y0, y1, i in self.menu_rows if y0 <= y < y1), -1)

    def menu_hover_at(self, y):
        i = self.menu_index(y)
        if i != self.menu_hover:
            self.menu_hover = i
            self.draw_menu()

    def menu_click(self, y):
        i = self.menu_index(y)
        if i >= 0:
            self.close_menu()
            self.menu_items[i][2]()

    def menu_watch(self, token):
        # the window never gets focus, so poll for Esc / clicks outside
        if not self.menu_open or token != self.menu_token:
            return
        pt = wt.POINT()
        u32.GetCursorPos(ctypes.byref(pt))
        clicked = any(u32.GetAsyncKeyState(vk) & 0x8000 for vk in (0x01, 0x02, 0x04))
        if u32.GetAsyncKeyState(0x1B) & 0x8000 or clicked and not inside(self.menu_geom, pt):
            self.close_menu()
            return
        self.root.after(40, self.menu_watch, token)


    def tick(self):
        # an exception here would silently freeze the widget under pythonw
        try:
            self.update_ui()
        except Exception:
            import traceback
            CFG.parent.mkdir(parents=True, exist_ok=True)
            (CFG.parent / "error.log").write_text(traceback.format_exc(), "utf-8")
        self.root.after(500, self.tick)

    def update_ui(self):
        res = self.result
        if res is not self.handled:
            self.handled = res
            self.apply(res)
        if self.alert and self.alert[3] and time.time() > self.alert[3]:
            self.alert = None
        dpi = u32.GetDpiForWindow(self.hwnd)
        if dpi and abs(dpi / 96 - self.s) > 0.01:          # scale changed
            self.s = dpi / 96
            for attr, (_, size) in self.fonts.items():
                getattr(self, attr).configure(size=-self.px(size))
        charging = any(isinstance(d.state, tuple) and d.state[1] for d in self.devices.values())
        self.phase = (self.phase + 1) % 2 if charging else 0

        geom = self.place()
        # keyed-out pixels get no mouse events, so check the cursor ourselves
        pt = wt.POINT()
        u32.GetCursorPos(ctypes.byref(pt))
        card_up = self.card.winfo_ismapped() and self.card_geom
        self.hover = bool(geom) and (inside(geom, pt) or bool(card_up and inside(self.card_geom, pt, self.px(12))))
        if geom:
            self.draw_widget(geom)
        self.show(self.root, bool(geom))
        self.show(self.hit, bool(geom))
        if geom:
            for win, hwnd in ((self.root, self.hwnd), (self.hit, self.hit_hwnd)):
                self.set_window(win, geom)
                put_on_top(hwnd, geom)
        if self.menu_open:
            if geom:
                put_on_top(self.pm_hwnd)
            else:
                self.close_menu()
        card = bool(geom) and ((self.hover and not self.menu_open) or self.alert is not None)
        if card:
            self.draw_card(geom)
            put_on_top(self.card_hwnd)
            if self.alert and self.alert[3] is None:
                self.alert = self.alert[:3] + (time.time() + 8,)
        self.show(self.card, card)

    def show(self, win, on):
        if win.winfo_ismapped() != on:
            win.deiconify() if on else win.withdraw()

    def set_key(self, color):
        # only on change: tk resets the window styles every time
        if self.root.attributes("-transparentcolor") != color:
            self.root.attributes("-transparentcolor", color)
            overlay_style(self.hwnd)

    def set_window(self, win, g):
        spec = f"{g[2]}x{g[3]}+{g[0]}+{g[1]}"
        if win.geometry() != spec:
            win.geometry(spec)

    def items(self):
        if not self.devices:
            return [(ICONS["headset"], "…" if self.result is None else "—", None)]
        return [(ICONS[d.kind], f"{d.state[0]}%" if isinstance(d.state, tuple) else "—", d.state)
                for d in self.devices.values()]

    def item_width(self, text, st):
        return self.px(22) + self.f_text.measure(text) + (self.px(14) if isinstance(st, tuple) and st[1] else 0)

    def widget_size(self):
        pad = self.px(18) if self.notch_now else self.px(10)
        w = 2 * pad + sum(self.item_width(t, st) for _, t, st in self.items()) + self.px(12) * (len(self.items()) - 1)
        return w, self.px(32)

    def place(self):
        """Widget rect (x, y, w, h), or None to hide it."""
        if fullscreen_app_active():
            return None
        sw, sh = u32.GetSystemMetrics(0), u32.GetSystemMetrics(1)
        tb = u32.FindWindowW("Shell_TrayWnd", None) if self.mode.get() == "taskbar" else None
        tl, tt, tr, tbm = rect(tb) if tb else (0, 0, 0, 0)
        # taskbar on top / at the side: use the notch instead
        self.notch_now = not (tb and tr - tl > tbm - tt and tt > sh // 2)
        w, h = self.widget_size()
        if self.notch_now:
            return (sw - w) // 2, 0, w, h
        if tt >= sh - self.px(4):                 # auto-hide
            return None
        tn = u32.FindWindowExW(tb, None, "TrayNotifyWnd", None)
        right = rect(tn)[0] if tn else tr - self.px(300)
        x, y, h = right - w - self.px(4), tt, tbm - tt
        if time.time() - self.bg_checked > 5:
            self.bg_checked = time.time()
            # our own background is keyed out, so this is the taskbar's pixel
            self.bg = screen_pixel(x + self.px(2), tt + self.px(2))
        return x, y, w, h

    # --- drawing ---

    def draw_widget(self, geom):
        p, c = self.px, self.c
        notch = self.notch_now
        fg = "#1a1a1a" if not notch and is_light(self.bg) else "#ffffff"
        W, H = geom[2], geom[3]
        cy = H // 2
        c.delete("all")
        if notch:
            c.config(bg="#010203")
            self.set_key("#010203")
            r = p(14)
            c.create_rectangle(0, 0, W, H - r, fill="black", outline="black")
            c.create_rectangle(r, H - r, W - r, H, fill="black", outline="black")
            c.create_oval(0, H - 2 * r, 2 * r, H, fill="black", outline="black")
            c.create_oval(W - 2 * r, H - 2 * r, W, H, fill="black", outline="black")
        else:
            c.config(bg=self.bg)                     # keyed out; antialiased edges blend into the taskbar
            self.set_key(self.bg)
        x = p(18) if notch else p(10)
        for icon, text, st in self.items():
            c.create_text(x, cy, text=icon, font=(self.icon_font, -p(15)), fill=fg, anchor="w")
            c.create_text(x + p(22), cy, text=text, font=self.f_text, fill=level_color(st, fg), anchor="w")
            if isinstance(st, tuple) and st[1]:
                c.create_text(x + p(25) + self.f_text.measure(text), cy, text=BOLT, font=(self.icon_font, -p(11)),
                              fill=GREEN if self.phase == 0 else GREEN_DIM, anchor="w")
            x += self.item_width(text, st) + p(12)

    def status(self, dev, sub):
        if self.alert and self.alert[0] == dev.pid:
            return self.alert[1], self.alert[2]
        st = dev.state
        if not isinstance(st, tuple):
            return OFFLINE_TEXT[dev.kind], sub
        level, charging = st
        if charging:
            return ("Заряд полный" if level >= 100 else "Заряжается"), GREEN
        rate = self.cfg.get("rates", {}).get(dev.name)
        if rate:
            return f"От батареи · хватит на {fmt_hours(level / rate)}", sub
        mins = int((time.time() - dev.hist[0][0]) / 60) if dev.hist else 0
        if mins < 30:
            return f"От батареи · считаю расход: {mins} из 30 мин", sub
        return "От батареи · считаю расход: жду, пока заряд снизится", sub

    def draw_menu(self, geom=None):
        p, c = self.px, self.pc
        light = not self.notch_now and is_light(self.bg)
        bg, fg, hl, line = (("#f9f9f9", "#1a1a1a", "#ebebeb", "#e5e5e5") if light
                            else ("#2b2b2b", "#ffffff", "#3d3d3d", "#3a3a3a"))
        if self.menu_theme != light:
            self.menu_theme = light
            dwm_style(self.pm_hwnd, dark=not light)
        pad, rh, sh = p(4), p(32), p(9)
        W = max(p(210), p(44) + max(self.f_text.measure(it[1]) for it in self.menu_items if it) + p(28))
        H = 2 * pad + sum(rh if it else sh for it in self.menu_items)
        if geom:                                     # right-aligned above the widget
            x, y, w, h = geom
            mx = min(max(x + w - W, p(8)), u32.GetSystemMetrics(0) - W - p(8))
            my = y + h + p(10) if self.notch_now else y - H - p(10)
            self.menu_geom = (mx, my, W, H)
            self.set_window(self.pm, self.menu_geom)
        c.delete("all")
        c.config(bg=bg)
        self.menu_rows, yy = [], pad
        for i, it in enumerate(self.menu_items):
            if it is None:
                c.create_line(p(12), yy + sh // 2, W - p(12), yy + sh // 2, fill=line)
                yy += sh
                continue
            icon, label, _, checked = it
            if i == self.menu_hover:
                round_rect(c, pad, yy + 1, W - pad, yy + rh - 1, p(4), hl)
            glyph = icon or ("\ue73e" if checked and checked() else "")
            if glyph:
                c.create_text(p(18), yy + rh // 2, text=glyph, font=(self.icon_font, -p(13)), fill=fg, anchor="w")
            c.create_text(p(44), yy + rh // 2, text=label, font=self.f_text, fill=fg, anchor="w")
            self.menu_rows.append((yy, yy + rh, i))
            yy += rh

    def draw_card(self, geom):
        p, c = self.px, self.cc
        light = not self.notch_now and is_light(self.bg)
        bg, fg, sub, track, line = (("#f9f9f9", "#1a1a1a", "#616161", "#e0e0e0", "#e5e5e5") if light
                                    else ("#2b2b2b", "#ffffff", "#a8a8a8", "#454545", "#3a3a3a"))
        if self.card_theme != light:
            self.card_theme = light
            dwm_style(self.card_hwnd, dark=not light)
        devs, pad, row = list(self.devices.values()), p(18), p(74)
        left = f"Обновлено в {self.updated}" if self.updated else ""
        right = "ЛКМ — обновить · ПКМ — меню"
        empty = "Ищу устройства…" if self.result is None else "Беспроводные устройства Razer не найдены"
        # fit the longest line
        W = 2 * pad + max([p(280), self.f_small.measure(left + right) + p(24), self.f_sub.measure(empty)]
                          + [self.f_sub.measure(self.status(d, sub)[0]) for d in devs]
                          + [p(26) + self.f_name.measure(d.name) + p(20) + self.f_big.measure("100%") + p(16)
                             for d in devs])
        H = pad + max(1, len(devs)) * row + p(34)
        x, y, w, h = geom
        cx = min(max(x + w // 2 - W // 2, p(8)), u32.GetSystemMetrics(0) - W - p(8))
        cy = y + h + p(10) if self.notch_now else y - H - p(10)
        self.card_geom = (cx, cy, W, H)
        self.set_window(self.card, self.card_geom)
        c.delete("all")
        c.config(bg=bg)

        if not devs:
            c.create_text(pad, pad + row // 2 - p(8), text=empty, font=self.f_sub, fill=sub, anchor="w")
        for i, d in enumerate(devs):
            y0 = pad + i * row
            level = d.state[0] if isinstance(d.state, tuple) else None
            charging = isinstance(d.state, tuple) and d.state[1]
            c.create_text(pad, y0 + p(12), text=ICONS[d.kind], font=(self.icon_font, -p(16)), fill=fg, anchor="w")
            c.create_text(pad + p(26), y0 + p(12), text=d.name, font=self.f_name, fill=fg, anchor="w")
            pct = "—" if level is None else f"{level}%"
            c.create_text(W - pad, y0 + p(12), text=pct, font=self.f_big, fill=level_color(d.state, fg), anchor="e")
            if charging:
                c.create_text(W - pad - self.f_big.measure(pct) - p(4), y0 + p(12), text=BOLT,
                              font=(self.icon_font, -p(13)), fill=GREEN, anchor="e")
            bw, by = p(6), y0 + p(34)
            x0, x1 = pad + bw // 2, W - pad - bw // 2
            c.create_line(x0, by, x1, by, width=bw, fill=track, capstyle="round")
            if level:
                c.create_line(x0, by, x0 + max(1, int((x1 - x0) * level / 100)), by, width=bw,
                              fill=level_color(d.state, GREEN), capstyle="round")
            text, col = self.status(d, sub)
            c.create_text(pad, y0 + p(54), text=text, font=self.f_sub, fill=col, anchor="w")
        fy = H - p(34)
        c.create_line(0, fy, W, fy, fill=line)
        c.create_text(pad, fy + p(17), text=left, font=self.f_small, fill=sub, anchor="w")
        c.create_text(W - pad, fy + p(17), text=right, font=self.f_small, fill=sub, anchor="e")


if __name__ == "__main__":
    if "--once" in sys.argv:          # diagnostics; the exe has no console, hence the file
        report = "\n".join(map(str, read_all())) or "no wireless Razer devices found"
        print(report)
        CFG.parent.mkdir(parents=True, exist_ok=True)
        (CFG.parent / "diagnostics.txt").write_text(report, "utf-8")
    else:
        ctypes.windll.kernel32.CreateMutexW(None, False, f"Local\\{APP}")
        if ctypes.windll.kernel32.GetLastError() == 183:      # ERROR_ALREADY_EXISTS
            sys.exit(0)
        App().root.mainloop()
