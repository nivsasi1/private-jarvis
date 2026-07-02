"""Always-on-top floating widget: live waveform + live transcript + history.

Crucial Windows detail: the window gets WS_EX_NOACTIVATE, so clicking its
buttons NEVER steals focus from the text field you're dictating into — the
paste lands where your cursor already is.

Idle: compact pill  [⠿ 🎤 ⚡ HE 📋 🕘 ✕]
Recording: expands with an eased animation — waveform bars dance with your
voice and a live partial transcript streams underneath.

Runs in the main thread (tkinter requirement); worker-thread state is read
via a 60ms poll. -transparentcolor makes unused canvas area invisible and
click-through.
"""

import collections
import ctypes

TRANS = "#010203"
INK = "#2b2440"
EDGE = "#5b2d8e"
MIC_COLORS = {"idle": "#5b2d8e", "rec": "#d64545", "busy": "#e8a33d", "cmd": "#4a7fd0"}
WAVE_COLORS = {"rec": "#e46a6a", "cmd": "#7aa5e8", "busy": "#e8a33d"}

W_REC, H_REC = 340, 96      # full canvas/window size (transparent when unused)
W_IDLE, H_IDLE = 252, 56
PILL_R = 26


def _rrect_pts(x1, y1, x2, y2, r):
    return [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
            x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]


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
                 on_record, on_command, on_cycle_language, on_copy_last,
                 get_history, on_quit):
        import tkinter as tk
        self.tk = tk

        self.get_state = get_state
        self.get_language = get_language
        self.get_level = get_level
        self.get_live_text = get_live_text
        self.get_history = get_history
        self.on_copy_last = on_copy_last
        self.levels = collections.deque([0.0] * 28, maxlen=28)
        self.width = float(W_IDLE)
        self.height = float(H_IDLE)
        self._flash = 0
        self._hist_win = None

        root = tk.Tk()
        self.root = root
        root.title("FreeWhisper")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-transparentcolor", TRANS)
        root.attributes("-alpha", 0.97)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{W_REC}x{H_REC}+{sw - W_REC - 40}+{sh - H_REC - 120}")

        c = tk.Canvas(root, width=W_REC, height=H_REC, bg=TRANS, highlightthickness=0)
        c.pack()
        self.canvas = c

        self.bg = c.create_polygon(_rrect_pts(1, 1, W_IDLE, H_IDLE - 1, PILL_R),
                                   smooth=True, fill=INK, outline=EDGE, width=2)
        y = H_IDLE // 2
        self.grip = c.create_text(16, y, text="⠿", fill="#8a7fa8", font=("Segoe UI", 12))

        mx = 48
        self.mic_circle = c.create_oval(mx - 15, y - 15, mx + 15, y + 15,
                                        fill=MIC_COLORS["idle"], outline="", tags="mic")
        c.create_oval(mx - 5, y - 10, mx + 5, y + 2, fill="white", outline="", tags="mic")
        c.create_rectangle(mx - 2, y + 2, mx + 2, y + 7, fill="white", outline="", tags="mic")
        c.create_line(mx - 7, y + 8, mx + 7, y + 8, fill="white", width=2, tags="mic")

        c.create_text(78, y, text="⚡", fill="#e8c96a", font=("Segoe UI", 13), tags="cmd")

        self.lang_pill = c.create_polygon(_rrect_pts(94, 14, 148, H_IDLE - 14, 12),
                                          smooth=True, fill="#3a3153", outline="", tags="lang")
        self.lang_text = c.create_text(121, y, text="", fill="white",
                                       font=("Segoe UI", 10, "bold"), tags="lang")

        self.copy_btn = c.create_text(166, y, text="📋", font=("Segoe UI", 12), tags="copy")
        c.create_text(196, y, text="🕘", font=("Segoe UI", 12), tags="hist")
        c.create_text(226, y, text="✕", fill="#c9899a", font=("Segoe UI", 12, "bold"), tags="x")

        self.live = c.create_text(W_REC - 22, 74, text="", fill="#cfc7e6", anchor="e",
                                  font=("Segoe UI", 10), width=W_REC - 44, tags="live")

        c.tag_bind("mic", "<Button-1>", lambda e: on_record())
        c.tag_bind("cmd", "<Button-1>", lambda e: on_command())
        c.tag_bind("lang", "<Button-1>", lambda e: on_cycle_language())
        c.tag_bind("copy", "<Button-1>", lambda e: self._copy_clicked())
        c.tag_bind("hist", "<Button-1>", lambda e: self._toggle_history())
        c.tag_bind("x", "<Button-1>", lambda e: on_quit())
        for t in ("mic", "cmd", "lang", "copy", "hist", "x"):
            c.tag_bind(t, "<Enter>", lambda e: c.config(cursor="hand2"))
            c.tag_bind(t, "<Leave>", lambda e: c.config(cursor=""))
        for item in (self.bg, self.grip):
            c.tag_bind(item, "<ButtonPress-1>", self._press)
            c.tag_bind(item, "<B1-Motion>", self._drag)

        self._drag_off = (0, 0)
        _no_activate(root)
        self._poll()

    # --- interactions ---------------------------------------------------------

    def _press(self, e):
        self._drag_off = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def _drag(self, e):
        dx, dy = self._drag_off
        self.root.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")
        if self._hist_win is not None:
            self._place_history()

    def _copy_clicked(self):
        if self.on_copy_last():
            self._flash = 8  # brief green flash = copied

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
            tk.Label(win, text="עדיין אין היסטוריה", bg=INK, fg="#8a7fa8",
                     font=("Segoe UI", 10), padx=14, pady=10).pack()
        for entry in list(items)[:8]:
            shown = entry if len(entry) <= 46 else entry[:45] + "…"
            row = tk.Label(win, text=shown, bg=INK, fg="#e6e0f5", anchor="e",
                           justify="right", font=("Segoe UI", 10), padx=12, pady=5,
                           width=44, cursor="hand2")
            row.pack(fill="x")
            row.bind("<Button-1>", lambda e, full=entry: self._copy_history(full, e.widget))
            row.bind("<Enter>", lambda e: e.widget.config(bg="#3a3153"))
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
        x = self.root.winfo_x() + W_REC - win.winfo_reqwidth()
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

    # --- render loop ----------------------------------------------------------

    def _poll(self):
        state = self.get_state()
        c = self.canvas
        active = state in ("rec", "busy", "cmd")

        c.itemconfig(self.mic_circle, fill=MIC_COLORS.get(state, MIC_COLORS["idle"]))
        c.itemconfig(self.lang_text, text=self.get_language().upper())
        if self._flash > 0:
            self._flash -= 1
            c.itemconfig(self.copy_btn, text="✔" if self._flash else "📋")

        if state in ("rec", "cmd"):
            self.levels.append(self.get_level())
        elif state == "busy":
            self.levels.append(self.levels[-1] * 0.7)
        else:
            self.levels.append(0.0)

        # eased expand/collapse (ease-out)
        tw = W_REC if active else W_IDLE
        th = H_REC - 4 if active else H_IDLE
        if abs(self.width - tw) > 0.5 or abs(self.height - th) > 0.5:
            self.width += (tw - self.width) * 0.3
            self.height += (th - self.height) * 0.3
            c.coords(self.bg, *_rrect_pts(1, 1, self.width, self.height - 1, PILL_R))

        c.delete("wave")
        if self.width > 260:
            color = WAVE_COLORS.get(state, "#8a7fa8")
            x0 = 250
            n = int((self.width - x0 - 14) // 6)
            for i, lvl in enumerate(list(self.levels)[-n:] if n > 0 else []):
                x = x0 + i * 6
                h = 2 + min(1.0, lvl / 0.06) ** 0.5 * 16
                c.create_line(x, H_IDLE / 2 - h, x, H_IDLE / 2 + h, fill=color,
                              width=3, capstyle="round", tags="wave")

        live = self.get_live_text() if active else ""
        c.itemconfig(self.live, text=live[-90:],
                     state="normal" if self.height > 80 and live else "hidden")

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
