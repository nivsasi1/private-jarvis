import time

import keyboard
import pyperclip


def paste_text(text: str, delay_ms: int = 150):
    """Inject text into the focused window via clipboard + Ctrl+V.

    Clipboard-paste (not simulated typing) is required for Hebrew/RTL text.
    Restores the previous clipboard afterwards.
    """
    if not text:
        return
    try:
        previous = pyperclip.paste()
    except pyperclip.PyperclipException:
        previous = None
    pyperclip.copy(text)
    time.sleep(delay_ms / 1000)
    keyboard.send("ctrl+v")
    time.sleep(delay_ms / 1000)
    if previous is not None:
        pyperclip.copy(previous)


def copy_text(text: str):
    """Just put text on the clipboard (for the copy button / history)."""
    pyperclip.copy(text)


def grab_selection(delay_ms: int = 150) -> str:
    """Copy whatever is selected in the focused app and return it.

    Uses the Ctrl+C trick; restores the previous clipboard. Returns "" when
    nothing is selected (clipboard unchanged after Ctrl+C).
    """
    try:
        previous = pyperclip.paste()
    except pyperclip.PyperclipException:
        previous = ""
    marker = "\x00__freewhisper__\x00"
    pyperclip.copy(marker)
    time.sleep(delay_ms / 1000)
    keyboard.send("ctrl+c")
    time.sleep(delay_ms / 1000)
    try:
        grabbed = pyperclip.paste()
    except pyperclip.PyperclipException:
        grabbed = marker
    pyperclip.copy(previous)
    return "" if grabbed == marker else grabbed
