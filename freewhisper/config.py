from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


@dataclass
class LLMConfig:
    enabled: bool = True
    url: str = "http://127.0.0.1:11434"
    model: str = "gemma3:12b"
    timeout_s: int = 30


@dataclass
class Config:
    hotkey: str = "ctrl+shift+space"
    command_hotkey: str = "ctrl+shift+c"
    language_toggle_hotkey: str = "ctrl+alt+l"
    language: str = "auto"
    command_language: str = "he"
    models: dict = field(default_factory=lambda: {
        "he": "ivrit-ai/whisper-large-v3-turbo-ct2",
        "en": "deepdml/faster-whisper-large-v3-turbo-ct2",
    })
    device: str = "auto"
    compute_type: str = "default"
    beam_size: int = 5
    llm: LLMConfig = field(default_factory=LLMConfig)
    dictionary: list = field(default_factory=list)
    paste_delay_ms: int = 150
    input_device: int | None = None
    overlay: bool = True
    live_typing: bool = True
    restore_clipboard: bool = False
    silence_seconds: float = 2.0
    silence_threshold: float = 0.008
    max_record_s: float = 120.0
    # --- jarvis ---
    talk_hotkey: str = "ctrl+shift+j"
    voice_he: str = "he-IL-AvriNeural"
    voice_en: str = "en-GB-RyanNeural"
    voice_rate: str = "+8%"
    anthropic_key: str = ""
    claude_model: str = "claude-sonnet-5"
    chat_model: str = "gemma3:12b"


def load(path: Path = CONFIG_PATH) -> Config:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    llm = LLMConfig(**raw.pop("llm", {}))
    known = {k: v for k, v in raw.items() if k in Config.__dataclass_fields__}
    return Config(llm=llm, **known)
