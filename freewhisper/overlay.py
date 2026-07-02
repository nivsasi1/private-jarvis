"""Always-on-top floating widget — five interchangeable DESIGNS, atelier palette.

- waves      pill panel, three rolling sine curves (Siri-style) + mic pulse ring
- equalizer  pill panel, mirrored studio bars with slowly-falling peak caps
- particles  pill panel, sparks fly out of the mic side as you speak
- orb        no box: a breathing glowing orb, chip buttons + transcript below
- capsule    a slim, almost invisible bar that glows with your voice;
             buttons appear on hover

Switch live from the tray menu (Design: cycle) or set `design:` in config.yaml.

Crucial Windows detail: the window gets WS_EX_NOACTIVATE, so clicking its
buttons NEVER steals focus from the text field you're dictating into.
Runs in the main thread; worker-thread state is read via a 60ms poll.
"""

import collections
import ctypes
import math
import random

TRANS = "#010203"
INK = "#2b2440"
EDGE = "#5b2d8e"
GRIP = "#8a7fa8"
LIVE_FG = "#cfc7e6"
X_FG = "#c9899a"
PILL_BG = "#3a3153"
MIC_COLORS = {"idle": "#5b2d8e", "rec": "#d64545", "busy": "#e8a33d", "cmd": "#4a7fd0"}
FX_MAIN = {"rec": "#e46a6a", "cmd": "#7aa5e8", "busy": "#e8a33d", "idle": GRIP}

DESIGNS = ["waves", "equalizer", "particles", "orb", "capsule"]

# pill-panel geometry (waves / equalizer / particles)
W_REC, H_REC = 380, 96
W_IDLE, H_IDLE = 252, 56
PILL_R = 26
FX_X0 = 252


def _rrect_pts(x1, y1, x2, y2, r):
    return [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
            x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]


def _blend(c1: str, c2: str, f: float) -> str:
    f = max(0.0, min(1.0, f))
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * f):02x}" for x, y in zip(a, b))


def _no_activate(tk_window):
    """WS_EX_NOACTIVATE + TOOLWINDOW: clickable but never takes focus, no alt-tab entry."""
    tk_window.update_idletasks()
    hwnd = ctypes.windll.user32.GetParent(tk_window.winfo_id()) or tk_window.winfo_id()
    GWL_EXSTYLE = -20
    style = ctypes.windll.user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    ctypes.windll.user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE,
                                           style | 0x08000000 | 0x00000080)


class Overlay:
    def __init__(self, get_state, get_language, get_level, get_live_text,
                 get_design, on_record, on_command, on_cycle_language,
                 on_copy_last, get_history, on_quit):
        import tkinter as tk
        self.tk = tk

        self.get_state = get_state
        self.get_language = get_language
        self.get_level = get_level
        self.get_live_text = get_live_text
        self.get_design = get_design
        self.get_history = get_history
        self.on_record = on_record
        self.on_copy_last = on_copy_last
        self.on_quit_cb = on_quit

        self.design = None
        self.W, self.H = W_REC, H_REC
        self.width = float(W_IDLE)
        self.height = float(H_IDLE)
        self._amp = 0.0
        self._t = 0.0
        self._pulse = 0.0
        self._flash = 0
        self._hist_win = None
        self._parts = []           # particles design
        self._peaks = []           # equalizer design
        self._hover = False        # capsule design

        root = tk.Tk()
        self.root = root
        root.title("FreeWhisper")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-transparentcolor", TRANS)
        root.attributes("-alpha", 0.97)

        self.canvas = tk.Canvas(root, bg=TRANS, highlightthickness=0)
        self.canvas.pack()

        c = self.canvas
        c.tag_bind("mic", "<Button-1>", lambda e: on_record())
        c.tag_bind("cmd", "<Button-1>", lambda e: on_command())
        c.tag_bind("lang", "<Button-1>", lambda e: on_cycle_language())
        c.tag_bind("copy", "<Button-1>", lambda e: self._copy_clicked())
        c.tag_bind("hist", "<Button-1>", lambda e: self._toggle_history())
        c.tag_bind("x", "<Button-1>", lambda e: on_quit())
        for t in ("mic", "cmd", "lang", "copy", "hist", "x", "grab"):
            c.tag_bind(t, "<Enter>", lambda e: c.config(cursor="hand2"))
            c.tag_bind(t, "<Leave>", lambda e: c.config(cursor=""))
        # drag surfaces; "grab" items also toggle recording on a clean click
        for t in ("drag", "grab"):
            c.tag_bind(t, "<ButtonPress-1>", self._press)
            c.tag_bind(t, "<B1-Motion>", self._drag)
        c.tag_bind("grab", "<ButtonRelease-1>", self._release_grab)
        c.bind("<Enter>", lambda e: self._set_hover(True))
        c.bind("<Leave>", lambda e: self._set_hover(False))

        self._drag_off = (0, 0)
        self._moved = False
        self._build(self.get_design())
        _no_activate(root)
        self._poll()

    # --- layout builders --------------------------------------------------------

    def _build(self, design):
        design = design if design in DESIGNS else "waves"
        first = self.design is None
        self.design = design
        c = self.canvas
        c.delete("all")
        self._parts, self._peaks = [], []
        self.live_backdrop = None

        keep_x = None if first else self.root.winfo_x()
        if design in ("waves", "equalizer", "particles"):
            self.W, self.H = W_REC, H_REC
            self._build_pill()
        elif design == "orb":
            self.W, self.H = 240, 176
            self._build_orb()
        else:
            self.W, self.H = 280, 84
            self._build_capsule()

        c.config(width=self.W, height=self.H)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x = self.root.winfo_x() if keep_x is not None else sw - self.W - 40
        y = self.root.winfo_y() if keep_x is not None else sh - self.H - 120
        self.root.geometry(f"{self.W}x{self.H}+{x}+{y}")

    def _build_pill(self):
        c = self.canvas
        self.width, self.height = float(W_IDLE), float(H_IDLE)
        self.bg = c.create_polygon(_rrect_pts(1, 1, W_IDLE, H_IDLE - 1, PILL_R),
                                   smooth=True, fill=INK, outline=EDGE, width=2,
                                   tags="drag")
        y = H_IDLE // 2
        c.create_text(16, y, text="⠿", fill=GRIP, font=("Segoe UI", 12), tags="drag")
        mx = 48
        self.mic_center = (mx, y)
        self.mic_circle = c.create_oval(mx - 15, y - 15, mx + 15, y + 15,
                                        fill=MIC_COLORS["idle"], outline="", tags="mic")
        self._mic_glyph(mx, y)
        c.create_text(78, y, text="⚡", fill="#e8c96a", font=("Segoe UI", 13), tags="cmd")
        c.create_polygon(_rrect_pts(94, 14, 148, H_IDLE - 14, 12), smooth=True,
                         fill=PILL_BG, outline="", tags="lang")
        self.lang_text = c.create_text(121, y, text="", fill="white",
                                       font=("Segoe UI", 11, "bold"), tags="lang")
        self.copy_btn = c.create_text(166, y, text="📋", font=("Segoe UI", 12), tags="copy")
        c.create_text(196, y, text="🕘", font=("Segoe UI", 12), tags="hist")
        c.create_text(226, y, text="✕", fill=X_FG, font=("Segoe UI", 12, "bold"), tags="x")
        self.live = c.create_text(self.W - 22, 74, text="", fill=LIVE_FG, anchor="e",
                                  font=("Segoe UI", 11), width=self.W - 44)

    def _build_orb(self):
        c = self.canvas
        cx = self.W // 2
        self.mic_center = (cx, 46)
        self.orb = c.create_oval(cx - 26, 20, cx + 26, 72, fill=MIC_COLORS["idle"],
                                 outline=EDGE, width=2, tags="grab")
        self._mic_glyph(cx, 46, tags="grab")
        xs = [cx - 68, cx - 34, cx, cx + 34, cx + 68]
        self._chip(xs[0], 100, "cmd", "⚡", "#e8c96a")
        self.lang_text = self._chip(xs[1], 100, "lang", "HE", "white", small=True)
        self.copy_btn = self._chip(xs[2], 100, "copy", "📋")
        self._chip(xs[3], 100, "hist", "🕘")
        self._chip(xs[4], 100, "x", "✕", X_FG)
        self.live_backdrop = c.create_polygon(_rrect_pts(8, 122, self.W - 8, 170, 14),
                                              smooth=True, fill=INK, outline=EDGE,
                                              width=1, state="hidden")
        self.live = c.create_text(cx, 146, text="", fill=LIVE_FG, anchor="center",
                                  font=("Segoe UI", 10), width=self.W - 32)

    def _build_capsule(self):
        c = self.canvas
        cx = self.W // 2
        self.bar = c.create_polygon(_rrect_pts(20, 62, self.W - 20, 78, 8), smooth=True,
                                    fill=INK, outline=EDGE, width=1.5, tags="grab")
        self.mic_center = (cx, 70)
        xs = [cx - 68, cx - 34, cx, cx + 34, cx + 68]
        self._chip(xs[0], 40, "cmd", "⚡", "#e8c96a", hideable=True)
        self.lang_text = self._chip(xs[1], 40, "lang", "HE", "white", small=True, hideable=True)
        self.copy_btn = self._chip(xs[2], 40, "copy", "📋", hideable=True)
        self._chip(xs[3], 40, "hist", "🕘", hideable=True)
        self._chip(xs[4], 40, "x", "✕", X_FG, hideable=True)
        self.live_backdrop = c.create_polygon(_rrect_pts(8, 2, self.W - 8, 26, 10),
                                              smooth=True, fill=INK, outline=EDGE,
                                              width=1, state="hidden")
        self.live = c.create_text(cx, 14, text="", fill=LIVE_FG, anchor="center",
                                  font=("Segoe UI", 9), width=self.W - 32)

    def _mic_glyph(self, mx, y, tags="mic"):
        c = self.canvas
        c.create_oval(mx - 5, y - 10, mx + 5, y + 2, fill="white", outline="", tags=tags)
        c.create_rectangle(mx - 2, y + 2, mx + 2, y + 7, fill="white", outline="", tags=tags)
        c.create_line(mx - 7, y + 8, mx + 7, y + 8, fill="white", width=2, tags=tags)

    def _chip(self, x, y, tag, label, color="white", small=False, hideable=False):
        c = self.canvas
        tags = (tag, "chip") if hideable else (tag,)
        c.create_oval(x - 14, y - 14, x + 14, y + 14, fill=INK, outline=EDGE,
                      width=1.5, tags=tags)
        font = ("Segoe UI", 9, "bold") if small else ("Segoe UI", 11)
        return c.create_text(x, y, text=label, fill=color, font=font, tags=tags)

    # --- interactions -------------------------------------------------------------

    def _press(self, e):
        self._drag_off = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())
        self._moved = False

    def _drag(self, e):
        dx, dy = self._drag_off
        self.root.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")
        self._moved = True
        if self._hist_win is not None:
            self._place_history()

    def _release_grab(self, e):
        if not self._moved:
            self.on_record()

    def _set_hover(self, on):
        self._hover = on

    def _copy_clicked(self):
        if self.on_copy_last():
            self._flash = 8

    def _toggle_history(self):
        if self._hist_win is not None:
            self._close_history()
            return
        tk = self.tk
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.0)
        win.configure(bg=INK, highlightbackground=EDGE, highlightthickness=2)
        items = self.get_history()
        if not items:
            tk.Label(win, text="עדיין אין היסטוריה", bg=INK, fg=GRIP,
                     font=("Segoe UI", 10), padx=14, pady=10).pack()
        for entry in list(items)[:8]:
            shown = entry if len(entry) <= 46 else entry[:45] + "…"
            row = tk.Label(win, text=shown, bg=INK, fg="#e6e0f5", anchor="e",
                           justify="right", font=("Segoe UI", 10), padx=12, pady=5,
                           width=44, cursor="hand2")
            row.pack(fill="x")
            row.bind("<Button-1>", lambda e, full=entry: self._copy_history(full, e.widget))
            row.bind("<Enter>", lambda e: e.widget.config(bg=PILL_BG))
            row.bind("<Leave>", lambda e: e.widget.config(bg=INK))
        self._hist_win = win
        _no_activate(win)
        self._place_history()
        self._fade_in(win, 0.0)

    def _copy_history(self, text, widget):
        from .injector import copy_text
        copy_text(text)
        widget.config(fg="#7be08a")
        widget.after(600, lambda: widget.config(fg="#e6e0f5"))

    def _place_history(self):
        win = self._hist_win
        win.update_idletasks()
        x = self.root.winfo_x() + self.W - win.winfo_reqwidth()
        y = self.root.winfo_y() - win.winfo_reqheight() - 8
        win.geometry(f"+{x}+{max(0, y)}")

    def _close_history(self):
        if self._hist_win is not None:
            self._hist_win.destroy()
            self._hist_win = None

    def _fade_in(self, win, alpha):
        if self._hist_win is not win:
            return
        alpha = min(0.97, alpha + 0.12)
        win.attributes("-alpha", alpha)
        if alpha < 0.97:
            win.after(20, lambda: self._fade_in(win, alpha))

    # --- per-design dynamic layers (tag "fx", redrawn every frame) ---------------

    def _fx_waves(self, state):
        c = self.canvas
        x0, x1 = FX_X0, self.width - 16
        span = x1 - x0
        if span < 40:
            return
        cy = H_IDLE / 2
        main = FX_MAIN.get(state, FX_MAIN["idle"])
        base = 2.5 + self._amp * 15
        for amp_f, cycles, speed, color in (
                (1.00, 2.2, 1.6, main),
                (0.62, 3.1, -2.3, _blend(main, INK, 0.45)),
                (0.38, 4.0, 3.0, _blend(main, INK, 0.68))):
            pts, x = [], x0
            step = max(3, int(span // 34))
            while x <= x1:
                u = (x - x0) / span
                yy = cy + math.sin(u * cycles * 2 * math.pi + self._t * speed) \
                    * base * amp_f * math.sin(math.pi * u)
                pts += [x, yy]
                x += step
            if len(pts) >= 8:
                c.create_line(*pts, fill=color, width=2.4, smooth=True,
                              capstyle="round", tags="fx")
        self._fx_pulse(state)

    def _fx_equalizer(self, state):
        c = self.canvas
        x0, x1 = FX_X0 + 6, self.width - 20
        if x1 - x0 < 40:
            return
        cy = H_IDLE / 2
        main = FX_MAIN.get(state, FX_MAIN["idle"])
        n = 13
        if len(self._peaks) != n:
            self._peaks = [3.0] * n
        gap = (x1 - x0) / n
        for i in range(n):
            wob = 0.5 + 0.5 * math.sin(self._t * 1.35 + i * 1.7) \
                * math.cos(self._t * 0.9 + i * 0.6)
            h = 2.5 + self._amp * wob * 17
            self._peaks[i] = max(self._peaks[i] - 0.55, h)
            x = x0 + gap * i + gap / 2
            c.create_line(x, cy - h, x, cy + h, fill=main, width=4,
                          capstyle="round", tags="fx")
            p = self._peaks[i] + 3
            cap = _blend(main, INK, 0.35)
            c.create_line(x - 2, cy - p, x + 2, cy - p, fill=cap, width=2, tags="fx")
            c.create_line(x - 2, cy + p, x + 2, cy + p, fill=cap, width=2, tags="fx")
        self._fx_pulse(state)

    def _fx_particles(self, state):
        c = self.canvas
        cy = H_IDLE / 2
        main = FX_MAIN.get(state, FX_MAIN["rec"])
        if state in ("rec", "cmd"):
            for _ in range(1 + int(self._amp * 5)):
                self._parts.append({
                    "x": FX_X0 + 4, "y": cy + random.uniform(-9, 9),
                    "vx": random.uniform(2.2, 5.5) * (1 + self._amp),
                    "vy": random.uniform(-1.4, 1.4),
                    "r": random.uniform(1.5, 3.2), "age": 0,
                    "life": random.uniform(14, 26)})
        alive = []
        for p in self._parts:
            p["x"] += p["vx"]
            p["y"] += p["vy"]
            p["vy"] *= 0.97
            p["age"] += 1
            if p["age"] <= p["life"] and p["x"] < self.width - 14:
                alive.append(p)
                col = _blend(main, INK, p["age"] / p["life"])
                r = p["r"] * (1 - 0.4 * p["age"] / p["life"])
                c.create_oval(p["x"] - r, p["y"] - r, p["x"] + r, p["y"] + r,
                              fill=col, outline="", tags="fx")
        self._parts = alive[-150:]
        self._fx_pulse(state)

    def _fx_pulse(self, state):
        if state not in ("rec", "cmd"):
            self._pulse = 0.0
            return
        self._pulse = (self._pulse + 0.055 + self._amp * 0.06) % 1.0
        mx, my = self.mic_center
        r = 16 + self._pulse * 11
        color = _blend(MIC_COLORS[state], INK, 0.25 + self._pulse * 0.75)
        self.canvas.create_oval(mx - r, my - r, mx + r, my + r,
                                outline=color, width=2, tags="fx")
        self.canvas.tag_lower("fx", "mic")

    def _fx_orb(self, state):
        c = self.canvas
        cx, cy = self.mic_center
        color = MIC_COLORS.get(state, MIC_COLORS["idle"])
        breath = 0.6 + 0.4 * math.sin(self._t * 0.8)
        r = 26 + self._amp * 11 + (2.5 * breath if state == "idle" else 0)
        c.coords(self.orb, cx - r, cy - r, cx + r, cy + r)
        c.itemconfig(self.orb, fill=_blend(color, INK, 0.12 * (1 - self._amp)))
        if state in ("rec", "cmd", "busy"):
            for offset in (0.0, 0.5):
                p = (self._pulse + offset) % 1.0
                rr = r + 4 + p * 20
                ring = _blend(color, TRANS, 0.3 + p * 0.7)
                c.create_oval(cx - rr, cy - rr, cx + rr, cy + rr,
                              outline=ring, width=2, tags="fx")
            self._pulse = (self._pulse + 0.045 + self._amp * 0.05) % 1.0
            self.canvas.tag_lower("fx", "grab")

    def _fx_capsule(self, state):
        c = self.canvas
        cx = self.W / 2
        main = FX_MAIN.get(state, FX_MAIN["idle"])
        active = state in ("rec", "cmd", "busy")
        glow_w = (30 + self._amp * (self.W - 90)) if active else 16 + 6 * math.sin(self._t * 0.7)
        col = main if active else _blend(EDGE, INK, 0.35)
        c.create_polygon(_rrect_pts(cx - glow_w, 65, cx + glow_w, 75, 5), smooth=True,
                         fill=col, outline="", tags="fx")
        show = "normal" if (self._hover or active) else "hidden"
        c.itemconfigure("chip", state=show)

    # --- render loop ---------------------------------------------------------------

    def _poll(self):
        if self.get_design() != self.design:
            self._build(self.get_design())
        state = self.get_state()
        c = self.canvas
        active = state in ("rec", "busy", "cmd")
        self._t += 0.22

        target = min(1.0, self.get_level() / 0.05) ** 0.7 if state in ("rec", "cmd") else 0.0
        self._amp += (target - self._amp) * (0.45 if target > self._amp else 0.12)

        c.itemconfig(self.lang_text, text=self.get_language().upper()[:4])
        if self._flash > 0:
            self._flash -= 1
            c.itemconfig(self.copy_btn, text="✔" if self._flash else "📋")

        c.delete("fx")
        if self.design in ("waves", "equalizer", "particles"):
            c.itemconfig(self.mic_circle, fill=MIC_COLORS.get(state, MIC_COLORS["idle"]))
            tw = self.W if active else W_IDLE
            th = H_REC - 4 if active else H_IDLE
            if abs(self.width - tw) > 0.5 or abs(self.height - th) > 0.5:
                self.width += (tw - self.width) * 0.3
                self.height += (th - self.height) * 0.3
                c.coords(self.bg, *_rrect_pts(1, 1, self.width, self.height - 1, PILL_R))
            if self.width > FX_X0 + 40:
                getattr(self, f"_fx_{self.design}")(state)
            live = self.get_live_text() if active else ""
            c.itemconfig(self.live, text=live[-90:],
                         state="normal" if self.height > 80 and live else "hidden")
        else:
            getattr(self, f"_fx_{self.design}")(state)
            live = self.get_live_text() if active else ""
            shown = "normal" if live else "hidden"
            c.itemconfig(self.live, text=live[-80:], state=shown)
            if self.live_backdrop:
                c.itemconfigure(self.live_backdrop, state=shown)

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
