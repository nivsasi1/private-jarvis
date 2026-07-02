import numpy as np
import sounddevice as sd

TARGET_RATE = 16000  # Whisper's native rate


class Recorder:
    """Push-to-talk mic capture: start() on key down, stop() on release."""

    def __init__(self, device: int | None = None):
        self.device = device
        self._chunks: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._rate = TARGET_RATE

    def start(self):
        if self._stream is not None:
            return
        self._chunks = []
        last_err = None
        # some mics won't open at 16kHz — fall back to their native rate and resample
        for rate in (TARGET_RATE, None):
            try:
                if rate is None:
                    info = sd.query_devices(self.device, "input")
                    rate = int(info["default_samplerate"])
                stream = sd.InputStream(
                    samplerate=rate,
                    channels=1,
                    dtype="float32",
                    device=self.device,
                    callback=lambda indata, *_: self._chunks.append(indata.copy()),
                )
                stream.start()
                self._stream, self._rate = stream, rate
                return
            except Exception as e:
                last_err = e
        raise RuntimeError(f"could not open microphone: {last_err}")

    def stop(self) -> np.ndarray:
        if self._stream is None:
            return np.zeros(0, dtype=np.float32)
        self._stream.stop()
        self._stream.close()
        self._stream = None
        return self._to_16k(self._chunks)

    def snapshot(self) -> np.ndarray:
        """Audio captured so far, without stopping — for live partial transcripts."""
        return self._to_16k(list(self._chunks))

    def _to_16k(self, chunks) -> np.ndarray:
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(chunks).flatten()
        if self._rate != TARGET_RATE and audio.size:
            n = int(audio.size * TARGET_RATE / self._rate)
            audio = np.interp(
                np.linspace(0, audio.size - 1, n), np.arange(audio.size), audio
            ).astype(np.float32)
        return audio

    @property
    def recording(self) -> bool:
        return self._stream is not None

    def recent_rms(self, window_s: float = 0.3) -> float:
        """Level of the last `window_s` seconds — used for silence auto-stop."""
        if not self._chunks:
            return 0.0
        need = int(self._rate * window_s)
        buf, total = [], 0
        for c in reversed(self._chunks):
            buf.append(c)
            total += len(c)
            if total >= need:
                break
        audio = np.concatenate(buf).flatten()
        return rms(audio[-need:])


def rms(audio: np.ndarray) -> float:
    return float(np.sqrt(np.mean(audio**2))) if audio.size else 0.0
