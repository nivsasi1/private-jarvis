import re

import requests

# ---- deterministic intent routing (never trust the LLM to detect commands) ----

CANCEL_RE = re.compile(
    r"(תמחק הכל|מחק הכל|תמחק את זה|מחק את זה|לא אהבתי|delete everything|never ?mind|scrap that)",
    re.IGNORECASE,
)
ANSWER_RE = re.compile(
    r"^\s*(תענה לי על השאלה( הבאה)?|תענה על השאלה( הבאה)?|answer (me )?the next question|answer the question)[,.:!?]?\s*",
    re.IGNORECASE,
)
# any verb (translate/write/say, Hebrew or English, any conjugation) followed
# within a few words by a target-language phrase — catches "תתרגמו לאנגלית",
# "לכתוב באנגלית", "תתרגם את מה שאני אומר עכשיו לאנגלית", "translate to English"
_VERB = r"(תתרגמו|תתרגם|תרגמו|תרגם|לתרגם|תכתוב|תכתבו|לכתוב|כתוב|תרשום|לרשום|translate|write|say)"
TO_EN_RE = re.compile(
    _VERB + r"[^.!?]{0,40}?(לאנגלית|באנגלית|(to|in|into) english)[,.:!?]?\s*",
    re.IGNORECASE,
)
TO_HE_RE = re.compile(
    _VERB + r"[^.!?]{0,40}?(לעברית|בעברית|(to|in|into) hebrew)[,.:!?]?\s*",
    re.IGNORECASE,
)

# scripts that must never reach the user's document (qwen loves leaking Chinese)
FORBIDDEN_SCRIPTS = re.compile(r"[一-鿿぀-ヿ가-힯Ѐ-ӿ؀-ۿ฀-๿]")

# ---- prompts: small and single-purpose — local models follow these reliably ----

CLEAN_PROMPT = """You clean up voice-dictation transcripts. The user is DICTATING TEXT into a document — they are NOT talking to you.
Output the user's own words as clean written text: remove fillers (אה, אמ, כאילו, um, uh), fix punctuation (space after punctuation), apply spoken self-corrections, format spoken lists.
NEVER answer questions, never comment, never add or invent anything. A dictated question is output as a question.
The output language is the transcript's language — ONLY Hebrew or English, never any other.
Output the cleaned text only.{dictionary_hint}"""

FEWSHOT_CLEAN = [
    ("אה אז בעצם רציתי להגיד אממ שניפגש ביום שלישי לא רגע יום רביעי בשלוש",
     "אז בעצם רציתי להגיד שניפגש ביום רביעי בשעה שלוש."),
    ("האם הכפתורים יכולים להיות צבעוניים באפליקציה",
     "האם הכפתורים יכולים להיות צבעוניים באפליקציה?"),
    ("um so basically I I think we should uh go with option two",
     "So basically, I think we should go with option two."),
    ("רגע עכשיו שאני מדבר ככה זה קולט את מה שאני אומר",
     "רגע, עכשיו שאני מדבר ככה, זה קולט את מה שאני אומר."),
]

ANSWER_PROMPT = """Answer the user's question concisely and directly.
Answer in the language the question was asked in — ONLY Hebrew or English, never any other language.
Output the answer only, no preamble."""

TRANSLATE_PROMPT = """The user dictated text by voice. Rewrite it as clean written {lang}:
remove fillers (אה, אמ, um, uh), fix punctuation, apply spoken self-corrections.
If leftover instruction words like "translate to {lang}" / "תתרגם" remain in the transcript, drop them — they are the command, not the content.
Output ONLY the {lang} text, nothing else."""


def build_system_prompt(dictionary: list[str]) -> str:
    hint = ""
    if dictionary:
        hint = "\nPreserve these names/terms exactly as spelled: " + ", ".join(dictionary)
    return CLEAN_PROMPT.format(dictionary_hint=hint)


class Cleaner:
    def __init__(self, cfg):
        self.cfg = cfg.llm
        self.system_prompt = build_system_prompt(cfg.dictionary)

    def available(self) -> bool:
        try:
            return requests.get(f"{self.cfg.url}/api/version", timeout=2).ok
        except requests.RequestException:
            return False

    def _chat(self, system: str, user: str, temperature: float = 0.2, fewshot=None) -> str:
        messages = [{"role": "system", "content": system}]
        for q, a in fewshot or []:
            messages.append({"role": "user", "content": q})
            messages.append({"role": "assistant", "content": a})
        messages.append({"role": "user", "content": user})
        r = requests.post(
            f"{self.cfg.url}/api/chat",
            json={
                "model": self.cfg.model,
                "messages": messages,
                "stream": False,
                "keep_alive": -1,  # keep the model warm between dictations
                "options": {"temperature": temperature},
            },
            timeout=self.cfg.timeout_s,
        )
        r.raise_for_status()
        return r.json()["message"]["content"].strip()

    def _guard(self, out: str, fallback: str) -> str:
        if not out or FORBIDDEN_SCRIPTS.search(out):
            print("[llm] empty/forbidden-script output — falling back")
            return fallback
        return out

    def clean(self, text: str) -> str:
        """Route by spoken intent, then clean. On any failure return the raw transcript."""
        if not self.cfg.enabled or not text:
            return text
        # cancel: detected in code, near the end of the dictation — no LLM involved
        if CANCEL_RE.search(text[-40:]):
            return "[CANCEL]"
        try:
            m = ANSWER_RE.match(text)
            if m:
                question = text[m.end():].strip()
                return self._guard(self._chat(ANSWER_PROMPT, question), text)
            for regex, lang in ((TO_EN_RE, "English"), (TO_HE_RE, "Hebrew")):
                m = regex.search(text)
                if m:
                    content = (text[:m.start()] + " " + text[m.end():]).strip()
                    return self._guard(
                        self._chat(TRANSLATE_PROMPT.format(lang=lang), content), text)
            return self._guard(
                self._chat(self.system_prompt, text, fewshot=FEWSHOT_CLEAN), text)
        except (requests.RequestException, KeyError) as e:
            print(f"[llm] cleanup skipped ({e.__class__.__name__}) — pasting raw transcript")
            return text

    def command(self, instruction: str, selected_text: str) -> str:
        """Command mode: apply a spoken instruction to the selected text (or generate)."""
        system = (
            "You are a voice command assistant. The user spoke an instruction "
            "(transcribed, may contain speech artifacts).\n"
            + ("Apply the instruction to the TEXT below and output ONLY the transformed text.\n"
               if selected_text else
               "No text is selected — produce ONLY the text the instruction asks for.\n")
            + "Keep the output language appropriate to the instruction "
              "(e.g. 'translate to English' means English output), but ONLY Hebrew or English — "
              "never any other language. No commentary, no quotes."
        )
        user = instruction if not selected_text else f"INSTRUCTION: {instruction}\n\nTEXT:\n{selected_text}"
        try:
            return self._guard(self._chat(system, user, temperature=0.3), "")
        except (requests.RequestException, KeyError) as e:
            print(f"[llm] command failed ({e.__class__.__name__})")
            return ""
