# Mark-L → JARVIS Core migration map

## Migrated now

| Mark-L | JARVIS Core | Change |
|---|---|---|
| `core/prompt.txt` | `app/prompts/legacy_mark_l_prompt.txt` | Preserved as migration reference; a smaller provider-neutral prompt is now active. |
| `memory/memory_manager.py` formatting concepts | `app/application/prompt_builder.py` | Prompt formatting is separated from persistence. |
| `memory/long_term.json` persistence model | `MemoryRepository` + PostgreSQL implementation | JSON is no longer a core dependency. |
| direct `google.genai` calls in `main.py` | `app/infrastructure/ai/gemini.py` | Gemini is behind `AIProvider`. |
| application orchestration in `JarvisLive` | `AssistantService` | First extracted use case. |
| `config/api_keys.json` | `pydantic-settings` + `.env` | Secrets are never copied into source. |
| local dashboard FastAPI bootstrap | `app/main.py` + routers | Replaced with a cloud-oriented FastAPI application. |

## Deliberately deferred to `jarvis-agent`

- `actions/open_app.py`
- `actions/computer_control.py`
- `actions/computer_settings.py`
- `actions/file_controller.py`
- `actions/screen_processor.py`
- `actions/desktop.py`
- local microphone/speaker handling from `main.py`
- PyQt `ui.py`
- local system monitoring

These operate on a physical device and must not live in the cloud brain.

## Deferred Core features

- device registry / WebSocket gateway
- MCP host and permission engine
- tasks and reminders
- conversation persistence and summarization
- OpenAI / Claude providers
- Google OAuth / Calendar
- GitHub MCP
- notifications
- RAG / pgvector
