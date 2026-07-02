import requests

SYSTEM_PROMPT = """You are a dictation post-processor. The user dictated text by voice; you receive the raw speech-to-text transcript.
Rewrite it as clean written text in the SAME language it was dictated in:
- remove filler words (um, uh, אה, אמ, כאילו)
- fix punctuation and casing
- apply spoken self-corrections (keep only the corrected version)
- format spoken lists as numbered lists
Do NOT add content, answer questions, or translate. Output ONLY the cleaned text, nothing else.{dictionary_hint}"""


def build_system_prompt(dictionary: list[str]) -> str:
    hint = ""
    if dictionary:
        hint = "\nPreserve these names/terms exactly as spelled: " + ", ".join(dictionary)
    return SYSTEM_PROMPT.format(dictionary_hint=hint)


class Cleaner:
    def __init__(self, cfg):
        self.cfg = cfg.llm
        self.system_prompt = build_system_prompt(cfg.dictionary)

    def available(self) -> bool:
        try:
            return requests.get(f"{self.cfg.url}/api/version", timeout=2).ok
        except requests.RequestException:
            return False

    def _chat(self, system: str, user: str, temperature: float = 0.2) -> str:
        r = requests.post(
            f"{self.cfg.url}/api/chat",
            json={
                "model": self.cfg.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
                "keep_alive": -1,  # keep the model warm between dictations
                "options": {"temperature": temperature},
            },
            timeout=self.cfg.timeout_s,
        )
        r.raise_for_status()
        return r.json()["message"]["content"].strip()

    def clean(self, text: str) -> str:
        """Return cleaned text; on any failure return the raw transcript unchanged."""
        if not self.cfg.enabled or not text:
            return text
        try:
            return self._chat(self.system_prompt, text) or text
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
              "(e.g. 'translate to English' means English output). No commentary, no quotes."
        )
        user = instruction if not selected_text else f"INSTRUCTION: {instruction}\n\nTEXT:\n{selected_text}"
        try:
            return self._chat(system, user, temperature=0.3)
        except (requests.RequestException, KeyError) as e:
            print(f"[llm] command failed ({e.__class__.__name__})")
            return ""
