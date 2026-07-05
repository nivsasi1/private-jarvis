"""Multi-monitor placement (Windows). Lets the widgets open on a chosen monitor.

`pick("secondary")` returns the work area of the left-most non-primary monitor
(the user's smaller left screen); falls back to the only/primary monitor.
`corner(...)` computes a bottom-right origin on that monitor.
"""

import ctypes


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", ctypes.c_ulong)]


def list_monitors():
    out = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.POINTER(_RECT), ctypes.c_double)

    def _cb(hmon, hdc, lprc, data):
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        w = mi.rcWork  # excludes the taskbar
        out.append({"x": w.left, "y": w.top, "w": w.right - w.left,
                    "h": w.bottom - w.top, "primary": bool(mi.dwFlags & 1)})
        return 1

    try:
        ctypes.windll.user32.EnumDisplayMonitors(0, 0, proc(_cb), 0)
    except Exception:
        pass
    return out


def pick(which="secondary"):
    mons = list_monitors()
    if not mons:
        return None
    if isinstance(which, int):
        return mons[which] if 0 <= which < len(mons) else mons[0]
    if which == "primary":
        return next((m for m in mons if m["primary"]), mons[0])
    # secondary → left-most non-primary; else the only monitor
    non = [m for m in mons if not m["primary"]]
    return min(non, key=lambda m: m["x"]) if non else mons[0]


def corner(which, w, h, margin_r=16, margin_b=16):
    """Bottom-right origin (top-left x,y) for a w×h window on the chosen monitor."""
    m = pick(which)
    if not m:
        return None
    return (m["x"] + m["w"] - w - margin_r, m["y"] + m["h"] - h - margin_b)
