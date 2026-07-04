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
    "You have tools to open/close real browser pages, check weather, time, files, and launch apps. "
    "Use them when useful and narrate briefly. Before anything that spends money or is destructive, "
    "stop and ask for explicit confirmation first."
)


class Brain:
    def __init__(self, cfg, tools):
        self.cfg = cfg
        self.tools = tools
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

    def think(self, user_text: str) -> str:
        self.history.append({"role": "user", "content": user_text})
        self.history = self.history[-self.max_turns * 2:]
        reply = self._think_claude() if self.using_claude else self._think_ollama()
        self.history.append({"role": "assistant", "content": reply})
        return reply

    # --- Claude (tools) ------------------------------------------------------

    def _think_claude(self) -> str:
        from .tools import SCHEMAS
        msgs = [m for m in self.history]
        for _ in range(6):  # tool-use rounds
            resp = self._client.messages.create(
                model=getattr(self.cfg, "claude_model", "claude-sonnet-5"),
                max_tokens=1024,
                system=PERSONA,
                tools=SCHEMAS,
                messages=msgs,
            )
            if resp.stop_reason == "tool_use":
                msgs.append({"role": "assistant", "content": resp.content})
                results = []
                for block in resp.content:
                    if block.type == "tool_use":
                        out = self.tools.dispatch(block.name, block.input)
                        results.append({"type": "tool_result", "tool_use_id": block.id,
                                        "content": str(out)})
                msgs.append({"role": "user", "content": results})
                continue
            return "".join(b.text for b in resp.content if b.type == "text").strip()
        return "לא הצלחתי לסיים את הפעולה."

    # --- Ollama (chat only) --------------------------------------------------

    def _think_ollama(self) -> str:
        try:
            r = requests.post(
                f"{self.cfg.llm.url}/api/chat",
                json={
                    "model": getattr(self.cfg, "chat_model", self.cfg.llm.model),
                    "messages": [{"role": "system", "content": PERSONA}] + self.history,
                    "stream": False, "keep_alive": -1,
                    "options": {"temperature": 0.6},
                },
                timeout=self.cfg.llm.timeout_s,
            )
            r.raise_for_status()
            return r.json()["message"]["content"].strip()
        except (requests.RequestException, KeyError) as e:
            return f"(אין חיבור למוח: {e.__class__.__name__})"
