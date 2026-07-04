"""Futuristic arc-reactor HUD — Jarvis's face.

A dark circular core with rotating tick-rings and a pulsing center, plus a
caption strip that shows what you said, what Jarvis replies, and a live log of
its actions ("↗ opening booking.com"). Colors shift by state:
  idle=dim cyan · listen=bright cyan (pulses with your voice) · think=amber
  (rings spin fast) · speak=green-cyan ripples.

Runs on the main thread; worker state read via a 40ms poll. WS_EX_NOACTIVATE
keeps clicks from stealing focus. -transparentcolor gives the round shape.
"""

import ctypes
import math

TRANS = "#010203"
BG = "#0a0e14"
CYAN = "#00e5ff"
AMBER = "#ffb63e"
GREEN = "#39ffa0"
DIM = "#1b3a44"
TXT = "#bfeaf2"
STATE_COLOR = {"idle": CYAN, "listen": CYAN, "think": AMBER, "speak": GREEN}

CX, CY, R = 130, 116, 84       # core center + radius
W, H = 260, 300


def _no_activate(win):
    win.update_idletasks()
    hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
    style = ctypes.windll.user32.GetWindowLongPtrW(hwnd, -20)
    ctypes.windll.user32.SetWindowLongPtrW(hwnd, -20, style | 0x08000000 | 0x00000080)


def _blend(c1, c2, f):
    f = max(0.0, min(1.0, f))
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * f):02x}" for x, y in zip(a, b))


class HUD:
    def __init__(self, get_state, get_level, get_caption, get_log, on_talk, on_quit):
        import tkinter as tk
        self.get_state = get_state
        self.get_level = get_level
        self.get_caption = get_caption
        self.get_log = get_log

        root = tk.Tk()
        self.root = root
        root.title("Jarvis")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-transparentcolor", TRANS)
        root.attributes("-alpha", 0.96)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{W}x{H}+{sw - W - 40}+{sh - H - 80}")

        c = tk.Canvas(root, width=W, height=H, bg=TRANS, highlightthickness=0)
        c.pack()
        self.canvas = c
        self._t = 0.0
        self._amp = 0.0

        # caption panel (rounded rect) under the core
        self.panel = c.create_rectangle(10, 210, W - 10, H - 8, outline=DIM, width=1,
                                        fill=BG)
        self.cap = c.create_text(W // 2, 232, text="", fill=TXT, width=W - 30,
                                 font=("Segoe UI", 10), justify="center")
        self.logline = c.create_text(W // 2, 274, text="", fill=CYAN, width=W - 30,
                                     font=("Consolas", 8), justify="center")

        c.tag_bind("core", "<Button-1>", lambda e: on_talk())
        c.tag_bind("core", "<Enter>", lambda e: c.config(cursor="hand2"))
        c.tag_bind("core", "<Leave>", lambda e: c.config(cursor=""))
        c.tag_bind("core", "<Button-3>", lambda e: on_quit())
        # drag by panel
        c.tag_bind("drag", "<ButtonPress-1>", self._press)
        c.tag_bind("drag", "<B1-Motion>", self._drag)
        c.addtag_withtag("drag", self.panel)

        self._off = (0, 0)
        _no_activate(root)
        self._poll()

    def _press(self, e):
        self._off = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def _drag(self, e):
        dx, dy = self._off
        self.root.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")

    def _poll(self):
        c = self.canvas
        st = self.get_state()
        col = STATE_COLOR.get(st, CYAN)
        self._t += 0.06
        target = min(1.0, self.get_level() / 0.05) if st == "listen" else \
            (0.6 + 0.4 * math.sin(self._t * 6)) if st == "speak" else 0.0
        self._amp += (target - self._amp) * 0.3

        c.delete("core")

        # outer rotating tick ring
        spin = self._t * (5 if st == "think" else 1.4)
        for i in range(48):
            a = spin + i * (2 * math.pi / 48)
            long = (i % 4 == 0)
            r0 = R + 6
            r1 = R + (16 if long else 11)
            x0, y0 = CX + r0 * math.cos(a), CY + r0 * math.sin(a)
            x1, y1 = CX + r1 * math.cos(a), CY + r1 * math.sin(a)
            shade = _blend(BG, col, 0.4 + 0.6 * (0.5 + 0.5 * math.sin(a * 3 - spin)))
            c.create_line(x0, y0, x1, y1, fill=shade, width=2 if long else 1, tags="core")

        # two counter-rotating arc segments
        for k, (rr, sweep, sp) in enumerate([(R - 4, 70, -spin), (R - 16, 50, spin * 1.6)]):
            deg = math.degrees(sp)
            for seg in range(3):
                c.create_arc(CX - rr, CY - rr, CX + rr, CY + rr,
                             start=deg + seg * 120, extent=sweep,
                             style="arc", outline=col, width=2, tags="core")

        # ripple rings while speaking
        if st == "speak":
            for j in range(3):
                p = (self._t * 0.7 + j / 3) % 1.0
                rr = 24 + p * (R - 20)
                c.create_oval(CX - rr, CY - rr, CX + rr, CY + rr,
                              outline=_blend(col, BG, p), width=2, tags="core")

        # pulsing core
        cr = 20 + self._amp * 20
        c.create_oval(CX - cr - 8, CY - cr - 8, CX + cr + 8, CY + cr + 8,
                      fill=_blend(BG, col, 0.12), outline="", tags="core")
        c.create_oval(CX - cr, CY - cr, CX + cr, CY + cr,
                      fill=_blend(col, "#ffffff", 0.15 + self._amp * 0.4),
                      outline=col, width=2, tags="core")
        c.create_text(CX, CY, text="J", fill=BG, font=("Segoe UI Semibold", 18), tags="core")

        cap = self.get_caption()
        c.itemconfig(self.cap, text=cap[:180])
        log = self.get_log()
        c.itemconfig(self.logline, text=log[:60], fill=col)

        self.root.after(40, self._poll)

    def run(self):
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            pass

    def close(self):
        try:
            self.root.after(0, self.root.destroy)
        except Exception:
            pass
