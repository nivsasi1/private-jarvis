"""Visible browser Jarvis drives — you watch it open and close pages.

Uses Selenium (Chrome). WebDriver objects are thread-affine, so everything runs
on one dedicated browser thread fed by a command queue; callers push
(action, args) and block for the result. Selenium Manager auto-downloads the
matching chromedriver on first use — no manual setup.
"""

import queue
import threading


class BrowserController:
    def __init__(self):
        self._q: "queue.Queue" = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._started = False
        self._driver = None
        self._handles = []       # tab window-handles in open order

    def start(self):
        if not self._started:
            self._started = True
            self._thread.start()

    def _run(self):
        while True:
            action, args, result = self._q.get()
            try:
                result["value"] = getattr(self, f"_do_{action}")(*args)
            except Exception as e:
                result["error"] = str(e)
            result["event"].set()

    def _call(self, action, *args):
        self.start()
        result = {"event": threading.Event()}
        self._q.put((action, args, result))
        result["event"].wait(60)
        if result.get("error"):
            raise RuntimeError(result["error"])
        return result.get("value")

    # --- primitives run ON the browser thread --------------------------------

    def _ensure(self):
        if self._driver is None:
            from selenium import webdriver
            opts = webdriver.ChromeOptions()
            opts.add_argument("--start-maximized")
            opts.add_experimental_option("excludeSwitches", ["enable-automation"])
            opts.add_experimental_option("detach", True)
            self._driver = webdriver.Chrome(options=opts)
            self._handles = list(self._driver.window_handles)

    def _do_open(self, url):
        self._ensure()
        if "://" not in url:
            url = "https://" + url
        if self._handles and len(self._driver.window_handles) <= len(self._handles):
            self._driver.switch_to.new_window("tab")
        self._driver.get(url)
        h = self._driver.current_window_handle
        if h not in self._handles:
            self._handles.append(h)
        return self._driver.title or url

    def _do_close_last(self):
        if not self._handles:
            return "no open pages"
        h = self._handles.pop()
        self._driver.switch_to.window(h)
        title = self._driver.title
        self._driver.close()
        if self._handles:
            self._driver.switch_to.window(self._handles[-1])
        return title

    def _do_close_all(self):
        n = len(self._handles)
        if self._driver:
            self._driver.quit()
        self._driver = None
        self._handles = []
        return n

    def _do_list(self):
        titles = []
        for h in self._handles:
            self._driver.switch_to.window(h)
            titles.append(self._driver.title)
        return titles

    def _do_read(self):
        if not self._driver or not self._handles:
            return ""
        self._driver.switch_to.window(self._handles[-1])
        return self._driver.find_element("tag name", "body").text[:4000]

    # --- public API ----------------------------------------------------------

    def open(self, url):
        return self._call("open", url)

    def close_last(self):
        return self._call("close_last")

    def close_all(self):
        return self._call("close_all")

    def list_pages(self):
        return self._call("list")

    def read_current(self):
        return self._call("read")
