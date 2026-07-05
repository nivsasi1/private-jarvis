"""'Hey Jarvis' wake word via openWakeWord (pretrained, runs on CPU/ONNX).

A background thread reads the mic in 80ms frames and scores each against the
'hey_jarvis' model. On a confident hit it calls on_wake() — but only when
Jarvis is idle (is_busy() False), so it won't trigger on Jarvis's own voice
or mid-conversation. Debounced so one "hey jarvis" fires once.
"""

import threading
import time

import numpy as np


class WakeWord:
    def __init__(self, on_wake, is_busy, threshold=0.5, device=None):
        self.on_wake = on_wake
        self.is_busy = is_busy
        self.threshold = threshold
        self.device = device
        self._run = False
        self._model = None

    def available(self) -> bool:
        try:
            import openwakeword  # noqa: F401
            return True
        except Exception:
            return False

    def start(self):
        self._run = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._run = False

    def _loop(self):
        try:
            from openwakeword.model import Model
            import sounddevice as sd
        except Exception as e:
            print(f"[wake] disabled ({e})")
            return
        try:
            self._model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        except Exception as e:
            print(f"[wake] model load failed ({e})")
            return

        print("[wake] listening for 'Hey Jarvis'")
        last_fire = 0.0
        block = 1280  # 80ms @ 16kHz
        try:
            with sd.InputStream(samplerate=16000, channels=1, dtype="int16",
                                blocksize=block, device=self.device) as stream:
                while self._run:
                    data, _ = stream.read(block)
                    frame = data.reshape(-1)
                    scores = self._model.predict(frame)
                    score = scores.get("hey_jarvis", 0.0)
                    now = time.time()
                    if score >= self.threshold and not self.is_busy() \
                            and now - last_fire > 2.5:
                        last_fire = now
                        print(f"[wake] ⚡ Hey Jarvis ({score:.2f})")
                        try:
                            self._model.reset()
                        except Exception:
                            pass
                        self.on_wake()
        except Exception as e:
            print(f"[wake] stream error ({e})")
