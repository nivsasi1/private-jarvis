# 🤖 Private Jarvis

A local, futuristic voice assistant — talk to it, it talks back, and it can drive
a real browser (you watch it open and close pages), check weather/time, launch
apps, and search your files. Forked from FreeWhisper; see [PLAN.md](PLAN.md) for the roadmap.

## Run

```powershell
.venv\Scripts\python -m jarvis --check   # brain/voice/hotkey status
.venv\Scripts\python -m jarvis           # start (arc-reactor HUD appears)
```

Or use the **Jarvis** desktop shortcut. Press **Ctrl+Shift+J** (or click the glowing
core) to talk. Stop talking and it thinks, then replies out loud. Right-click the
core to quit. The HUD glows cyan idle, brighter when listening, amber while thinking,
green while speaking — and logs its actions ("↗ opening booking.com").

## Two brains (hybrid)

| Brain | When | Can it use tools? |
|---|---|---|
| **Local Ollama** (`gemma3:12b`) | default, no key needed | ❌ conversation only |
| **Claude** (`claude-sonnet-5`) | when an Anthropic API key is set | ✅ browser, weather, files, apps |

To unlock the cloud brain + tools, paste your key into `config.yaml` (`anthropic_key:`)
or set the `ANTHROPIC_API_KEY` environment variable. **This spends your Anthropic
tokens.** Without a key, Jarvis still chats fully locally.

## Tools (Claude brain)

`get_time` · `get_weather` · `open_app` · `search_files` · `open_url` ·
`read_page` · `close_tab` · `close_browser` · `list_tabs` · `remember` · `recall`

The browser is real Chrome via Selenium — Jarvis opens/closes tabs visibly.
Anything that spends money or is destructive requires your explicit confirmation first.

## Wake word — "Hey Jarvis"

Just say **"Hey Jarvis"** (no hotkey) and it starts listening. Uses openWakeWord's
pretrained model, runs locally on CPU, and only triggers when Jarvis is idle (so it
won't wake on its own voice). Toggle with `wake_enabled` / tune `wake_threshold` in config.

## Memory

Jarvis has persistent long-term memory (SQLite + `bge-m3` embeddings via Ollama).
Say "remember that…" and it saves; relevant facts are auto-recalled into context every
turn, so it just *knows* things later ("what's my cat's name?"). Semantic, multilingual
(strong Hebrew). Stored in `jarvis_memory.db` (gitignored, stays on your machine).

## Voice

edge-tts (Microsoft, free, cloud) — Hebrew `he-IL-AvriNeural`, English `en-GB-RyanNeural`,
auto-picked per reply. This is the one cloud dependency for now; a local Hebrew TTS
swap is on the roadmap.

## What's realistic (and not)

✅ Conversation, browser navigation + reading pages, computer basics, per-turn tool use.
⚠️ Booking/checkout: Jarvis can search & compare, but stops for confirmation before paying;
expect occasional anti-bot CAPTCHAs. ❌ No barge-in echo-cancel, no long autonomous
multi-step missions (small models drift) — see PLAN.md.

## Config (in `config.yaml`)

`talk_hotkey`, `voice_he`, `voice_en`, `voice_rate`, `anthropic_key`, `claude_model`,
`chat_model`, plus all the shared STT settings (mic, silence, language).
