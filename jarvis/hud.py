"""Jarvis HUD — a modern AI-assistant panel.

A glassy dark card: the arc-reactor orb as a live status avatar up top, a
conversation area with chat bubbles (you vs. Jarvis) and an action log, and a
text field at the bottom so you can TYPE to Jarvis as well as talk. The orb
animates by state (idle/listen/think/speak).

Runs on the main thread; worker state read via a 60ms poll. Unlike FreeWhisper's
overlay this window IS focusable — you need that to type — but it stays
always-on-top and frameless.
"""

import ctypes
import math

TRANS = "#010203"
CARD = "#0e1220"
CARD2 = "#141a2e"
EDGE = "#1e2b45"
CYAN = "#00e5ff"
AMBER = "#ffb63e"
GREEN = "#39ffa0"
MUTE = "#5b6b86"
TXT = "#d7e3f4"
USER_BG = "#241f3a"
USER_FG = "#e7dcff"
JV_BG = "#0f2e39"
JV_FG = "#c8f6ff"
STATE_COLOR = {"idle": CYAN, "listen": CYAN, "think": AMBER, "speak": GREEN}
STATE_LABEL = {"idle": "מוכן", "listen": "מקשיב…", "think": "חושב…", "speak": "מדבר…"}

W, H = 360, 452
OCX, OCY, OR = 42, 46, 24       # mini orb


def _rr(x1, y1, x2, y2, r):
    return [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
            x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]


def _blend(c1, c2, f):
    f = max(0.0, min(1.0, f))
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * f):02x}" for x, y in zip(a, b))


class HUD:
    def __init__(self, get_state, get_level, get_user, get_reply, get_log,
                 on_talk, on_submit, on_quit):
        import tkinter as tk
        self.tk = tk
        self.get_state = get_state
        self.get_level = get_level
        self.get_user = get_user
        self.get_reply = get_reply
        self.get_log = get_log
        self.on_submit = on_submit

        root = tk.Tk()
        self.root = root
        root.title("Jarvis")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-transparentcolor", TRANS)
        root.configure(bg=TRANS)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{W}x{H}+{sw - W - 40}+{sh - H - 70}")

        c = tk.Canvas(root, width=W, height=H, bg=TRANS, highlightthickness=0)
        c.pack()
        self.canvas = c
        self._t = 0.0
        self._amp = 0.0
        self._last = None

        # card
        c.create_polygon(_rr(2, 2, W - 2, H - 2, 22), smooth=True, fill=CARD,
                         outline=EDGE, width=1.5)
        # header
        c.create_text(78, 34, text="JARVIS", anchor="w", fill=CYAN,
                      font=("Segoe UI Semibold", 15))
        c.create_text(78, 54, text="voice assistant", anchor="w", fill=MUTE,
                      font=("Segoe UI", 8))
        self.status = c.create_text(W - 20, 34, text="", anchor="e", fill=CYAN,
                                    font=("Segoe UI", 9, "bold"))
        self.x_btn = c.create_text(W - 20, 54, text="✕ סגור", anchor="e", fill=MUTE,
                                   font=("Segoe UI", 8), tags="x")
        c.create_line(20, 78, W - 20, 78, fill=EDGE)

        # conversation area is redrawn on demand (tag "conv")
        # input bar
        iy0, iy1 = H - 52, H - 16
        c.create_polygon(_rr(16, iy0, W - 74, iy1, 16), smooth=True, fill=CARD2,
                         outline=EDGE, width=1, tags="inpbg")
        self.entry_var = tk.StringVar()
        self.entry = tk.Entry(root, textvariable=self.entry_var, bd=0,
                              bg=CARD2, fg=TXT, insertbackground=CYAN,
                              font=("Segoe UI", 11), justify="right")
        c.create_window(24, (iy0 + iy1) // 2, anchor="w", window=self.entry,
                        width=W - 150, height=26)
        self.entry.bind("<Return>", self._send)
        self.entry.insert(0, "")
        self._ph = "כתוב או דבר…"
        self._set_placeholder()
        self.entry.bind("<FocusIn>", self._clear_placeholder)
        self.entry.bind("<FocusOut>", self._set_placeholder_evt)

        # send + mic buttons
        c.create_oval(W - 64, iy0, W - 64 + 34, iy0 + 34, fill=CARD2, outline=EDGE,
                      tags="send")
        c.create_text(W - 64 + 17, iy0 + 17, text="➤", fill=CYAN,
                      font=("Segoe UI", 12), tags="send")
        c.create_oval(W - 62, iy0 - 44, W - 62 + 30, iy0 - 44 + 30, fill=CARD2,
                      outline=EDGE, tags="micbtn")
        c.create_text(W - 62 + 15, iy0 - 44 + 15, text="🎤", font=("Segoe UI", 11),
                      tags="micbtn")

        c.tag_bind("x", "<Button-1>", lambda e: on_quit())
        c.tag_bind("send", "<Button-1>", lambda e: self._send())
        c.tag_bind("micbtn", "<Button-1>", lambda e: on_talk())
        c.tag_bind("orb", "<Button-1>", lambda e: on_talk())
        for t in ("x", "send", "micbtn", "orb"):
            c.tag_bind(t, "<Enter>", lambda e: c.config(cursor="hand2"))
            c.tag_bind(t, "<Leave>", lambda e: c.config(cursor=""))
        # drag by header
        c.bind("<ButtonPress-1>", self._press)
        c.bind("<B1-Motion>", self._drag)

        self._off = (0, 0)
        self._poll()

    # --- input ---------------------------------------------------------------

    def _set_placeholder(self):
        if not self.entry_var.get():
            self.entry.config(fg=MUTE)
            self.entry_var.set(self._ph)

    def _set_placeholder_evt(self, _):
        self._set_placeholder()

    def _clear_placeholder(self, _):
        if self.entry_var.get() == self._ph:
            self.entry_var.set("")
            self.entry.config(fg=TXT)

    def _send(self, _=None):
        txt = self.entry_var.get().strip()
        if not txt or txt == self._ph:
            return
        self.entry_var.set("")
        self.on_submit(txt)

    # --- drag ----------------------------------------------------------------

    def _press(self, e):
        if e.y < 78:  # only the header drags
            self._off = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())
            self._drag_ok = True
        else:
            self._drag_ok = False

    def _drag(self, e):
        if getattr(self, "_drag_ok", False):
            dx, dy = self._off
            self.root.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")

    # --- conversation --------------------------------------------------------

    def _bubble(self, y, side, text, bg, fg):
        c = self.canvas
        maxw = 236
        padx, pady = 14, 11
        tx = W - 28 if side == "right" else 28
        t = c.create_text(tx, y, text=text, anchor="n" + ("e" if side == "right" else "w"),
                          fill=fg, width=maxw, font=("Segoe UI", 11),
                          justify="right", tags="conv")
        b = c.bbox(t)
        rect = _rr(b[0] - padx, b[1] - pady, b[2] + padx, b[3] + pady, 14)
        r = c.create_polygon(rect, smooth=True, fill=bg, outline="", tags="conv")
        c.tag_lower(r, t)
        return b[3] + pady + 20      # generous gap before the next bubble

    def _redraw_conv(self):
        c = self.canvas
        c.delete("conv")
        y = 112                       # more space under the header divider
        user, reply = self.get_user(), self.get_reply()
        if user:
            y = self._bubble(y, "right", user, USER_BG, USER_FG)
        if reply:
            y = self._bubble(y, "left", reply, JV_BG, JV_FG)
        log = self.get_log()
        if log:
            c.create_text(W // 2, min(y + 6, H - 66), text=log, fill=MUTE,
                          font=("Consolas", 8), tags="conv")

    # --- orb + loop ----------------------------------------------------------

    def _draw_orb(self, col, st):
        c = self.canvas
        c.delete("orb")
        spin = self._t * (5 if st == "think" else 1.4)
        for i in range(28):
            a = spin + i * (2 * math.pi / 28)
            long = (i % 4 == 0)
            r0, r1 = OR + 3, OR + (9 if long else 6)
            c.create_line(OCX + r0 * math.cos(a), OCY + r0 * math.sin(a),
                          OCX + r1 * math.cos(a), OCY + r1 * math.sin(a),
                          fill=_blend(CARD, col, 0.4 + 0.6 * (0.5 + 0.5 * math.sin(a * 3 - spin))),
                          width=1, tags="orb")
        for rr, sweep, sp in [(OR - 2, 70, -spin), (OR - 9, 50, spin * 1.6)]:
            deg = math.degrees(sp)
            for seg in range(3):
                c.create_arc(OCX - rr, OCY - rr, OCX + rr, OCY + rr,
                             start=deg + seg * 120, extent=sweep, style="arc",
                             outline=col, width=1.5, tags="orb")
        if st == "speak":
            for j in range(2):
                p = (self._t * 0.7 + j / 2) % 1.0
                rr = 8 + p * (OR - 4)
                c.create_oval(OCX - rr, OCY - rr, OCX + rr, OCY + rr,
                              outline=_blend(col, CARD, p), width=1, tags="orb")
        cr = 8 + self._amp * 8
        c.create_oval(OCX - cr, OCY - cr, OCX + cr, OCY + cr,
                      fill=_blend(col, "#ffffff", 0.15 + self._amp * 0.4),
                      outline=col, width=1.5, tags="orb")

    def _poll(self):
        st = self.get_state()
        col = STATE_COLOR.get(st, CYAN)
        self._t += 0.06
        target = min(1.0, self.get_level() / 0.05) if st == "listen" else \
            (0.6 + 0.4 * math.sin(self._t * 6)) if st == "speak" else 0.0
        self._amp += (target - self._amp) * 0.3

        self._draw_orb(col, st)
        self.canvas.itemconfig(self.status, text=STATE_LABEL.get(st, ""), fill=col)

        sig = (self.get_user(), self.get_reply(), self.get_log())
        if sig != self._last:
            self._last = sig
            self._redraw_conv()

        self.root.after(60, self._poll)

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
