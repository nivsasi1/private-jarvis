import os
import sys
from pathlib import Path

import numpy as np


def _add_cuda_dlls():
    """Make pip-installed NVIDIA DLLs (cublas/cudnn) findable by ctranslate2."""
    base = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    for sub in ("cublas", "cudnn", "cuda_nvrtc"):
        p = base / sub / "bin"
        if p.is_dir():
            os.add_dll_directory(str(p))
            os.environ["PATH"] = str(p) + os.pathsep + os.environ.get("PATH", "")


class Transcriber:
    """Lazy-loads one faster-whisper model per language and keeps them warm.

    'auto' mode: the multilingual (en) model detects the language; if it hears
    Hebrew, the audio is re-run through the ivrit.ai model (the Hebrew fine-tune
    gave up language detection, so it can't be first).
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self._models: dict[str, object] = {}

    def _get_model(self, key: str):
        if key not in self._models:
            _add_cuda_dlls()
            from faster_whisper import WhisperModel  # slow import, keep it lazy
            print(f"[stt] loading model '{key}': {self.cfg.models[key]} ...")
            self._models[key] = WhisperModel(
                self.cfg.models[key],
                device=self.cfg.device,
                compute_type=self.cfg.compute_type,
            )
            print("[stt] model ready")
        return self._models[key]

    def _run(self, model, audio, language, beam_size) -> tuple[str, str]:
        prompt = ", ".join(self.cfg.dictionary) or None
        segments, info = model.transcribe(
            audio,
            language=language,
            beam_size=beam_size,
            vad_filter=True,
            initial_prompt=prompt,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        return text, info.language

    def transcribe(self, audio: np.ndarray, language: str) -> str:
        if audio.size < 1600:  # <0.1s — hotkey tap, not speech
            return ""
        if language == "auto":
            text, detected = self._run(self._get_model("en"), audio, None, self.cfg.beam_size)
            print(f"[stt] detected language: {detected}")
            if detected == "he":
                text, _ = self._run(self._get_model("he"), audio, "he", self.cfg.beam_size)
            return text
        text, _ = self._run(self._get_model(language), audio, language, self.cfg.beam_size)
        return text

    def partial(self, audio: np.ndarray, language: str) -> str:
        """Fast low-beam pass over the audio so far — live preview only."""
        if audio.size < 8000:
            return ""
        key = "en" if language == "auto" else language
        lang = None if language == "auto" else language
        text, _ = self._run(self._get_model(key), audio, lang, 1)
        return text
