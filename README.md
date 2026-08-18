# JARVIS Core

JARVIS Core is the primary backend and orchestration service for JARVIS. It
provides the cloud-side AI, memory, prompt construction, and HTTP interfaces
used by JARVIS clients and device agents.

## Capabilities

- Provider-neutral AI contracts
- Gemini AI provider integration
- Prompt and runtime-context construction
- Long-term memory backed by PostgreSQL
- FastAPI endpoints for chat, health, and memory management
- Environment-based configuration
- Unit, API, database-integration, and live-provider tests

## Architectural scope

JARVIS Core owns reasoning, orchestration, persistence, and network APIs.
Device-specific capabilities such as microphones, speakers, cameras, desktop
automation, local files, and operating-system control belong in separate agent
applications that communicate with the Core through explicit APIs.

## Project structure

```text
app/
  api/              FastAPI routes, schemas, and dependency wiring
  application/      Provider-independent use cases and prompt construction
  core/             Application configuration
  domain/           AI and memory contracts
  infrastructure/   Database repositories and AI provider adapters
  prompts/          System prompt resources
migrations/         Alembic environment and versioned schema changes
scripts/            Operational utilities
tests/              Unit, API, integration, and live smoke tests
alembic.ini          Alembic command configuration
```

The dependency direction is inward: `domain` and `application` define the
business behavior, while `api` and `infrastructure` provide external adapters.

## Requirements

- Python 3.12 or newer
- Docker with Docker Compose for the local PostgreSQL service
- A Gemini API key when Gemini or the live AI test is enabled

## Run locally

1. Create the local environment file:

   ```powershell
   Copy-Item .env.example .env
   ```

   On macOS or Linux, use `cp .env.example .env`.

2. Configure `JARVIS_GEMINI_API_KEY` in `.env` and review the other settings.
   The local `.env` is ignored by Git and must not be committed.

3. Start PostgreSQL:

   ```powershell
   docker compose up -d postgres
   ```

4. Create a virtual environment and install the project:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -e ".[dev]"
   ```

   On macOS or Linux, activate it with `source .venv/bin/activate`.

5. Apply the database migrations:

   ```powershell
   python -m alembic upgrade head
   ```

6. Start the API:

   ```powershell
   python -m uvicorn app.main:app --reload
   ```

7. Open `http://localhost:8000/docs` for the interactive API documentation.

## Application composition

`app.main.create_app()` is the composition root. It resolves configuration once,
then the FastAPI lifespan creates one database engine, AI provider client, and
prompt builder for that application instance. Request dependencies borrow those
shared resources, while shutdown closes the provider and database reliably.
Memory and chat routes call application services and domain contracts; only the
dependency-composition layer constructs SQLAlchemy adapters.

Application startup does not create or alter tables. Schema changes are explicit,
versioned Alembic migrations that must run before the corresponding application
version is started.

## Database migrations

Apply all pending migrations and verify the database is at the latest revision:

```powershell
python -m alembic upgrade head
python -m alembic current --check-heads
```

After changing an ORM model, generate and review a candidate migration:

```powershell
python -m alembic revision --autogenerate -m "describe the schema change"
python -m alembic check
```

Never put a database URL in `alembic.ini`; migration commands read
`JARVIS_DATABASE_URL` through the same settings used by the application.

If a database already has the original `memories` table but no
`alembic_version` table, back it up and verify that its columns, constraint, and
index exactly match revision `0001_create_memories`. Only then record the
baseline without recreating the table:

```powershell
python -m alembic stamp 0001_create_memories
python -m alembic current --check-heads
```

Do not use `stamp` for an empty database; use `upgrade head` so Alembic actually
creates the schema.

The safer project command performs that schema comparison before it stamps an
existing database:

```powershell
python scripts/adopt_alembic_baseline.py
```

## Operational utilities

Before importing a memory JSON export, confirm that the target database is at
the current migration head, then run the importer:

```powershell
python -m alembic current --check-heads
python scripts/import_legacy_memory.py C:\path\to\long_term.json
```

## Configuration

Important `.env` settings include:

- `JARVIS_ENVIRONMENT`
- `JARVIS_DATABASE_URL`
- `JARVIS_AI_PROVIDER`
- `JARVIS_GEMINI_API_KEY`
- `JARVIS_GEMINI_MODEL`
- `JARVIS_CORS_ORIGINS`
- `RUN_DB_TESTS`
- `RUN_LIVE_AI_TESTS`

Use [.env.example](.env.example) as the configuration template.

## API endpoints

- `GET /api/v1/health`
- `POST /api/v1/chat`
- `GET /api/v1/memories`
- `PUT /api/v1/memories`
- `DELETE /api/v1/memories/{category}/{key}`

Deleting an existing memory returns `204 No Content`; deleting a missing memory
returns `404 Not Found`.

## Tests

The local `.env` controls whether the PostgreSQL and Gemini checks run:

```dotenv
RUN_DB_TESTS=1
RUN_LIVE_AI_TESTS=1
```

With both enabled, the normal command runs the entire suite. The PostgreSQL test
requires the configured database to be migrated to `head`. The Gemini test makes
a real request and may consume quota:

```powershell
python -m pytest -v
```

The live check is intentionally strict: invalid credentials, an unavailable
model, or a provider region restriction fails the test instead of silently
skipping it.

Run only the safe offline suite without contacting PostgreSQL or Gemini. This
still exercises the full Alembic upgrade/check/downgrade path against an isolated
temporary SQLite database:

```powershell
python -m pytest -m "not integration and not live" -v
```

Run either external test separately:

```powershell
python -m pytest -m integration tests/test_database_integration.py -v
python -m pytest -m live tests/test_gemini_smoke.py -v
```

## Architecture boundaries

Code in `app/domain` and `app/application` must not import FastAPI,
SQLAlchemy, Gemini SDKs, operating-system APIs, or other vendor/platform
libraries. Those dependencies belong in `app/api` or `app/infrastructure`.

## Planned capabilities

- Device registration and real-time connections
- Tool execution with explicit permissions
- Tasks, reminders, and notifications
- Conversation persistence and summarization
- Additional AI providers
- Calendar, GitHub, and other service integrations
- Retrieval-augmented generation and vector search
