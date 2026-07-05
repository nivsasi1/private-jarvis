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
    {"name": "remember", "description": "Save a fact about the user to long-term memory "
     "(preferences, names, facts they tell you to remember).",
     "input_schema": {"type": "object", "properties": {
         "fact": {"type": "string"}}, "required": ["fact"]}},
    {"name": "recall", "description": "Search the user's long-term memory for relevant facts.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string"}}, "required": ["query"]}},
    {"name": "read_emails", "description": "Read the user's recent Gmail inbox "
     "(senders, subjects, snippets). Summarize naturally when asked.",
     "input_schema": {"type": "object", "properties": {
         "count": {"type": "integer", "description": "how many (default 8)"}}}},
    {"name": "search_emails", "description": "Search Gmail with a query like "
     "'from:bank', 'is:unread', 'subject:invoice'.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string"}}, "required": ["query"]}},
]

APPS = {
    "chrome": "chrome", "notepad": "notepad", "calc": "calc",
    "calculator": "calc", "spotify": "spotify", "explorer": "explorer",
    "cmd": "cmd", "terminal": "wt", "vscode": "code", "code": "code",
    "settings": "ms-settings:", "discord": "discord",
}


class Tools:
    def __init__(self, on_event=None, memory=None, gmail=None):
        self.on_event = on_event or (lambda s: None)
        self.browser = BrowserController()
        self.memory = memory
        self.gmail = gmail

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

    # --- memory --------------------------------------------------------------

    def t_remember(self, fact):
        self._ev(f"🧠 remembering: {fact[:30]}")
        return self.memory.remember(fact) if self.memory else "(no memory)"

    def t_recall(self, query):
        self._ev(f"🧠 recalling: {query[:30]}")
        if not self.memory:
            return "(no memory)"
        hits = self.memory.recall(query)
        return "\n".join(f"- {h}" for h in hits) if hits else "nothing relevant"

    # --- gmail ---------------------------------------------------------------

    def _emails(self, query, count):
        if not self.gmail or not self.gmail.available():
            return "(Gmail is not set up)"
        try:
            msgs = self.gmail.recent(count, query)
        except Exception as e:
            return f"(gmail error: {e})"
        if not msgs:
            return "no emails found"
        return "\n".join(f"- {m['from']}: {m['subject']} — {m['snippet'][:90]}" for m in msgs)

    def t_read_emails(self, count=8):
        self._ev("📧 reading inbox")
        return self._emails("", count)

    def t_search_emails(self, query):
        self._ev(f"📧 searching mail: {query[:30]}")
        return self._emails(query, 8)
