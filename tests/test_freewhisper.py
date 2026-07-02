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
    assert "Output the cleaned text only" in prompt
    assert "Preserve these names" not in prompt


def test_cancel_and_routing_regexes():
    from freewhisper.cleaner import ANSWER_RE, CANCEL_RE, TO_EN_RE, TO_HE_RE
    assert CANCEL_RE.search("אה לא רגע תמחק הכל")
    assert not CANCEL_RE.search("בסדר גמור וכדאי לראות")
    assert ANSWER_RE.match("תענה לי על השאלה הבאה כמה זה שתיים")
    assert ANSWER_RE.match("answer the next question what time is it")
    # every phrasing the user actually tried must route to translate
    for t in (
        "תכתוב את זה באנגלית היי אמא",
        "תתרגמו לאנגלית מה איתכם",
        "לכתוב באנגלית, מה איתכם",
        "תתרגם את מה שאני אומר עכשיו לאנגלית מה איתכם",
        "translate to English what about you",
        "Translate to English. What about you?",
    ):
        assert TO_EN_RE.search(t), t
    assert TO_HE_RE.search("translate to Hebrew how are you")
    assert TO_HE_RE.search("תתרגם את זה לעברית how are you")
    assert not TO_EN_RE.search("אני אוהב לדבר אנגלית עם חברים")


def test_system_prompt_with_dictionary():
    prompt = build_system_prompt(["לב התחביב", "Preact"])
    assert "לב התחביב" in prompt and "Preact" in prompt
