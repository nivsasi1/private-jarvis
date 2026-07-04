"""Text-to-speech: edge-tts synthesis + Windows winmm (MCI) mp3 playback.

edge-tts gives excellent Hebrew and English voices for free (cloud). Playback
uses ctypes → winmm so there's no pygame/ffmpeg dependency. Hebrew vs English
voice is picked automatically from the text.
"""

import asyncio
import ctypes
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
        self._alias = None
        self._lock = threading.Lock()
        self._speaking = threading.Event()

    @property
    def speaking(self) -> bool:
        return self._speaking.is_set()

    def _synth(self, text: str, path: Path):
        voice = self.voice_he if _HEB.search(text) else self.voice_en

        async def run():
            comm = edge_tts.Communicate(text, voice, rate=self.rate)
            await comm.save(str(path))

        asyncio.new_event_loop().run_until_complete(run())

    def speak(self, text: str, on_done=None):
        """Synthesize and play in a background thread. Interrupts any current speech."""
        if not text or not text.strip():
            if on_done:
                on_done()
            return
        threading.Thread(target=self._speak_blocking, args=(text, on_done),
                          daemon=True).start()

    def _speak_blocking(self, text, on_done):
        with self._lock:
            self.stop()
            path = Path(tempfile.gettempdir()) / f"jarvis_{uuid.uuid4().hex}.mp3"
            try:
                self._synth(text, path)
                self._speaking.set()
                alias = f"jv{uuid.uuid4().hex[:8]}"
                self._alias = alias
                _mci(f'open "{path}" alias {alias}')
                _mci(f"play {alias} wait")
            except Exception as e:
                print(f"[tts] {e}")
            finally:
                if self._alias:
                    _mci(f"close {self._alias}")
                    self._alias = None
                self._speaking.clear()
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass
                if on_done:
                    on_done()

    def stop(self):
        if self._alias:
            _mci(f"stop {self._alias}")
            _mci(f"close {self._alias}")
            self._alias = None
        self._speaking.clear()
