"""Jarvis's brain — hybrid: Claude (with tool-use) when an API key is present,
local Ollama for conversation otherwise.

Claude path runs a full tool-use loop: it may call tools (open pages, weather,
files...) and we feed results back until it produces a final spoken reply.
Ollama path is plain conversation (no tools) so the app is fully usable with
zero cloud and zero key.
"""

import os

import requests

PERSONA = (
    "You are Jarvis, a concise, witty personal voice assistant on the user's Windows PC. "
    "You speak back out loud, so keep replies short and natural — a sentence or two, no lists "
    "unless asked. Reply in the user's language (Hebrew or English). "
    "You have tools to open/close real browser pages, check weather, time, files, launch apps, "
    "read/search the user's Gmail (read-only — summarize inbox, find emails), "
    "see the user's screen with see_screen when they ask what's on it, and look at images they attach. "
    "You also have long-term memory: call remember() when the user shares something worth keeping "
    "(preferences, names, facts, 'remember that...'), and recall() when you need past context. "
    "Relevant memories are also injected for you each turn. "
    "Use tools when useful and narrate briefly. Before anything that spends money or is destructive, "
    "stop and ask for explicit confirmation first."
)


def _image_block(data_b64: str, media_type: str) -> dict:
    return {"type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data_b64}}


def _image_block_from_file(path):
    import base64
    import mimetypes
    try:
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        return _image_block(data, mimetypes.guess_type(path)[0] or "image/png")
    except Exception as e:
        print(f"[brain] could not read image {path}: {e}")
        return None


class Brain:
    def __init__(self, cfg, tools, memory=None):
        self.cfg = cfg
        self.tools = tools
        self.memory = memory
        self.history = []          # [{role, content}]
        self.max_turns = 12
        self.api_key = os.environ.get("ANTHROPIC_API_KEY") or getattr(cfg, "anthropic_key", "")
        self._client = None
        if self.api_key:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self.api_key)
            except Exception as e:
                print(f"[brain] anthropic init failed: {e}")

    @property
    def using_claude(self) -> bool:
        return self._client is not None

    def _system(self, user_text: str) -> str:
        """Persona + any relevant remembered facts for this turn."""
        if self.memory:
            facts = self.memory.recall(user_text, 4)
            if facts:
                return PERSONA + "\n\nמה שאתה זוכר על המשתמש:\n" + \
                    "\n".join(f"- {f}" for f in facts)
        return PERSONA

    def think(self, user_text: str, on_delta=None, on_sentence=None, image=None) -> str:
        """Return the reply. If streaming callbacks are given (Claude path),
        on_delta(chunk) fires per token and on_sentence(sentence) per finished
        sentence so the caller can show text live and speak sentence-by-sentence."""
        self.history.append({"role": "user", "content": user_text})
        self.history = self.history[-self.max_turns * 2:]
        system = self._system(user_text)
        if self.using_claude:
            reply = self._think_claude(system, on_delta, on_sentence, image=image)
        else:
            reply = self._think_ollama(system)
            if on_sentence:
                on_sentence(reply)      # local path doesn't stream — speak it whole
        self.history.append({"role": "assistant", "content": reply})
        return reply

    # --- Claude (tools) ------------------------------------------------------

    def _think_claude(self, system, on_delta=None, on_sentence=None, image=None) -> str:
        import re

        from .tools import SCHEMAS
        buf = [""]
        all_text = []
        _END = re.compile(r"[.!?…]+(?:\s|$)|\n")

        def emit(text):
            all_text.append(text)
            if on_delta:
                on_delta(text)
            buf[0] += text
            while on_sentence:
                m = _END.search(buf[0])
                if not m:
                    break
                s, buf[0] = buf[0][:m.end()].strip(), buf[0][m.end():]
                if s:
                    on_sentence(s)

        def flush():
            s = buf[0].strip()
            buf[0] = ""
            if s and on_sentence:
                on_sentence(s)

        model = getattr(self.cfg, "claude_model", "claude-sonnet-5")
        msgs = [m for m in self.history]
        if image:  # attach the picked image to the latest user turn
            blk = _image_block_from_file(image)
            if blk:
                txt = msgs[-1]["content"] if isinstance(msgs[-1].get("content"), str) else ""
                msgs[-1] = {"role": "user", "content":
                            [blk, {"type": "text", "text": txt or "(look at this image)"}]}
        try:
            for _ in range(6):  # tool-use rounds
                with self._client.messages.stream(model=model, max_tokens=1024,
                                                  system=system, tools=SCHEMAS,
                                                  messages=msgs) as stream:
                    for event in stream:
                        if event.type == "content_block_delta" and \
                                getattr(event.delta, "type", "") == "text_delta":
                            emit(event.delta.text)
                    final = stream.get_final_message()
                flush()  # speak any remaining narration before running tools
                if final.stop_reason == "tool_use":
                    msgs.append({"role": "assistant", "content": final.content})
                    results = []
                    for block in final.content:
                        if block.type == "tool_use":
                            out = self.tools.dispatch(block.name, block.input)
                            if isinstance(out, dict) and "__image__" in out:
                                content = [_image_block(out["__image__"], out["media_type"]),
                                           {"type": "text", "text": "(the user's current screen)"}]
                            else:
                                content = str(out)
                            results.append({"type": "tool_result", "tool_use_id": block.id,
                                            "content": content})
                    msgs.append({"role": "user", "content": results})
                    continue
                break
        except Exception as e:                       # bad key/model/network — don't crash the turn
            print(f"[brain] Claude error: {e}")
            if not all_text:
                return "מצטער, נתקלתי בבעיה עם המוח בענן."
        return "".join(all_text).strip() or "לא הצלחתי לסיים."

    # --- Ollama (chat only) --------------------------------------------------

    def _think_ollama(self, system) -> str:
        try:
            r = requests.post(
                f"{self.cfg.llm.url}/api/chat",
                json={
                    "model": getattr(self.cfg, "chat_model", self.cfg.llm.model),
                    "messages": [{"role": "system", "content": system}] + self.history,
                    "stream": False, "keep_alive": -1,
                    "options": {"temperature": 0.6},
                },
                timeout=self.cfg.llm.timeout_s,
            )
            r.raise_for_status()
            return r.json()["message"]["content"].strip()
        except (requests.RequestException, KeyError) as e:
            return f"(אין חיבור למוח: {e.__class__.__name__})"
