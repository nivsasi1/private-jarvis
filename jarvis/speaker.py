"""Streaming text-to-speech: a two-stage pipeline so Jarvis can start talking
sentence-by-sentence while the rest of the reply is still being generated.

edge-tts synthesizes; Windows winmm (MCI) plays the mp3 (no pygame/ffmpeg).
- feed(gen, sentence) enqueues a sentence for the current turn.
- a SYNTH worker turns sentences into mp3 files (so the next one is ready early),
- a PLAY worker plays them strictly in order.
`gen` (turn id) invalidates stale audio when a new turn starts or on stop().
"""

import asyncio
import ctypes
import queue
import re
import tempfile
import threading
import uuid
from pathlib import Path

import edge_tts

_HEB = re.compile(r"[֐-׿]")


def _mci(cmd: str) -> str:
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.winmm.mciSendStringW(cmd, buf, 256, 0)
    return buf.value


class Speaker:
    def __init__(self, cfg):
        self.voice_he = getattr(cfg, "voice_he", "he-IL-AvriNeural")
        self.voice_en = getattr(cfg, "voice_en", "en-GB-RyanNeural")
        self.rate = getattr(cfg, "voice_rate", "+8%")
        self._gen = 0
        self._synth_q: "queue.Queue" = queue.Queue()
        self._play_q: "queue.Queue" = queue.Queue()
        self._alias = None
        self._playing = threading.Event()
        self._lock = threading.Lock()
        threading.Thread(target=self._synth_worker, daemon=True).start()
        threading.Thread(target=self._play_worker, daemon=True).start()

    @property
    def speaking(self) -> bool:
        return (self._playing.is_set() or not self._synth_q.empty()
                or not self._play_q.empty())

    # --- public API ----------------------------------------------------------

    def start_turn(self) -> int:
        """Begin a fresh utterance: bump the turn id, drop anything queued/playing."""
        with self._lock:
            self._gen += 1
            _drain(self._synth_q)
            _drain(self._play_q)
            self._stop_current()
            return self._gen

    def feed(self, gen: int, text: str):
        if text and text.strip():
            self._synth_q.put((gen, text.strip()))

    def speak(self, text: str):
        """One-shot (greeting, non-streamed replies)."""
        g = self.start_turn()
        self.feed(g, text)

    def stop(self):
        self.start_turn()

    def wait_idle(self, timeout: float = 60.0):
        import time
        end = time.time() + timeout
        while self.speaking and time.time() < end:
            time.sleep(0.05)

    # --- workers -------------------------------------------------------------

    def _synth(self, text: str) -> Path:
        voice = self.voice_he if _HEB.search(text) else self.voice_en
        path = Path(tempfile.gettempdir()) / f"jarvis_{uuid.uuid4().hex}.mp3"

        async def run():
            await edge_tts.Communicate(text, voice, rate=self.rate).save(str(path))

        asyncio.new_event_loop().run_until_complete(run())
        return path

    def _synth_worker(self):
        while True:
            gen, text = self._synth_q.get()
            if gen != self._gen:
                continue
            try:
                path = self._synth(text)
                if gen == self._gen:
                    self._play_q.put((gen, path))
                else:
                    path.unlink(missing_ok=True)
            except Exception as e:
                print(f"[tts] synth: {e}")

    def _play_worker(self):
        while True:
            gen, path = self._play_q.get()
            if gen != self._gen:
                Path(path).unlink(missing_ok=True)
                continue
            self._playing.set()
            try:
                alias = f"jv{uuid.uuid4().hex[:8]}"
                self._alias = alias
                _mci(f'open "{path}" alias {alias}')
                _mci(f"play {alias} wait")
            except Exception as e:
                print(f"[tts] play: {e}")
            finally:
                self._stop_current()
                Path(path).unlink(missing_ok=True)
                if self._play_q.empty():
                    self._playing.clear()

    def _stop_current(self):
        if self._alias:
            _mci(f"stop {self._alias}")
            _mci(f"close {self._alias}")
            self._alias = None


def _drain(q: "queue.Queue"):
    try:
        while True:
            item = q.get_nowait()
            if isinstance(item, tuple) and len(item) == 2:
                p = item[1]
                if isinstance(p, Path):
                    p.unlink(missing_ok=True)
    except queue.Empty:
        pass
