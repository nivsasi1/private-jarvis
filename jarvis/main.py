"""Jarvis — voice assistant loop: listen → think (tools) → speak, with a HUD.

Reuses FreeWhisper's recorder + transcriber for speech-to-text, adds a hybrid
brain (Claude tools / local Ollama), edge-tts voice, and the arc-reactor HUD.
"""

import argparse
import os
import sys
import threading
import time
import traceback
from collections import deque
from pathlib import Path

from freewhisper import config as config_mod
from freewhisper.recorder import Recorder, rms
from freewhisper.transcriber import Transcriber

from .brain import Brain
from .speaker import Speaker
from .tools import Tools

LOG_PATH = Path(__file__).resolve().parent.parent / "jarvis.log"


def _log_if_windowless():
    if sys.stdout is None or sys.stderr is None:
        f = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = f


class Jarvis:
    def __init__(self, cfg):
        self.cfg = cfg
        self.recorder = Recorder(cfg.input_device)
        self.stt = Transcriber(cfg)
        self.speaker = Speaker(cfg)
        self.tools = Tools(on_event=self._on_tool)
        self.brain = Brain(cfg, self.tools)
        self.state = "idle"        # idle | listen | think | speak
        self.user_text = ""
        self.reply = ""
        self.logline = ""
        self._hud = None
        self.busy = threading.Lock()

    def _on_tool(self, msg):
        self.logline = msg
        print(f"[tool] {msg}")

    # --- turn lifecycle ------------------------------------------------------

    def talk(self):
        if self.state == "listen":
            self._stop_and_process()
        elif self.state == "idle":
            self._start_listen()
        elif self.state == "speak":
            self.speaker.stop()      # barge-in: silence Jarvis and listen again
            self._start_listen()

    def submit_text(self, text):
        """Typed input from the HUD field — same brain path as voice, no STT."""
        text = (text or "").strip()
        if not text or self.state in ("think", "speak"):
            return
        if self.recorder.recording:
            self.recorder.stop()
        threading.Thread(target=self._respond, args=(text,), daemon=True).start()

    def _start_listen(self):
        try:
            self.recorder.start()
            self.state = "listen"
            self.user_text = "…"
            print("[rec] ● listening")
            threading.Thread(target=self._watchdog, daemon=True).start()
        except Exception as e:
            print(f"[rec] mic error: {e}")

    def _watchdog(self):
        started = last = time.time()
        while self.state == "listen" and self.recorder.recording:
            time.sleep(0.1)
            now = time.time()
            if self.recorder.recent_rms(0.3) > self.cfg.silence_threshold:
                last = now
            if now - last >= self.cfg.silence_seconds and now - started > 1.0:
                self._stop_and_process()
                return
            if now - started >= self.cfg.max_record_s:
                self._stop_and_process()
                return

    def _stop_and_process(self):
        if not self.recorder.recording:
            return
        audio = self.recorder.stop()
        print(f"[rec] ■ {audio.size / 16000:.1f}s, level={rms(audio):.4f}")
        threading.Thread(target=self._process, args=(audio,), daemon=True).start()

    def _process(self, audio):
        with self.busy:
            self.state = "think"
            text = self.stt.transcribe(audio, self.cfg.language)
            if not text:
                self.user_text = "לא שמעתי"
                self.state = "idle"
                return
        self._respond(text)

    def _respond(self, text):
        with self.busy:
            self.state = "think"
            try:
                print(f"[you] {text}")
                self.user_text = text
                self.reply = ""
                reply = self.brain.think(text)
                print(f"[jarvis] {reply}")
                self.reply = reply
                self.state = "speak"
                done = threading.Event()
                self.speaker.speak(reply, on_done=done.set)
                done.wait(60)
            except Exception:
                traceback.print_exc()
            finally:
                self.state = "idle"
                self.logline = ""

    # --- lifecycle -----------------------------------------------------------

    def quit(self, *_):
        print("[app] quit")
        try:
            import keyboard
            keyboard.unhook_all()
        except Exception:
            pass
        if self._hud:
            self._hud.close()
        threading.Timer(0.4, lambda: os._exit(0)).start()

    def run(self):
        import keyboard
        keyboard.add_hotkey(self.cfg.talk_hotkey, self.talk)

        brain_kind = "Claude (tools)" if self.brain.using_claude else "local Ollama (chat)"
        print(f"Jarvis online. Brain: {brain_kind}. "
              f"Press {self.cfg.talk_hotkey} or click the core to talk.")
        greet = "ג'רוויס מוכן." if not self.brain.using_claude else "Jarvis online and ready."
        self.speaker.speak(greet)

        try:
            from .hud import HUD
            self._hud = HUD(
                get_state=lambda: self.state,
                get_level=lambda: self.recorder.recent_rms(0.08) if self.recorder.recording else 0.0,
                get_user=lambda: self.user_text,
                get_reply=lambda: self.reply,
                get_log=lambda: self.logline,
                on_talk=self.talk,
                on_submit=self.submit_text,
                on_quit=self.quit,
            )
            self._hud.run()
            self.quit()
        except Exception as e:
            print(f"[hud] {e}")
            traceback.print_exc()
            threading.Event().wait()


def _lock():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 47815))
        return s
    except OSError:
        print("[app] Jarvis already running")
        sys.exit(0)


def main():
    _log_if_windowless()
    ap = argparse.ArgumentParser(prog="jarvis")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    cfg = config_mod.load()
    if args.check:
        b = Brain(cfg, Tools())
        print("brain :", "Claude" if b.using_claude else "Ollama (no ANTHROPIC_API_KEY)")
        print("talk  :", cfg.talk_hotkey)
        print("voice :", cfg.voice_he, "/", cfg.voice_en)
        return
    _keep = _lock()
    Jarvis(cfg).run()


if __name__ == "__main__":
    main()
