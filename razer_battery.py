"""Battery level of wireless Razer headsets, mice and keyboards on the Windows taskbar, plus Bluetooth, Logitech, DualShock 4 and HeadsetControl devices.

Protocol by what the HID report descriptor offers:
  output report 2    PA frames (Barracuda X 0550, BlackShark V2 Pro 2023)
  output report 1    PA frames, Barracuda Pro variant
  feature report FF  state blob (Barracuda X 0536, Nari)
  feature, no id     classic 90-byte report, class 07 (mice, keyboards, BlackShark V2 Pro 2020)
"""
import ctypes, ctypes.wintypes as wt, hashlib, json, os, re, shutil, subprocess, sys, threading, time, winreg
import tkinter as tk, tkinter.font as tkfont, urllib.request
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
# Every query wakes the headset's radio, and the Barracuda X seems to count that as activity, so on
# battery we ask less often than its 15-minute auto-off timer. Hovering the widget asks right away.
POLL_ON_BATTERY, POLL_CHARGING, POLL_OFFLINE, HOVER_REFRESH = 20 * 60, 5 * 60, 60, 60
SLEEP_GAP = 2 * POLL_ON_BATTERY + 60     # longer silence than this means the PC slept, not that we waited
LOW_LEVELS = (20, 10)
APP = "RazerBatteryIndicator"
VERSION = "1.5.0"
REPO = "NikolayYaroslavcev/razer-battery-tray"
CFG = Path(os.environ["APPDATA"]) / APP / "config.json"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RU = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF in (0x19, 0x23)   # Windows in Russian, Belarusian

def L(ru, en):
    return ru if RU else en


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



def protocols_for(kinds, d):
    table = ((proto_pa2, ("out", 2)), (proto_pa1, ("out", 1)), (proto_blob, ("feat", 0xFF)), (proto_std, ("feat", 0)))
    # vendor frames only for the headset families they were documented on; a different device could
    # read the same bytes as a settings write. Everything else gets the classic read-only report.
    name = (d.get("product_string") or "").lower()
    known = d["product_id"] in HEADSET_PIDS or any(w in name for w in ("barracuda", "blackshark", "nari"))
    return [p for p, k in table if k in kinds and (known or p is proto_std)]


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
    name = "".join(ch for ch in (d.get("product_string") or "") if ch.isprintable())[:48]
    name = name.replace(" 2.4", "").replace(" Dongle", "").strip()
    return name if name.lower().startswith("razer") else f"Razer {name or 'device'}"


_working = {}     # pid -> (path, protocol) that answered last time


def _try(pid, d, kind, protos=None):
    h = hid.device()
    try:
        h.open_path(d["path"])
    except (OSError, ValueError):
        return None
    try:
        for proto in protos or protocols_for(report_kinds(h), d):
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


def read_all(radio=True):
    """[(pid, kind, name, state)], state is 'offline' or (percent, charging). radio=False reads again only
    what costs the devices nothing (PASSIVE) and repeats the last readings of the rest."""
    if radio:
        _radio[:] = read_razer()
        if time.time() - _slow[0] > SLOW_EVERY:   # these talk to devices, so not on every hover or offline poll
            _slow[:] = [time.time(), _optional(read_logitech) + _optional(read_headsetcontrol)]
    razer = [r for _, _, n, _ in _radio if (r := n.lower().removeprefix("razer ")) not in ("", "device")]
    bt = [e for e in _optional(read_bluetooth) if not any(r in e[2].lower() for r in razer)]  # same headset via BT
    return _radio + bt + _optional(read_dualshock) + _slow[1]


SLOW_EVERY, _slow, _radio = 5 * 60, [0.0, []], []
PASSIVE = ("bt:", "ds4:")         # pid prefixes of sources that only read what Windows or the pad already has

def _optional(source):
    try:
        return source()
    except Exception:                 # optional sources must never cost the Razer readings
        return []


def read_razer():
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


# --- other brands; their pids are strings so they never mix with Razer product ids ---

def _guess_kind(name):
    n = name.lower()
    if any(w in n for w in ("keyboard", "клавиат", "keys", "mechanical")):
        return "keyboard"
    return "mouse" if any(w in n for w in ("mouse", "мыш", "mx ", "trackball")) else "headset"


class _PropKey(ctypes.Structure):
    _fields_ = [("fmtid", ctypes.c_byte * 16), ("pid", wt.DWORD)]

class _DevInfo(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("ClassGuid", ctypes.c_byte * 16), ("DevInst", wt.DWORD), ("Reserved", ctypes.c_void_p)]

def _prop_key(guid, pid):
    import uuid
    return _PropKey((ctypes.c_byte * 16).from_buffer_copy(uuid.UUID(guid).bytes_le), pid)

BT_BATTERY = _prop_key("104EA319-6EE2-4701-BD47-8DDBF425BBE5", 2)     # what Settings > Bluetooth shows
BT_CONNECTED = _prop_key("83DA6326-97A6-4088-9453-A1923F573B29", 15)
FRIENDLY_NAME = _prop_key("A45C254E-DF1C-4EFD-8020-67D146A850E0", 14)
BT_CLASS = _prop_key("2BD67D8B-8BEB-48D5-87E0-6CDA3428040A", 10)       # Bluetooth class of device


def read_bluetooth():
    """Any brand: Windows already keeps the level of connected Bluetooth devices, nothing is woken."""
    sa = ctypes.windll.setupapi
    sa.SetupDiGetClassDevsW.restype = ctypes.c_void_p
    sa.SetupDiGetClassDevsW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, wt.HWND, wt.DWORD]
    sa.SetupDiEnumDeviceInfo.argtypes = [ctypes.c_void_p, wt.DWORD, ctypes.c_void_p]
    sa.SetupDiGetDeviceInstanceIdW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, wt.DWORD, ctypes.c_void_p]
    sa.SetupDiGetDevicePropertyW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                             ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, wt.DWORD]
    sa.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
    hs = sa.SetupDiGetClassDevsW(None, None, None, 0x2 | 0x4)              # DIGCF_PRESENT | DIGCF_ALLCLASSES
    if not hs or hs == ctypes.c_void_p(-1).value:
        return []
    info = _DevInfo(cbSize=ctypes.sizeof(_DevInfo))
    def prop(key):
        typ, buf, n = wt.DWORD(), ctypes.create_string_buffer(512), wt.DWORD()
        ok = sa.SetupDiGetDevicePropertyW(hs, ctypes.byref(info), ctypes.byref(key), ctypes.byref(typ), buf, 512,
                                          ctypes.byref(n), 0)
        return buf.raw[:n.value] if ok else None
    out, seen, i = [], set(), 0
    try:
        while sa.SetupDiEnumDeviceInfo(hs, i, ctypes.byref(info)):
            i += 1
            iid = ctypes.create_unicode_buffer(260)
            if not sa.SetupDiGetDeviceInstanceIdW(hs, ctypes.byref(info), iid, 260, None) or \
                    not iid.value.upper().startswith(("BTHENUM\\", "BTHLE\\", "BTHLEDEVICE\\", "BTHHFENUM\\")):
                continue
            level, raw_name = prop(BT_BATTERY), prop(FRIENDLY_NAME)
            if not level or not raw_name:
                continue
            name = raw_name.decode("utf-16-le", "ignore").rstrip("\0").strip()
            if name in seen:              # one device shows up as several service nodes
                continue
            seen.add(name)
            cod = prop(BT_CLASS)
            major = (int.from_bytes(cod[:4], "little") >> 8) & 0x1F if cod and len(cod) >= 4 else None
            if major in (1, 2):           # computer, phone: not a peripheral of this PC
                continue
            kind = {0x04: "headset"}.get(major) or _guess_kind(name)
            conn = prop(BT_CONNECTED)
            state = "offline" if conn is not None and conn[:1] == b"\0" else (min(level[0], 100), False)
            out.append((f"bt:{name}", kind, name, state))
    finally:
        sa.SetupDiDestroyDeviceInfoList(hs)
    return out


# Logitech Unifying / Lightspeed / Bolt receivers, HID++ 2.0 (as documented by Solaar). Written from the
# protocol notes without the hardware, like the non-Barracuda Razer support.
LOGI_VID = 0x046D
LOGI_RECEIVERS = {0xC52B, 0xC532, 0xC534, 0xC539, 0xC53A, 0xC53D, 0xC53F, 0xC541, 0xC545, 0xC547, 0xC548}
LOGI_KINDS = {0: "keyboard", 3: "mouse", 4: "mouse", 5: "mouse", 8: "headset"}

def _hidpp(h, idx, feature, fn, *args):
    """One long HID++ request to device slot idx, reply payload or None (no device, off, unsupported)."""
    code = (fn << 4) | 0x0A                                   # 0x0A: our software id
    while h.read(20, 0):                                      # a late reply must not answer this request
        pass
    h.write([0x11, idx, feature, code, *args] + [0] * (16 - len(args)))
    end = time.time() + 0.3
    while time.time() < end:
        r = h.read(20, 50)
        if r and r[0] == 0x11 and r[1] == idx:
            if r[2] == feature and r[3] == code:
                return r[4:]
            if r[2] == 0xFF:                                  # HID++ 2.0 error reply
                return None
    return None

def _logi_device(h, idx):
    def feat(code):
        r = _hidpp(h, idx, 0, 0, code >> 8, code & 0xFF)     # root feature: index of a feature, 0 = none
        return r[0] if r else None
    level = None
    fi = feat(0x1004)                                         # unified battery
    if fi is None:
        return None                                           # empty slot or device asleep
    if fi and (r := _hidpp(h, idx, fi, 1)):
        caps = _hidpp(h, idx, fi, 0)
        if caps and caps[1] & 0x02:                           # reports a percentage
            level = r[0]
        else:                                                 # only full / good / low / critical
            level = next((v for bit, v in ((8, 100), (4, 60), (2, 20), (1, 5)) if r[1] & bit), None)
        charging = r[2] in (1, 2, 3)
    elif (fi := feat(0x1000)) and (r := _hidpp(h, idx, fi, 0)):  # battery level status
        level, charging = r[0], r[2] in (1, 2, 3, 4)
    if level is None:
        return None
    name, kind = "Logitech", "mouse"
    if (fi := feat(0x0005)) and (r := _hidpp(h, idx, fi, 0)):   # device name and type
        buf = b""
        while len(buf) < r[0]:
            part = _hidpp(h, idx, fi, 1, len(buf))
            if not part:
                break
            buf += bytes(part[:r[0] - len(buf)])
        name = buf.decode("utf-8", "ignore").strip("\0 ") or name
        if t := _hidpp(h, idx, fi, 2):
            kind = LOGI_KINDS.get(t[0], kind)
    if not name.lower().startswith("logitech"):
        name = f"Logitech {name}"
    return kind, name, (min(level, 100), charging)

def read_logitech():
    out = []
    for d in hid.enumerate(LOGI_VID):
        if d["product_id"] not in LOGI_RECEIVERS or (d["usage_page"], d["usage"]) != (0xFF00, 0x0002):
            continue                                          # only the long-report HID++ collection
        h = hid.device()
        try:
            h.open_path(d["path"])
        except (OSError, ValueError):
            continue
        try:
            for idx in range(1, 7):
                key = f"lg:{d['product_id']:04X}:{idx}"
                if res := _logi_device(h, idx):
                    _logi_seen[key] = res[:2]
                    out.append((key, *res))
                elif key in _logi_seen:       # asleep: keep it, or its alerts would repeat on every wake
                    out.append((key, *_logi_seen[key], "offline"))
        except OSError:                       # receiver unplugged mid-read: keep what we got
            pass
        finally:
            h.close()
    return out

_logi_seen = {}                               # slot -> (kind, name) of devices that answered before


DS4_PIDS = {0x05C4, 0x09CC}                  # DualShock 4 v1, v2

def _ds4_state(h):
    """Status byte of the input reports the pad streams anyway; nothing is sent to it."""
    for _ in range(10):
        r = h.read(80, 100)
        if not r:
            return None
        if r[0] == 0x11 and len(r) > 32:         # Bluetooth, full report
            st = r[32]
        elif r[0] == 0x01 and len(r) > 30:       # USB (the short Bluetooth 0x01 report has no battery)
            st = r[30]
        else:
            continue
        level, cable = st & 0x0F, bool(st & 0x10)  # levels as in Linux hid-sony: 0-10, 11 = full on cable
        return (100 if level >= 10 else level * 10 + 5), cable and level < 11
    return None

def ds4_pads():
    """Only lists devices, nothing is opened: cheap enough to watch for a pad being swapped."""
    pads = {}
    for d in hid.enumerate(0x054C):
        if d["product_id"] in DS4_PIDS and (d["usage_page"], d["usage"]) == (1, 5):
            key = d["serial_number"] or hashlib.md5(d["path"]).hexdigest()[:12]   # Bluetooth gives the MAC
            pads.setdefault(key, d)                                # the same pad twice: once
    return pads

def read_dualshock():
    out = []
    for serial, d in ds4_pads().items():
        h = hid.device()
        try:
            h.open_path(d["path"])
            state = _ds4_state(h)
        except (OSError, ValueError):
            continue
        finally:
            h.close()
        if state:
            # each pad its own name: alerts and discharge history go by name, a swapped pad must not inherit them
            out.append((f"ds4:{serial}", "gamepad", f"DualShock 4 ({serial[-4:].upper()})", state))
    return out


def read_headsetcontrol():
    """SteelSeries, Logitech, Corsair, HyperX... headsets, if the user installed HeadsetControl."""
    path = os.environ.get("PATH", "")         # explicit path: no lookup in the current folder
    exe = shutil.which("headsetcontrol", path=path) or shutil.which("headsetcontrol-windows-x86_64", path=path)
    if not exe:
        return []
    r = subprocess.run([exe, "-b", "-o", "json"], capture_output=True, timeout=10, creationflags=NO_WINDOW)
    out = []
    for d in json.loads(r.stdout).get("devices", []):
        vid = d.get("id_vendor")
        if (int(vid, 16) if isinstance(vid, str) else vid) == VID:
            continue                                          # Razer: read directly above
        b = d.get("battery") or {}
        level, status = b.get("level"), b.get("status")
        ok = status in ("BATTERY_AVAILABLE", "BATTERY_CHARGING") and isinstance(level, int) and level >= 0
        state = (min(level, 100), status == "BATTERY_CHARGING") if ok else "offline"
        out.append((f"hc:{d.get('id_vendor')}:{d.get('id_product')}", "headset", d.get("device") or "Headset", state))
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
        tmp = CFG.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(cfg))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CFG)          # a crash or power cut mid-write must not reset the settings and autostart
    except OSError:
        pass

# --- updates ---

def _get(url, timeout, limit):
    req = urllib.request.Request(url, headers={"User-Agent": "razer-battery-tray", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(limit)

def latest_release():
    """(version, exe_url, sha256) of the newest release, or None if it doesn't look right."""
    rel = json.loads(_get(f"https://api.github.com/repos/{REPO}/releases/latest", 15, 1 << 20))
    ver = str(rel.get("tag_name", "")).lstrip("v")
    asset = next((a for a in rel.get("assets", []) if a.get("name") == f"{APP}.exe"), None)
    sha = re.search(rf"\b([0-9A-Fa-f]{{64}})\s+{APP}\.exe", rel.get("body") or "")
    if not (asset and sha and re.fullmatch(r"\d+(\.\d+){0,3}", ver)):
        return None
    url = str(asset.get("browser_download_url", ""))
    if not url.startswith(f"https://github.com/{REPO}/releases/download/"):
        return None
    return ver, url, sha.group(1).lower()

def is_newer(a, b):
    return tuple(map(int, a.split("."))) > tuple(map(int, b.split(".")))

def download_update(url, sha):
    """Download, check the hash from the release notes, swap the exe in place. Returns the exe path."""
    data = _get(url, 120, 64 << 20)
    if hashlib.sha256(data).hexdigest() != sha:
        raise ValueError(L("контрольная сумма не совпала", "checksum mismatch"))
    exe = Path(sys.executable)
    new, old = exe.with_name(f"{exe.stem}.new.exe"), exe.with_name(f"{exe.stem}.old.exe")
    new.write_bytes(data)
    old.unlink(missing_ok=True)
    exe.rename(old)                   # a running exe can be renamed, just not overwritten
    try:
        new.rename(exe)
    except OSError:                   # e.g. an antivirus still scanning it: put ours back, or autostart breaks
        old.rename(exe)
        raise
    return exe


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

def set_autostart(on, cmd=None):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP, 0, winreg.REG_SZ, cmd or _run_cmd())
        else:
            try:
                winreg.DeleteValue(k, APP)
            except OSError:
                pass


# --- install / uninstall: the downloaded exe installs itself on first run ---

TITLE = "Razer Battery Tray"
INSTALL_DIR = Path(os.environ["LOCALAPPDATA"]) / "Programs" / TITLE
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\RazerBatteryTray"
NO_WINDOW, DETACHED = 0x08000000, 0x00000008

k32 = ctypes.windll.kernel32
k32.CreateMutexW.restype = k32.CreateEventW.restype = wt.HANDLE
k32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
k32.CreateEventW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR]
k32.SetEvent.argtypes = k32.ResetEvent.argtypes = k32.CloseHandle.argtypes = [wt.HANDLE]
k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]

def named_event(name):
    """Manual-reset event shared by all copies: 'quit' closes the running one, 'show' pops its card."""
    return k32.CreateEventW(None, True, False, f"Local\\{APP}.{name}")

def already_running():
    h = k32.CreateMutexW(None, False, f"Local\\{APP}")
    busy = k32.GetLastError() == 183                  # ERROR_ALREADY_EXISTS
    k32.CloseHandle(h)
    return busy

def stop_running():
    ev = named_event("quit")
    k32.SetEvent(ev)
    for _ in range(40):
        if not already_running():
            break
        time.sleep(0.1)
    else:                                             # 1.0.0 doesn't listen for the event
        subprocess.run(["taskkill", "/F", "/IM", f"{APP}.exe", "/FI", f"PID ne {os.getpid()}"],
                       capture_output=True, creationflags=NO_WINDOW)
        time.sleep(1)
    k32.ResetEvent(ev)
    k32.CloseHandle(ev)

def _ps(script):
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                   capture_output=True, creationflags=NO_WINDOW, timeout=60)

def _psq(value):
    return "'" + str(value).replace("'", "''") + "'"

_LINKS = ("foreach ($d in [Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs')) "
          "{ $l = Join-Path $d " + _psq(TITLE + ".lnk") + "; ")

def register(exe):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
        for name, val in (("DisplayName", TITLE), ("DisplayVersion", VERSION), ("Publisher", "NikolayYaroslavcev"),
                          ("DisplayIcon", f"{exe},0"), ("InstallLocation", str(exe.parent)),
                          ("UninstallString", f'"{exe}" --uninstall'),
                          ("QuietUninstallString", f'"{exe}" --uninstall --silent'),
                          ("URLInfoAbout", f"https://github.com/{REPO}")):
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, val)
        for name, val in (("NoModify", 1), ("NoRepair", 1), ("EstimatedSize", exe.stat().st_size // 1024)):
            winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, val)

def launch(exe, *args):
    # without the reset a pyinstaller exe started by another one reuses its temp folder, and the parent then
    # can't delete that folder on exit ("Failed to remove temporary directory")
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    subprocess.Popen([str(exe), *args], env=env, close_fds=True)

def remove_old_exe():
    """After an update the previous exe stays locked until its loader process exits; keep trying for a minute."""
    old = Path(sys.executable).with_name(f"{Path(sys.executable).stem}.old.exe")
    for _ in range(60):
        try:
            old.unlink(missing_ok=True)
            return
        except OSError:
            time.sleep(1)

def install():
    dst = INSTALL_DIR / f"{APP}.exe"
    stop_running()
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(sys.executable, dst)
    _ps(_LINKS + f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut($l); $s.TargetPath = {_psq(dst)}; "
                 f"$s.WorkingDirectory = {_psq(INSTALL_DIR)}; $s.Description = {_psq(TITLE)}; $s.Save() }}")
    register(dst)
    set_autostart(True, f'"{dst}"')
    launch(dst, "--installed")

def uninstall():
    u = ctypes.windll.user32
    quiet = "--silent" in sys.argv                   # winget uninstall
    ask = L(f"Удалить {TITLE} вместе с настройками?", f"Remove {TITLE} and its settings?")
    if not quiet and u.MessageBoxW(None, ask, TITLE, 0x4 | 0x20 | 0x40000) != 6:   # IDYES
        return
    stop_running()
    set_autostart(False)
    _ps(_LINKS + "Remove-Item -LiteralPath $l -ErrorAction SilentlyContinue }")
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass
    shutil.rmtree(CFG.parent, ignore_errors=True)
    if Path(sys.executable).parent == INSTALL_DIR:   # we are that exe: the folder goes once we've exited
        subprocess.Popen(f'cmd /c ping -n 3 127.0.0.1 >nul & rmdir /s /q "{INSTALL_DIR}"',
                         creationflags=NO_WINDOW | DETACHED, close_fds=True)
    if not quiet:
        u.MessageBoxW(None, L(f"{TITLE} удалён.", f"{TITLE} has been removed."), TITLE, 0x40 | 0x40000)


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
u32.SetWindowTextW.argtypes = [wt.HWND, wt.LPCWSTR]
u32.GetWindow.restype, u32.GetWindow.argtypes = wt.HWND, [wt.HWND, wt.UINT]
u32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
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

def under_taskbar(hwnd):
    """The taskbar comes above every topmost window when clicked; only then do we need to climb back."""
    tb, w = u32.FindWindowW("Shell_TrayWnd", None), hwnd
    for _ in range(500):
        w = u32.GetWindow(w, 3)                                      # GW_HWNDPREV: the window just above
        if not w or w == tb:
            return bool(w)
    return True

SHELL_FOCUS = ("Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Windows.UI.Core.CoreWindow",
               "TopLevelWindowForOverflowXamlIsland", "XamlExplorerHostIslandWindow", "NotifyIconOverflowWindow")

def own_by_taskbar(hwnd):
    # While Start, a tray flyout or the taskbar itself has focus, Windows 11 lifts the taskbar above every
    # topmost window. Windows it owns ride along. Only then, though: while the taskbar owns a visible window,
    # explorer holds back toasts as if a flyout were open. Re-checked each tick: explorer may have restarted.
    buf = ctypes.create_unicode_buffer(64)
    u32.GetClassNameW(u32.GetForegroundWindow(), buf, 64)
    tb = u32.FindWindowW("Shell_TrayWnd", None) if buf.value in SHELL_FOCUS else None
    if u32.GetWindow(hwnd, 4) != (tb or None):                       # GW_OWNER
        u32.SetWindowLongPtrW(hwnd, -8, tb or 0)                     # GWLP_HWNDPARENT
        return True


# --- UI ---

GREEN, GREEN_DIM, AMBER, RED, GREY = "#30d158", "#1f7a37", "#ffd60a", "#ff453a", "#8e8e93"
BOLT = "\ue945"
ICONS = {"headset": "\ue7f6", "mouse": "\ue962", "keyboard": "\ue765", "gamepad": "\ue7fc"}   # dict order = display order
OFFLINE_TEXT = {"headset": ("Наушники выключены или вне зоны", "Headset is off or out of range"),
                "mouse": ("Мышь выключена или спит", "Mouse is off or asleep"),
                "keyboard": ("Клавиатура выключена или спит", "Keyboard is off or asleep"),
                "gamepad": ("Геймпад выключен", "Controller is off")}


def fmt_hours(h):
    return f"~{h:.0f} {L('ч', 'h')}" if h >= 1.5 else f"~{max(1, round(h * 60)):.0f} {L('мин', 'min')}"

def level_color(st, fg):
    if not isinstance(st, tuple):
        return GREY
    level, charging = st
    return fg if charging or level > 35 else AMBER if level > 20 else RED


class Device:
    def __init__(self, pid, kind, name):
        self.pid, self.kind, self.name = pid, kind, name
        self.state, self.hist, self.alerted, self.seen_at = None, [], set(), 0
        self.last = None                      # (percent, "HH:MM") of the last real reading


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
        self.card_theme = self.menu_theme = self.drawn = None
        self.raised = {}                                    # hwnd -> geometry it was last raised with
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

        self.devices, self.known = {}, {}                   # pid -> Device: listed now / ever seen
        self.answered = set()                               # pids that ever returned a level
        self.menu_token = 0
        self.result, self.handled, self.updated = None, None, None
        self.alert = None                                   # (pid or None, text, color, hide_at, seconds)
        self.hover, self.menu_open, self.phase = False, False, 0
        self.bg, self.bg_checked = "#1c1c1c", 0
        self.name = None                                    # window title, what UI Automation reads
        self.wake = threading.Event()

        self.mode = tk.StringVar(value="notch" if self.cfg.get("mode") == "notch" else "taskbar")
        self.autostart = autostart_enabled()
        # (icon, label, action, checked); None is a separator
        self.base_menu = [
            ("\ue72c", L("Обновить", "Refresh"), self.wake.set, None),
            None,
            (None, L("На панели задач", "On the taskbar"), lambda: self.set_mode("taskbar"),
             lambda: self.mode.get() == "taskbar"),
            (None, L("Чёлка сверху", "Notch at the top"), lambda: self.set_mode("notch"),
             lambda: self.mode.get() == "notch"),
            None,
            (None, L("Запускать с Windows", "Start with Windows"), self.toggle_autostart, lambda: self.autostart),
            (None, L("Проверять обновления", "Check for updates"), self.toggle_updates,
             lambda: self.cfg.get("check_updates", True)),
            ("\ue7e8", L("Выход", "Exit"), self.quit, None),
        ]
        self.update = self.update_seen = self.update_done = None
        self.quit_ev, self.show_ev, self.quitting = named_event("quit"), named_event("show"), False
        self.interval, self.polled_at, self.ticked_at = POLL_OFFLINE, 0.0, time.time()
        self.menu_rows, self.menu_hover, self.menu_geom, self.card_geom, self.menu_now = [], -1, None, None, []
        self.hit.bind("<Button-1>", lambda e: self.wake.set())
        self.hit.bind("<Button-3>", self.open_menu)
        self.cc.bind("<Button-1>", lambda e: self.wake.set())
        self.cc.bind("<Button-3>", self.open_menu)
        self.pc.bind("<Motion>", lambda e: self.menu_hover_at(e.y))
        self.pc.bind("<Leave>", lambda e: self.menu_hover_at(-1))
        self.pc.bind("<Button-1>", lambda e: self.menu_click(e.y))

        if not self.cfg.get("welcomed"):       # first launch: turn on autostart and say where we are
            self.cfg["welcomed"] = True
            save_cfg(self.cfg)
            if not self.autostart:
                try:
                    self.toggle_autostart()
                except OSError:
                    pass
            self.show_alert(None, L("Готово! Заряд теперь здесь, у часов. Правый клик — меню",
                                    "Done! Battery levels are here, by the clock. Right click for the menu"), GREEN, 12)
        if "--installed" in sys.argv:
            self.show_alert(None, L("Установлено. Заряд здесь, у часов, ярлык на рабочем столе",
                                    "Installed. Battery levels are here, by the clock; shortcut on the desktop"), GREEN, 12)

        threading.Thread(target=self.poll_loop, daemon=True).start()
        threading.Thread(target=self.pad_watch, daemon=True).start()
        threading.Thread(target=self.update_loop, daemon=True).start()
        self.tick()

    @property
    def menu_items(self):
        # a check mark per device: untick one to keep it off the taskbar, the card and the alerts
        devs = [(None, d.name, lambda n=d.name: self.toggle_hidden(n), lambda n=d.name: n not in self.hidden)
                for d in self.devices.values()]
        v = self.update and self.update[0]
        top = [("\ue896", L(f"Обновить программу до {v}", f"Update to {v}"), self.start_update, None), None] if v else []
        return top + self.base_menu[:2] + devs + [None] * bool(devs) + self.base_menu[2:]

    @property
    def hidden(self):
        h = self.cfg.get("hidden")
        return h if isinstance(h, list) else []

    def toggle_hidden(self, name):
        h = self.hidden
        self.cfg["hidden"] = [n for n in h if n != name] if name in h else h + [name]
        save_cfg(self.cfg)

    def shown(self):
        return [d for d in self.devices.values() if d.name not in self.hidden]

    def update_loop(self):
        time.sleep(30)
        while True:
            if self.cfg.get("check_updates", True):
                try:
                    rel = latest_release()
                    if rel and is_newer(rel[0], VERSION):
                        self.update = rel
                except Exception:
                    pass              # offline, rate-limited, GitHub down: try tomorrow
            time.sleep(24 * 3600)

    def toggle_updates(self):
        self.cfg["check_updates"] = not self.cfg.get("check_updates", True)
        save_cfg(self.cfg)

    def start_update(self):
        if not getattr(sys, "frozen", False):          # running from source: just show the release
            os.startfile(f"https://github.com/{REPO}/releases/latest")
            return
        self.show_alert(None, L("Скачиваю обновление…", "Downloading the update…"), GREY, 60)
        ver, url, sha = self.update

        def work():
            try:
                self.update_done = download_update(url, sha)
            except Exception as e:
                self.update_done = e
        threading.Thread(target=work, daemon=True).start()

    def px(self, v):
        return int(round(v * self.s))


    def poll_loop(self):
        radio_at, woken = 0.0, True
        while True:
            radio = woken or time.time() - radio_at >= self.interval - 1
            try:
                self.result = read_all(radio), radio     # picked up by tick() on the UI thread
            except Exception:
                pass
            if radio:
                radio_at = time.time()
                self.updated, self.polled_at = time.strftime("%H:%M"), radio_at
            # Bluetooth and DualShock 4 levels cost nothing to read, so they don't wait for the Razer schedule
            woken = self.wake.wait(min(self.interval, POLL_OFFLINE))
            self.wake.clear()

    def pad_watch(self):
        """A pad connected, removed or swapped: read now, not at the next 20-minute poll."""
        last = None
        while True:
            try:
                pads = set(ds4_pads())
                if last is not None and pads != last:
                    self.wake.set()
                last = pads
            except Exception:
                pass
            time.sleep(3)

    def poll_interval(self):
        """Shortest interval any device needs (runs on the UI thread, read by the poll thread)."""
        def need(d):
            st = d.state
            if isinstance(d.pid, str):        # other brands must not make us wake the Razer radio more often
                return POLL_ON_BATTERY
            if not isinstance(st, tuple):
                return POLL_OFFLINE           # only the dongle answers, the headset is not woken
            return POLL_CHARGING if st[1] else POLL_ON_BATTERY
        return min((need(d) for d in self.devices.values()), default=POLL_OFFLINE)

    def apply(self, result):
        entries, radio = result
        seen = set()
        for pid, kind, name, state in entries:
            if not radio and pid in self.devices and not str(pid).startswith(PASSIVE):
                seen.add(pid)                 # repeated from the last radio poll: not a new reading to learn from
                continue
            if isinstance(state, tuple):
                self.answered.add(pid)
            elif pid not in self.answered and pid not in HEADSET_PIDS | MOUSE_PIDS:
                continue                      # wired headset, nothing to show
            # a pad or Bluetooth device missing from one read comes back with its alerts and history
            dev = self.devices[pid] = self.devices.get(pid) or self.known.get(pid)
            if dev is None:
                dev = self.devices[pid] = self.known[pid] = Device(pid, kind, name)
                try:                          # continue the measurement from the previous run
                    track = self.cfg.get("track")
                    t0, l0, last = track.get(name, (0, 0, 0)) if isinstance(track, dict) else (0, 0, 0)
                    if time.time() - float(last) < SLEEP_GAP:
                        dev.hist, dev.seen_at = [(float(t0), int(l0))], float(last)
                except (TypeError, ValueError):
                    pass
            if isinstance(state, tuple) and state[1] is None:   # charging read failed, keep last value
                old = dev.state
                state = (state[0], old[1] if isinstance(old, tuple) else False)
            dev.state = state
            if isinstance(state, tuple):
                dev.last = (state[0], time.strftime("%H:%M"))
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
        gap = now - dev.seen_at > SLEEP_GAP        # PC was asleep
        dev.seen_at = now
        for th in LOW_LEVELS:                 # charged behind our back: re-arm
            if level > th + 2:
                dev.alerted.discard(f"low{th}")
        if charging:
            self.save_track(dev, [])
            dev.alerted.difference_update(f"low{t}" for t in LOW_LEVELS)
            if level >= 100 and "full" not in dev.alerted:
                dev.alerted.add("full")
                self.show_alert(dev, L("Заряд 100% — можно отключать", "Fully charged, you can unplug it"), GREEN)
            return
        dev.alerted.discard("full")
        if dev.hist and (gap or level > dev.hist[-1][1] + 1):
            dev.hist = []
        if not dev.hist or level != dev.hist[-1][1]:
            dev.hist.append((now, level))
        t0, l0 = dev.hist[0]
        hours, drop = (now - t0) / 3600, l0 - level
        if hours >= 0.5 and drop >= 2:
            if not isinstance(self.cfg.get("rates"), dict):
                self.cfg["rates"] = {}
            rates = self.cfg["rates"]
            old, rate = rates.get(dev.name), drop / hours
            old = old if isinstance(old, (int, float)) and old > 0 else None
            rates[dev.name] = round(rate if old is None else 0.5 * old + 0.5 * rate, 3)
            dev.hist = [(now, level)]
        self.save_track(dev, dev.hist)
        for th in LOW_LEVELS:
            if level <= th and f"low{th}" not in dev.alerted:
                dev.alerted.update(f"low{t}" for t in LOW_LEVELS if t >= level)
                self.show_alert(dev, L(f"Осталось {level}% — пора на зарядку", f"{level}% left, time to charge"), RED)
                break

    def save_track(self, dev, hist):
        # kept in the config so a restart doesn't throw the measurement away
        dev.hist = hist
        if not isinstance(self.cfg.get("track"), dict):
            self.cfg["track"] = {}
        track = self.cfg["track"]
        if hist:
            old = track.get(dev.name)
            if isinstance(old, list) and len(old) == 3 and old[:2] == list(hist[0]) and \
                    time.time() - float(old[2]) < POLL_ON_BATTERY:
                return                        # Bluetooth is read every minute; the file needn't be written that often
            track[dev.name] = [hist[0][0], hist[0][1], time.time()]
        elif track.pop(dev.name, None) is None:
            return
        save_cfg(self.cfg)

    def show_alert(self, dev, text, color, seconds=8):
        # dev=None puts the text in the card's footer; the timer starts once it's actually on screen
        if dev and dev.name in self.hidden:
            return
        self.alert = (dev.pid if dev else None, text, color, None, seconds)

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
        self.menu_now = self.menu_items      # fixed while open: a poll adding a device must not shift the rows
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
            self.menu_now[i][2]()

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
        if not self.quitting:
            self.root.after(500, self.tick)

    def quit(self):
        self.quitting = True
        self.root.destroy()

    def update_ui(self):
        now = time.time()
        if now - self.ticked_at > 60:          # the PC slept: the numbers are stale
            self.wake.set()
        self.ticked_at = now
        if k32.WaitForSingleObject(self.quit_ev, 0) == 0:          # installer / uninstaller asks us to go
            self.quit()
            return
        if k32.WaitForSingleObject(self.show_ev, 0) == 0:          # the shortcut was clicked again
            k32.ResetEvent(self.show_ev)
            self.show_alert(None, L("Программа уже работает: заряд здесь, у часов",
                                    "Already running: battery levels are here, by the clock"), GREEN, 6)
        res = self.result
        if res is not self.handled:
            self.handled = res
            self.apply(res)
        if self.alert and self.alert[3] and time.time() > self.alert[3]:
            self.alert = None
        if self.update and self.update_seen != self.update[0]:
            self.update_seen = self.update[0]
            v = self.update[0]
            self.show_alert(None, L(f"Вышла версия {v}: правый клик → «Обновить программу»",
                                    f"Version {v} is out: right click → Update to {v}"), GREEN, 10)
        done, self.update_done = self.update_done, None
        if isinstance(done, Path):
            launch(done, "--after-update")
            self.quit()
            return
        if isinstance(done, Exception):
            self.show_alert(None, L("Не удалось обновить: ", "Update failed: ") + str(done)[:60], RED, 10)
        dpi = u32.GetDpiForWindow(self.hwnd)
        if dpi and abs(dpi / 96 - self.s) > 0.01:          # scale changed
            self.s = dpi / 96
            for attr, (_, size) in self.fonts.items():
                getattr(self, attr).configure(size=-self.px(size))
        charging = any(isinstance(d.state, tuple) and d.state[1] for d in self.shown())
        self.phase = (self.phase + 1) % 2 if charging else 0

        geom = self.place()
        # keyed-out pixels get no mouse events, so check the cursor ourselves
        pt = wt.POINT()
        u32.GetCursorPos(ctypes.byref(pt))
        card_up = self.card.winfo_ismapped() and self.card_geom
        was_hover = self.hover
        self.hover = bool(geom) and (inside(geom, pt) or bool(card_up and inside(self.card_geom, pt, self.px(12))))
        if self.hover and not was_hover and time.time() - self.polled_at > HOVER_REFRESH:
            self.wake.set()                   # someone is looking: get fresh numbers
        self.interval = self.poll_interval()
        if geom:
            self.draw_widget(geom)
            self.set_names()
        self.show(self.root, bool(geom))
        self.show(self.hit, bool(geom))
        if geom:
            for win, hwnd in ((self.root, self.hwnd), (self.hit, self.hit_hwnd)):
                self.set_window(win, geom)
                # SetWindowPos on every tick was most of the idle CPU, so only when something moved
                if own_by_taskbar(hwnd) or self.raised.get(hwnd) != geom or under_taskbar(hwnd):
                    self.raised[hwnd] = geom
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
                self.alert = self.alert[:3] + (time.time() + self.alert[4], self.alert[4])
        self.show(self.card, card)

    def set_names(self):
        """The widget is a canvas, so screen readers and UI Automation only see the window title."""
        name = "; ".join(f"{d.name} {text}" + ("" if isinstance(st, tuple) else L(", не в сети", ", offline"))
                         + (L(", заряжается", ", charging") if isinstance(st, tuple) and st[1] else "")
                         for d, (_, text, st) in zip(self.shown(), self.items())) or L("Razer: нет устройств",
                                                                                       "Razer: no devices")
        if name != self.name:
            self.name = name
            for hwnd in (self.hwnd, self.hit_hwnd):
                u32.SetWindowTextW(hwnd, name)

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
        devs = self.shown()
        if not devs:
            return [(ICONS["headset"], "…" if self.result is None else "—", None)]
        return [(ICONS[d.kind], f"{d.state[0]}%" if isinstance(d.state, tuple)
                 else f"{d.last[0]}%" if d.last else "—", d.state) for d in devs]

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
        drawn = (geom, self.items(), self.bg, notch, self.phase, self.s)
        if drawn == self.drawn:               # repainting a keyed-out window costs CPU every tick
            return
        self.drawn = drawn
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
            off = L(*OFFLINE_TEXT[dev.kind])
            if dev.last:
                return off + L(f" · было {dev.last[0]}% в {dev.last[1]}", f" · was {dev.last[0]}% at {dev.last[1]}"), sub
            return off, sub
        level, charging = st
        if charging:
            return (L("Заряд полный", "Fully charged") if level >= 100 else L("Заряжается", "Charging")), GREEN
        rates = self.cfg.get("rates")
        rate = rates.get(dev.name) if isinstance(rates, dict) else None
        if isinstance(rate, (int, float)) and rate > 0:
            return L("От батареи · хватит на ", "On battery · lasts ") + fmt_hours(level / rate), sub
        mins = int((time.time() - dev.hist[0][0]) / 60) if dev.hist else 0
        if mins < 30:
            return L(f"От батареи · считаю расход: {mins} из 30 мин",
                     f"On battery · measuring drain: {mins} of 30 min"), sub
        return L("От батареи · считаю расход: жду, пока заряд снизится",
                 "On battery · measuring drain: waiting for the level to drop"), sub

    def draw_menu(self, geom=None):
        p, c = self.px, self.pc
        light = not self.notch_now and is_light(self.bg)
        bg, fg, hl, line = (("#f9f9f9", "#1a1a1a", "#ebebeb", "#e5e5e5") if light
                            else ("#2b2b2b", "#ffffff", "#3d3d3d", "#3a3a3a"))
        if self.menu_theme != light:
            self.menu_theme = light
            dwm_style(self.pm_hwnd, dark=not light)
        pad, rh, sh = p(4), p(32), p(9)
        W = max(p(210), p(44) + max(self.f_text.measure(it[1]) for it in self.menu_now if it) + p(28))
        H = 2 * pad + sum(rh if it else sh for it in self.menu_now)
        if geom:                                     # right-aligned above the widget
            x, y, w, h = geom
            mx = min(max(x + w - W, p(8)), u32.GetSystemMetrics(0) - W - p(8))
            my = y + h + p(10) if self.notch_now else y - H - p(10)
            self.menu_geom = (mx, my, W, H)
            self.set_window(self.pm, self.menu_geom)
        c.delete("all")
        c.config(bg=bg)
        self.menu_rows, yy = [], pad
        for i, it in enumerate(self.menu_now):
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
        devs, pad, row = self.shown(), p(18), p(74)
        left = L("Обновлено в ", "Updated at ") + self.updated if self.updated else ""
        right, left_col = L("ЛКМ — обновить · ПКМ — меню", "Left click: refresh · Right click: menu"), sub
        if self.alert and self.alert[0] is None:
            left, right, left_col = self.alert[1], "", self.alert[2]
        empty = (L("Ищу устройства…", "Looking for devices…") if self.result is None
                 else L("Все устройства скрыты: правый клик → меню", "All devices are hidden: right click for the menu")
                 if self.devices else L("Беспроводные устройства не найдены", "No wireless devices found"))
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
            level = d.state[0] if isinstance(d.state, tuple) else d.last[0] if d.last else None
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
        c.create_text(pad, fy + p(17), text=left, font=self.f_small, fill=left_col, anchor="w")
        c.create_text(W - pad, fy + p(17), text=right, font=self.f_small, fill=sub, anchor="e")


def main():
    if "--once" in sys.argv:          # diagnostics; the exe has no console, hence the file
        found = read_all()
        if "--json" in sys.argv:      # for scripts, Rainmeter, agents
            report, name = json.dumps({"version": VERSION, "devices": [
                {"name": n, "kind": k, "pid": pid if isinstance(pid, str) else f"{pid:04X}", "online": isinstance(st, tuple),
                 "level": st[0] if isinstance(st, tuple) else None,
                 "charging": st[1] if isinstance(st, tuple) else None} for pid, k, n, st in found]},
                ensure_ascii=False), "diagnostics.json"
        else:                         # for bug reports: also the interfaces we didn't recognise
            w = sys.getwindowsversion()
            ifaces = [f"{VID:04X}:{d['product_id']:04X} if{d['interface_number']} usage {d['usage_page']:04X}:"
                      f"{d['usage']:04X} {d.get('product_string')!r} -> {classify(d) or 'skipped'}"
                      for d in hid.enumerate(VID)]
            report = "\n".join([f"{APP} {VERSION}, Windows {w.major}.{w.minor}.{w.build}", "", "Found:"]
                               + (list(map(str, found)) or ["no wireless Razer devices found"])
                               + ["", "Razer HID interfaces:"] + (ifaces or ["none"]))
            name = "diagnostics.txt"
        print(report)
        CFG.parent.mkdir(parents=True, exist_ok=True)
        (CFG.parent / name).write_text(report, "utf-8")
    elif "--quit" in sys.argv:
        stop_running()
    elif "--uninstall" in sys.argv:
        uninstall()
    elif getattr(sys, "frozen", False) and Path(sys.executable).parent != INSTALL_DIR and "--portable" not in sys.argv:
        install()
    else:
        waiting = "--after-update" in sys.argv or "--installed" in sys.argv   # the old copy may still be closing
        for _ in range(40 if waiting else 1):
            mutex = k32.CreateMutexW(None, False, f"Local\\{APP}")
            if k32.GetLastError() != 183:                             # ERROR_ALREADY_EXISTS
                break
            k32.CloseHandle(mutex)
            time.sleep(0.25)
        else:
            k32.SetEvent(named_event("show"))                         # already running: let it show itself
            return
        if getattr(sys, "frozen", False):
            threading.Thread(target=remove_old_exe, daemon=True).start()
            try:
                register(Path(sys.executable))                        # keeps the version shown in Settings current
            except OSError:
                pass
        App().root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:                 # no python traceback dialog for users; keep it for bug reports
        import traceback
        CFG.parent.mkdir(parents=True, exist_ok=True)
        (CFG.parent / "error.log").write_text(traceback.format_exc(), "utf-8")
