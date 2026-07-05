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
from .gmail_tool import Gmail
from .memory import Memory
from .speaker import Speaker
from .tools import Tools
from .wakeword import WakeWord

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
        self.memory = Memory(cfg)
        self.gmail = Gmail()
        self.tools = Tools(on_event=self._on_tool, memory=self.memory, gmail=self.gmail)
        self.brain = Brain(cfg, self.tools, memory=self.memory)
        self.wake = WakeWord(on_wake=self.talk,
                             is_busy=lambda: self.state != "idle",
                             threshold=getattr(cfg, "wake_threshold", 0.5),
                             device=cfg.input_device)
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

    def submit_text(self, text, image=None):
        """Typed input from the HUD field — same brain path as voice, no STT.
        image (optional file path) is sent to the vision model with the message."""
        text = (text or "").strip()
        if (not text and not image) or self.state in ("think", "speak"):
            return
        if self.recorder.recording:
            self.recorder.stop()
        threading.Thread(target=self._respond, args=(text or "מה זה?", image),
                         daemon=True).start()

    def _start_listen(self):
        try:
            self.recorder.start()
            self.state = "listen"
            self.user_text = "…"
            self.reply = ""
            print("[rec] ● listening")
            threading.Thread(target=self._watchdog, daemon=True).start()
            threading.Thread(target=self._partial_loop, daemon=True).start()
        except Exception as e:
            print(f"[rec] mic error: {e}")

    def _partial_loop(self):
        """Live transcript while you speak — show your words as they're recognized."""
        while self.state == "listen" and self.recorder.recording:
            time.sleep(1.0)
            if not self.recorder.recording:
                return
            try:
                text = self.stt.partial(self.recorder.snapshot(), self.cfg.language)
                if self.recorder.recording and text:
                    self.user_text = text
            except Exception as e:
                print(f"[stt] partial: {e}")
                return

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

    def _respond(self, text, image=None):
        with self.busy:
            self.state = "think"
            try:
                print(f"[you] {text}" + (" [+image]" if image else ""))
                self.user_text = ("🖼 " if image else "") + text
                self.reply = ""
                gen = self.speaker.start_turn()
                started = [False]

                def on_delta(chunk):
                    if not started[0]:
                        started[0] = True
                        self.state = "speak"     # orb goes green when speech begins
                    self.reply += chunk

                reply = self.brain.think(
                    text, on_delta=on_delta,
                    on_sentence=lambda s: self.speaker.feed(gen, s), image=image)
                print(f"[jarvis] {reply}")
                self.reply = reply or self.reply
                self.state = "speak"
                self.speaker.wait_idle(120)      # let the queued speech finish
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
        print(f"Jarvis online. Brain: {brain_kind}. Memory: {self.memory.count()} facts. "
              f"Press {self.cfg.talk_hotkey}, say 'Hey Jarvis', or click the orb.")
        if getattr(self.cfg, "wake_enabled", True):
            self.wake.start()
        # tidy memory in the background so it doesn't bloat with duplicates
        threading.Thread(target=self.memory.consolidate, daemon=True).start()
        greet = "ג'רוויס מוכן." if not self.brain.using_claude else "Jarvis online and ready."
        self.speaker.speak(greet)

        try:
            from freewhisper import screens
            from .hud import HUD, H, W
            origin = screens.corner(self.cfg.screen, W, H, side="right", margin_x=16, margin_b=16)
            self._hud = HUD(
                get_state=lambda: self.state,
                get_level=lambda: self.recorder.recent_rms(0.08) if self.recorder.recording else 0.0,
                get_user=lambda: self.user_text,
                get_reply=lambda: self.reply,
                get_log=lambda: self.logline,
                on_talk=self.talk,
                on_submit=self.submit_text,
                on_quit=self.quit,
                origin=origin,
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
    ap.add_argument("--auth-gmail", action="store_true", help="one-time Gmail browser authorization")
    args = ap.parse_args()
    cfg = config_mod.load()
    if args.auth_gmail:
        print(Gmail().authorize())
        return
    if args.check:
        mem = Memory(cfg)
        b = Brain(cfg, Tools(), memory=mem)
        wake = WakeWord(lambda: None, lambda: False)
        from .gmail_tool import TOKEN
        gm = Gmail()
        gmail_state = ("authorized" if TOKEN.exists()
                       else "creds present — run --auth-gmail" if gm.available()
                       else "no credentials")
        print("brain :", "Claude" if b.using_claude else "Ollama (no ANTHROPIC_API_KEY)")
        print("talk  :", cfg.talk_hotkey, "| say 'Hey Jarvis'")
        print("voice :", cfg.voice_he, "/", cfg.voice_en)
        print("memory:", mem.count(), "facts")
        print("wake  :", "openWakeWord ready" if wake.available() else "unavailable")
        print("gmail :", gmail_state)
        return
    _keep = _lock()
    Jarvis(cfg).run()


if __name__ == "__main__":
    main()
