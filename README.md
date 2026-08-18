# JARVIS Core — Refactor V1

This is the new cloud-side JARVIS brain extracted from the Mark-L project.

## What moved into the Core

- Provider-neutral AI contracts
- Gemini provider adapter
- Prompt/context building
- Long-term memory domain + PostgreSQL repository
- FastAPI HTTP API
- Environment-based configuration
- Legacy JSON memory import utility

## What intentionally did NOT move

The following belong in the future `jarvis-agent` because they control a physical device:

- PyQt UI
- microphone/speaker streams
- `open_app`
- screen/camera capture
- computer settings/control
- local filesystem operations
- desktop/browser automation
- local Git/process/system monitoring

Web/Google/GitHub/task services will be added to JARVIS Core in later V1 milestones.

## Run locally

1. Copy environment configuration:

   ```bash
   cp .env.example .env
   ```

2. Put your Gemini API key in `.env`. Do **not** copy `config/api_keys.json` from Mark-L.

3. Start PostgreSQL:

   ```bash
   docker compose up -d postgres
   ```

4. Create a virtual environment and install the project:

   ```bash
   python -m venv .venv
   source .venv/bin/activate     # Windows: .venv\\Scripts\\activate
   pip install -e ".[dev]"
   ```

5. Start the API:

   ```bash
   uvicorn app.main:app --reload
   ```

6. Open `http://localhost:8000/docs`.

## First endpoints

- `GET /api/v1/health`
- `POST /api/v1/chat`
- `GET /api/v1/memories`
- `PUT /api/v1/memories`
- `DELETE /api/v1/memories/{category}/{key}`

## Import old Mark-L memory

After PostgreSQL is running:

```bash
python scripts/import_legacy_memory.py ../Mark-L-main/memory/long_term.json
```

The importer copies memory values, not API keys or UI/device configuration.

## Architecture rule

`application` and `domain` code must not import Gemini, SQLAlchemy, FastAPI, PyQt, Windows APIs, or other vendor/platform SDKs. Those dependencies live in `infrastructure` or `api` adapters.
