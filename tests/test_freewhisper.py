from pathlib import Path

from freewhisper import config as config_mod
from freewhisper.cleaner import build_system_prompt


def test_config_loads_defaults():
    cfg = config_mod.load(Path(__file__).parent.parent / "config.yaml")
    assert cfg.language in ("auto", "he", "en")
    assert "he" in cfg.models and "en" in cfg.models
    assert cfg.llm.url.startswith("http")


def test_system_prompt_plain():
    prompt = build_system_prompt([])
    assert "Output ONLY the cleaned text" in prompt
    assert "Preserve these names" not in prompt


def test_system_prompt_with_dictionary():
    prompt = build_system_prompt(["לב התחביב", "Preact"])
    assert "לב התחביב" in prompt and "Preact" in prompt
