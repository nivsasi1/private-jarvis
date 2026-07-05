"""Jarvis HUD — a collapsible AI-assistant panel.

Collapsed by default: a small floating arc-reactor orb ("closed chat") in the
corner. When you talk (wake word / hotkey / mic) or click the orb to type, it
animates OPEN into a full chat panel — header, chat bubbles, and a text field.
After ~10s of no interaction it animates closed again.

Runs on the main thread; worker state read via a 40ms poll. The window is a
fixed frameless always-on-top canvas; -transparentcolor makes the unused area
transparent and click-through, so the collapsed orb floats free.
"""

import ctypes
import math
import time

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

W, H = 360, 464
COLLAPSE_AFTER = 10.0            # seconds idle → close
# orb positions (collapsed corner button ↔ expanded header)
ORB_C = (W - 42, H - 42, 24)     # x, y, r  collapsed
ORB_E = (42, 46, 24)             # expanded (header)
# card rects (collapsed small button ↔ expanded full panel)
RECT_C = (W - 78, H - 78, W - 6, H - 6)
RECT_E = (2, 2, W - 2, H - 2)


def _rr(x1, y1, x2, y2, r):
    return [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
            x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]


def _lerp(a, b, e):
    return a + (b - a) * e


def _blend(c1, c2, f):
    f = max(0.0, min(1.0, f))
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * f):02x}" for x, y in zip(a, b))


def _no_activate_off(win):
    # ensure the window CAN take focus (needed for the text field)
    win.update_idletasks()


class HUD:
    def __init__(self, get_state, get_level, get_user, get_reply, get_log,
                 on_talk, on_submit, on_quit, origin=None):
        import tkinter as tk
        self.tk = tk
        self.get_state = get_state
        self.get_level = get_level
        self.get_user = get_user
        self.get_reply = get_reply
        self.get_log = get_log
        self.on_talk = on_talk
        self.on_submit = on_submit
        self.on_quit = on_quit

        self.e = 0.0              # expansion 0..1 (animated)
        self.target = 0.0
        self._t = 0.0
        self._amp = 0.0
        self._last_act = time.time()
        self._last_sig = None

        root = tk.Tk()
        self.root = root
        root.title("Jarvis")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-transparentcolor", TRANS)
        root.configure(bg=TRANS)
        if origin is None:
            sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
            origin = (sw - W - 28, sh - H - 56)
        self._origin = origin
        root.geometry(f"{W}x{H}+{origin[0]}+{origin[1]}")

        c = tk.Canvas(root, width=W, height=H, bg=TRANS, highlightthickness=0)
        c.pack()
        self.canvas = c

        # text input (placed only when expanded)
        self.entry_var = tk.StringVar()
        self.entry = tk.Entry(root, textvariable=self.entry_var, bd=0, bg=CARD2,
                              fg=TXT, insertbackground=CYAN, font=("Segoe UI", 11),
                              justify="right")
        self.entry.bind("<Return>", self._send)
        self.entry.bind("<Key>", lambda e: self._touch())
        self.entry.bind("<FocusIn>", self._on_focus_in)
        self.entry.bind("<FocusOut>", self._on_focus_out)
        self._ph = "כתוב או דבר…"
        self._ph_on = False
        self._pending_image = None

        c.tag_bind("orb", "<Button-1>", lambda e: self._orb_click())
        c.tag_bind("mic", "<Button-1>", lambda e: (self._touch(), self.on_talk()))
        c.tag_bind("send", "<Button-1>", lambda e: self._send())
        c.tag_bind("attach", "<Button-1>", lambda e: self._attach())
        c.tag_bind("x", "<Button-1>", lambda e: self.on_quit())
        for t in ("orb", "mic", "send", "attach", "x"):
            c.tag_bind(t, "<Enter>", lambda e: c.config(cursor="hand2"))
            c.tag_bind(t, "<Leave>", lambda e: c.config(cursor=""))
        c.tag_bind("hdr", "<ButtonPress-1>", self._press)
        c.tag_bind("hdr", "<B1-Motion>", self._drag)
        c.bind("<Enter>", lambda e: self._touch())

        self._off = (0, 0)
        self._poll()

    # --- open/close ----------------------------------------------------------

    def _touch(self):
        self._last_act = time.time()

    def _expand(self, focus=False):
        self.target = 1.0
        self._touch()
        if focus:
            self.root.after(180, self._focus_entry)

    def _focus_entry(self):
        try:
            self.root.focus_force()
            self.entry.focus_set()
        except Exception:
            pass

    def _collapse(self):
        self.target = 0.0
        try:
            self.root.focus_set()
        except Exception:
            pass

    def _orb_click(self):
        if self.e < 0.5:          # collapsed → open for typing
            self._expand(focus=True)
        else:                      # already open → treat as talk toggle
            self._touch()
            self.on_talk()

    def _show_ph(self):
        if not self._ph_on and not self.entry_var.get():
            self._ph_on = True
            self.entry.config(fg=MUTE)
            self.entry_var.set(self._ph)

    def _clear_ph(self):
        if self._ph_on:
            self._ph_on = False
            self.entry_var.set("")
            self.entry.config(fg=TXT)

    def _on_focus_in(self, _):
        self._touch()
        self._clear_ph()

    def _on_focus_out(self, _):
        self._show_ph()

    def _attach(self):
        self._touch()
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="Attach an image",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.gif *.webp *.bmp")])
        if path:
            self._pending_image = path

    def _send(self, _=None):
        img = self._pending_image
        txt = "" if self._ph_on else self.entry_var.get().strip()
        if not txt and not img:
            return
        self.entry_var.set("")
        self._pending_image = None
        self._touch()
        self.on_submit(txt, img)

    def _press(self, e):
        self._off = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def _drag(self, e):
        dx, dy = self._off
        self.root.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")

    # --- conversation bubbles ------------------------------------------------

    def _bubble(self, y, side, text, bg, fg):
        c = self.canvas
        tx = W - 28 if side == "right" else 28
        t = c.create_text(tx, y, text=text, anchor="n" + ("e" if side == "right" else "w"),
                          fill=fg, width=236, font=("Segoe UI", 11),
                          justify="right", tags="content")
        b = c.bbox(t)
        r = c.create_polygon(_rr(b[0] - 14, b[1] - 11, b[2] + 14, b[3] + 11, 14),
                             smooth=True, fill=bg, outline="", tags="content")
        c.tag_lower(r, t)
        return b[3] + 11 + 20

    def _draw_content(self):
        c = self.canvas
        c.delete("content")
        c.create_text(78, 34, text="JARVIS", anchor="w", fill=CYAN,
                      font=("Segoe UI Semibold", 15), tags="content")
        c.create_text(78, 54, text="voice assistant", anchor="w", fill=MUTE,
                      font=("Segoe UI", 8), tags="content")
        st = self.get_state()
        c.create_text(W - 46, 34, text=STATE_LABEL.get(st, ""), anchor="e",
                      fill=STATE_COLOR.get(st, CYAN), font=("Segoe UI", 9, "bold"),
                      tags="content")
        c.create_text(W - 20, 34, text="✕", anchor="e", fill=MUTE,
                      font=("Segoe UI", 11, "bold"), tags=("content", "x"))
        c.create_line(20, 74, W - 20, 74, fill=EDGE, tags="content")
        # transparent header drag strip
        c.create_rectangle(70, 20, W - 60, 66, outline="", fill="", tags=("content", "hdr"))

        y = 100
        user, reply = self.get_user(), self.get_reply()
        if user:
            y = self._bubble(y, "right", user, USER_BG, USER_FG)
        if reply:
            y = self._bubble(y, "left", reply, JV_BG, JV_FG)
        log = self.get_log()
        if log:
            c.create_text(W // 2, min(y + 6, H - 70), text=log, fill=MUTE,
                          font=("Consolas", 8), tags="content")

        # input bar + attach / send buttons
        iy0, iy1 = H - 52, H - 16
        c.create_polygon(_rr(16, iy0, W - 108, iy1, 16), smooth=True, fill=CARD2,
                         outline=EDGE, width=1, tags="content")
        # 📎 attach — green when an image is queued
        c.create_text(W - 92, iy0 + 17, text="📎", font=("Segoe UI", 13),
                      fill=(GREEN if self._pending_image else MUTE), tags=("content", "attach"))
        c.create_oval(W - 64, iy0, W - 30, iy0 + 34, fill=CARD2, outline=EDGE, tags=("content", "send"))
        c.create_text(W - 47, iy0 + 17, text="➤", fill=CYAN, font=("Segoe UI", 12), tags=("content", "send"))

    # --- orb -----------------------------------------------------------------

    def _draw_orb(self, ox, oy, orad, col, st):
        c = self.canvas
        # calm, slow spin at rest; livelier while listening/speaking; fast when thinking
        spin = self._t * (5 if st == "think" else 0.7 if st == "idle" else 1.4)
        n = 28
        for i in range(n):
            a = spin + i * (2 * math.pi / n)
            long = (i % 4 == 0)
            r0, r1 = orad + 3, orad + (10 if long else 6)
            c.create_line(ox + r0 * math.cos(a), oy + r0 * math.sin(a),
                          ox + r1 * math.cos(a), oy + r1 * math.sin(a),
                          fill=_blend(CARD, col, 0.4 + 0.6 * (0.5 + 0.5 * math.sin(a * 3 - spin))),
                          width=1, tags="orb")
        for rr, sweep, sp in [(orad - 2, 70, -spin), (orad - 10, 50, spin * 1.6)]:
            deg = math.degrees(sp)
            for seg in range(3):
                c.create_arc(ox - rr, oy - rr, ox + rr, oy + rr, start=deg + seg * 120,
                             extent=sweep, style="arc", outline=col, width=1.5, tags="orb")
        if st == "speak":
            for j in range(2):
                p = (self._t * 0.7 + j / 2) % 1.0
                rr = orad * 0.3 + p * orad
                c.create_oval(ox - rr, oy - rr, ox + rr, oy + rr,
                              outline=_blend(col, CARD, p), width=1, tags="orb")
        cr = orad * 0.34 + self._amp * orad * 0.4
        c.create_oval(ox - cr, oy - cr, ox + cr, oy + cr,
                      fill=_blend(col, "#ffffff", 0.15 + self._amp * 0.4),
                      outline=col, width=1.5, tags="orb")

    # --- loop ----------------------------------------------------------------

    def _poll(self):
        c = self.canvas
        st = self.get_state()
        col = STATE_COLOR.get(st, CYAN)
        self._t += 0.06

        # activity: while talking/thinking/speaking, force open + keep alive
        if st != "idle":
            self.target = 1.0
            self._touch()
        elif self.target > 0.5 and not self._focused() \
                and time.time() - self._last_act > COLLAPSE_AFTER:
            self._collapse()

        # animate expansion (ease)
        self.e += (self.target - self.e) * 0.22
        e = max(0.0, min(1.0, self.e))
        ease = e * e * (3 - 2 * e)

        target_amp = min(1.0, self.get_level() / 0.05) if st == "listen" else \
            (0.6 + 0.4 * math.sin(self._t * 6)) if st == "speak" else 0.0
        self._amp += (target_amp - self._amp) * 0.3

        c.delete("orb")
        c.delete("card")

        # card grows from collapsed button to full panel
        rect = [_lerp(RECT_C[i], RECT_E[i], ease) for i in range(4)]
        rad = _lerp(22, 22, ease)
        c.create_polygon(_rr(rect[0], rect[1], rect[2], rect[3], rad), smooth=True,
                         fill=CARD, outline=EDGE, width=1.5, tags="card")

        # content only when nearly open
        if ease > 0.82:
            if not c.find_withtag("content") or self._last_sig != self._sig():
                self._last_sig = self._sig()
                self._draw_content()
            self._place_entry(True)
        else:
            c.delete("content")
            self._place_entry(False)

        # orb interpolates corner ↔ header
        ox = _lerp(ORB_C[0], ORB_E[0], ease)
        oy = _lerp(ORB_C[1], ORB_E[1], ease)
        orad = _lerp(ORB_C[2], ORB_E[2], ease)
        c.addtag_withtag("orb", c.create_oval(ox - orad - 6, oy - orad - 6,
                         ox + orad + 6, oy + orad + 6, outline="", fill="", tags="orb"))
        self._draw_orb(ox, oy, orad, col, st)
        c.tag_raise("orb")
        c.tag_raise("content")

        self.root.after(40, self._poll)

    def _sig(self):
        return (self.get_user(), self.get_reply(), self.get_log(),
                self.get_state(), bool(self._pending_image))

    def _focused(self):
        try:
            return self.root.focus_get() is self.entry
        except Exception:
            return False

    def _place_entry(self, show):
        if show:
            if not self.entry.winfo_ismapped():
                # span the input box's width so right-aligned text hugs the edge
                self.entry.place(x=26, y=H - 47, width=W - 104, height=26)
                if not self._focused():
                    self._show_ph()
        else:
            if self.entry.winfo_ismapped():
                self.entry.place_forget()

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
