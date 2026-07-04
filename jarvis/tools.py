"""Tool registry Jarvis can call: computer, web, and the visible browser.

Each tool has an Anthropic-style schema (for Claude tool-use) and a Python
handler. `dispatch` runs a handler and returns a short human string; it also
emits a one-line HUD event via the on_event callback so you see what Jarvis
is doing ("↗ opening booking.com").
"""

import datetime
import glob
import os
import subprocess
from pathlib import Path

import requests

from .browser import BrowserController

SCHEMAS = [
    {"name": "get_time", "description": "Current date and time.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_weather", "description": "Current weather for a place.",
     "input_schema": {"type": "object", "properties": {
         "location": {"type": "string"}}, "required": ["location"]}},
    {"name": "open_app", "description": "Launch a Windows app by name (chrome, spotify, notepad, calc...).",
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string"}}, "required": ["name"]}},
    {"name": "search_files", "description": "Find files by name under the user's home folder.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string"}}, "required": ["query"]}},
    {"name": "open_url", "description": "Open a web page in the visible browser so the user sees it.",
     "input_schema": {"type": "object", "properties": {
         "url": {"type": "string"}}, "required": ["url"]}},
    {"name": "read_page", "description": "Read the text of the currently open browser page.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "close_tab", "description": "Close the last opened browser tab.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "close_browser", "description": "Close all browser tabs.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "list_tabs", "description": "List open browser tabs.",
     "input_schema": {"type": "object", "properties": {}}},
]

APPS = {
    "chrome": "chrome", "notepad": "notepad", "calc": "calc",
    "calculator": "calc", "spotify": "spotify", "explorer": "explorer",
    "cmd": "cmd", "terminal": "wt", "vscode": "code", "code": "code",
    "settings": "ms-settings:", "discord": "discord",
}


class Tools:
    def __init__(self, on_event=None):
        self.on_event = on_event or (lambda s: None)
        self.browser = BrowserController()

    def _ev(self, s):
        self.on_event(s)

    def dispatch(self, name, args) -> str:
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            return f"(unknown tool {name})"
        try:
            return fn(**(args or {}))
        except Exception as e:
            return f"(tool {name} failed: {e})"

    # --- computer ------------------------------------------------------------

    def t_get_time(self):
        now = datetime.datetime.now()
        self._ev("🕐 checking the time")
        return now.strftime("%A %d %B %Y, %H:%M")

    def t_get_weather(self, location):
        self._ev(f"☁ weather for {location}")
        r = requests.get(f"https://wttr.in/{location}",
                         params={"format": "%l: %C %t, feels %f, wind %w"}, timeout=10)
        return r.text.strip()

    def t_open_app(self, name):
        key = name.lower().strip()
        target = APPS.get(key, key)
        self._ev(f"▶ opening {name}")
        os.startfile(target) if ":" in target else subprocess.Popen(
            f'start "" "{target}"', shell=True)
        return f"opened {name}"

    def t_search_files(self, query):
        self._ev(f"🔎 searching files: {query}")
        home = Path.home()
        hits = []
        for pat in (f"**/*{query}*",):
            hits += glob.glob(str(home / pat), recursive=True)[:20]
        hits = hits[:12]
        return "\n".join(hits) if hits else "no files found"

    # --- browser -------------------------------------------------------------

    def t_open_url(self, url):
        self._ev(f"↗ opening {url}")
        return f"opened: {self.browser.open(url)}"

    def t_read_page(self):
        self._ev("📄 reading the page")
        txt = self.browser.read_current()
        return txt or "no page open"

    def t_close_tab(self):
        self._ev("✕ closing tab")
        return f"closed: {self.browser.close_last()}"

    def t_close_browser(self):
        self._ev("✕ closing browser")
        return f"closed {self.browser.close_all()} tabs"

    def t_list_tabs(self):
        tabs = self.browser.list_pages()
        return "\n".join(tabs) if tabs else "no tabs open"
