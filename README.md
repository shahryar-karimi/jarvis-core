# JARVIS Core

JARVIS Core is the primary backend and orchestration service for JARVIS. It
provides the cloud-side AI, memory, prompt construction, and HTTP interfaces
used by JARVIS clients and device agents.

## Capabilities

- Provider-neutral AI contracts
- Gemini AI provider integration
- Prompt and runtime-context construction
- Long-term memory backed by PostgreSQL
- Device pairing, registration, and real-time command routing
- A server-side capability registry for `open_app`, `files`, and `system`
- FastAPI HTTP and WebSocket interfaces
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
  application/      Provider-independent use cases and orchestration
  core/             Application configuration
  domain/           AI, memory, device, and security contracts
  infrastructure/   Database, provider, persistence, and live-routing adapters
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

2. Configure `JARVIS_GEMINI_API_KEY`, `JARVIS_OWNER_TOKEN`,
   `JARVIS_DEVICE_ADMIN_TOKEN`, and
   `JARVIS_DEVICE_CREDENTIAL_DIGEST_KEY` in `.env`. Generate a different random
   value for each secret as shown in [.env.example](.env.example). The local
   `.env` is ignored by Git and must not be committed.

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

## Deploy to a VPS

The production stack runs Caddy, one JARVIS Core process, and PostgreSQL on a
private Docker network. Only Caddy publishes host ports. Follow the
[production deployment runbook](docs/deployment.md) to configure DNS and
secrets, migrate the database, verify HTTPS/WSS, and establish backup and
rollback procedures. Start from
[`.env.production.example`](.env.production.example); never deploy the local
development Compose configuration or commit `.env.production`.

## Application composition

`app.main.create_app()` is the composition root. It resolves configuration once,
then the FastAPI lifespan creates one database engine, AI provider client,
prompt builder, device registry, identity store, pairing service, capability
registry, and connection manager for that application instance. Request
dependencies borrow those shared resources, while shutdown closes live device
sockets before the provider and database.
Memory and chat routes call application services and domain contracts; only the
dependency-composition layer constructs SQLAlchemy adapters.

Application startup does not create or alter tables. Schema changes are explicit,
versioned Alembic migrations that must run before the corresponding application
version is started. Revision `0002_persist_devices` adds the `devices` and
`device_credentials` tables; raw device bearer tokens are never columns.

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
python -m alembic upgrade head
python -m alembic current --check-heads
```

Do not use `stamp` for an empty database; use `upgrade head` so Alembic actually
creates the schema.

The safer project command performs that schema comparison before it stamps an
existing database:

```powershell
python scripts/adopt_alembic_baseline.py
python -m alembic upgrade head
python -m alembic current --check-heads
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
- `JARVIS_TRUSTED_HOSTS`
- `JARVIS_DOCS_ENABLED`
- `JARVIS_OWNER_TOKEN`
- `JARVIS_DEVICE_ADMIN_TOKEN`
- `JARVIS_DEVICE_CREDENTIAL_DIGEST_KEY`
- `JARVIS_DEVICE_PAIRING_TTL_SECONDS`
- `JARVIS_DEVICE_HEARTBEAT_INTERVAL_SECONDS`
- `JARVIS_DEVICE_COMMAND_TIMEOUT_SECONDS`
- `JARVIS_DEVICE_MAX_MESSAGE_BYTES`
- `RUN_DB_TESTS`
- `RUN_LIVE_AI_TESTS`

Use [.env.example](.env.example) as the configuration template.

## API endpoints

- `GET /api/v1/health`
- `GET /api/v1/health/ready`
- `POST /api/v1/chat`
- `GET /api/v1/memories`
- `PUT /api/v1/memories`
- `DELETE /api/v1/memories/{category}/{key}`
- `POST /api/v1/devices/pairings`
- `POST /api/v1/devices/pair`
- `GET /api/v1/devices`
- `GET /api/v1/devices/capabilities`
- `GET /api/v1/devices/{device_id}`
- `DELETE /api/v1/devices/{device_id}`
- `POST /api/v1/devices/{device_id}/commands`
- `WS /api/v1/devices/ws`

Deleting an existing memory returns `204 No Content`; deleting a missing memory
returns `404 Not Found`.

Chat and memory endpoints require a random owner token of at least 32
characters in `JARVIS_OWNER_TOKEN`:

```http
Authorization: Bearer <owner-token>
```

Generate one with:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Keep it separate from `JARVIS_DEVICE_ADMIN_TOKEN`: the owner token authorizes
private assistant data, while the device-admin token authorizes pairing,
revocation, and command dispatch. The liveness and readiness endpoints remain
public; readiness reports only whether PostgreSQL is reachable and migrations
are current, without exposing connection details. Device pairing claims use
their one-time pairing secret, and Agent WebSockets use their own device
credential. Use HTTPS outside local development so bearer credentials are
encrypted in transit.

## Device gateway

The first Hive path is now executable end to end:

```text
JARVIS Core -> Device Gateway -> WebSocket -> JARVIS Agent -> Capability Registry -> {open_app, files, system}
```

An operator first creates a one-time pairing challenge with the protected
`POST /api/v1/devices/pairings` endpoint. The challenge fixes the device's server-side
capability grants. The Agent claims it once through
`POST /api/v1/devices/pair` and
receives an opaque `jv1` device credential. Pairing secrets and device
credentials are displayed only in those responses, use `Cache-Control:
no-store`, and are retained by Core only as keyed HMAC-SHA256 digests.
Device registrations and credential digests are persisted in PostgreSQL. The
short-lived, unclaimed pairing challenges remain process-local and disappear
on restart.

Operator endpoints require this header, backed by a random token of at least 32
characters in `JARVIS_DEVICE_ADMIN_TOKEN`:

```http
Authorization: Bearer <device-admin-token>
```

Agents connect to `WS /api/v1/devices/ws` with the exact
`jarvis-device.v1` WebSocket subprotocol and their device credential in the
`Authorization` header. Credentials are never accepted in URLs. After Core's
`server.hello`, the Agent sends `device.hello`; Core computes effective
capabilities as the intersection of the Agent's advertisement and the
operator's grants, then replies with `server.ready`. Commands and results use
strict, versioned JSON envelopes and are correlated by device, session, and
command IDs. Frames are text-only and capped at 64 KiB by default.

The frozen message shapes, state machine, close codes, and compatibility rules
are specified in the normative
[`jarvis-device.v1` protocol contract](docs/protocols/jarvis-device-v1.md).

Use HTTPS/WSS outside local development, and store the Agent credential in an
OS keychain or an ACL-restricted file. Capability grants are necessary but not
sufficient: the Agent must independently enforce app, path, and action
allowlists. Core never sends raw shell code. The initial capability/action
registry permits `open_app.open`, `files.{list,read,write}`, and
`system.{get_info,get_status}`.

Keep `JARVIS_DEVICE_CREDENTIAL_DIGEST_KEY` stable and private: changing it
invalidates every issued device credential and requires re-pairing. It must be
different from both HTTP API tokens.

Online presence, active WebSockets, heartbeat state, and pending commands are
intentionally process-local and reset when Core restarts; a persisted device
then appears offline until its Agent reconnects. Run a single Core worker until
a shared message broker and presence store exist, because multiple workers
cannot route a command to another process's WebSocket.

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

- Persistent pairing challenges and pairing audit history
- Shared device routing through a message broker
- Signed or mutual-TLS device identities
- Agent-side app, path, and system-action policy adapters
- Tasks, reminders, and notifications
- Conversation persistence and summarization
- Additional AI providers
- Calendar, GitHub, and other service integrations
- Retrieval-augmented generation and vector search
