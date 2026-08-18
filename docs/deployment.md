# Production deployment

This runbook deploys one JARVIS Core instance, PostgreSQL, and Caddy with
Docker Compose on an Ubuntu VPS. Caddy terminates HTTPS/WSS; Core and
PostgreSQL stay on a private Docker network.

Run exactly one Core worker and one Core replica. Active WebSockets, heartbeat
state, sessions, pairing challenges, and pending command futures are currently
process-local. Horizontal scaling requires a shared presence and command
broker first.

## 1. Prepare the server and DNS

Provision a maintained Ubuntu LTS VPS with a static address. Point an `A`
record such as `core.example.com` to it; add `AAAA` only when IPv6 works end to
end. In the provider firewall allow SSH from trusted addresses and public TCP
80/443 (plus UDP 443 if desired). Do not open 5432, 5433, or 8000.

Install Docker Engine and the Compose plugin from Docker's official Ubuntu
repository. Verify both before continuing:

```bash
sudo docker version
sudo docker compose version
```

Using the `docker` Unix group is equivalent to granting root-level access. Use
`sudo docker` unless that tradeoff is intentional.

## 2. Deploy a fixed revision

Clone the repository into a dedicated directory and check out a reviewed tag
or exact commit rather than deploying a moving branch:

```bash
sudo install -d -o "$USER" -g "$USER" /opt/jarvis-core
git clone <repository-url> /opt/jarvis-core
cd /opt/jarvis-core
git checkout <release-tag-or-commit>
```

For a private repository, use a read-only deploy key.

## 3. Configure secrets

Create the ignored production environment file and restrict its permissions:

```bash
cp .env.production.example .env.production
chmod 600 .env.production
```

Generate independent values for every secret: the PostgreSQL bootstrap-admin
password, application-role password, owner token, device-admin token, and
device credential digest key. Hex output is URL-safe and long enough for the
production checks, which avoids escaping problems in `JARVIS_DATABASE_URL`:

```bash
openssl rand -hex 32
```

Edit `.env.production` and replace every placeholder. The application password
in `JARVIS_DB_PASSWORD` must exactly match the password embedded in
`JARVIS_DATABASE_URL`; it must differ from `POSTGRES_ADMIN_PASSWORD`. Core uses
only the non-superuser `JARVIS_DB_USER` role. The bootstrap administrator owns
the database and schema; the application role owns only the tables and other
objects created by Alembic. Both database passwords and all three JARVIS
authentication/digest secrets must be mutually distinct and must not reuse the
Gemini API key. Bearer tokens are ASCII and at least 32 characters. Also:

- Set `JARVIS_IMAGE_TAG` to the reviewed release tag or short commit in a
  Docker-tag-safe form, and `JARVIS_VCS_REF` to the exact commit SHA returned by
  `git rev-parse HEAD`.
- Set `JARVIS_DOMAIN` to the DNS hostname only, without `https://` or a path.
- Replace `core.example.com` in `JARVIS_TRUSTED_HOSTS` with that public
  hostname, while retaining `localhost`, `127.0.0.1`, and `core` for internal
  container health probes.
- Keep `JARVIS_CORS_ORIGINS=[]` when no browser frontend calls Core. Otherwise,
  list only exact HTTPS origins, for example `["https://app.example.com"]`.
- Keep production API documentation disabled unless it is deliberately needed.
- Keep `JARVIS_DEVICE_CREDENTIAL_DIGEST_KEY` stable. Losing or changing it
  invalidates all issued device credentials and forces every Agent to re-pair.

Leave `JARVIS_ENV_FILE=.env.production` for the standard layout. To store
secrets elsewhere, export `JARVIS_ENV_FILE` as that absolute path before every
Compose, backup, or restore command; all deployment helpers honor the override.
When invoking a helper through `sudo`, pass the variable explicitly with
`sudo env JARVIS_ENV_FILE="$JARVIS_ENV_FILE" bash scripts/<helper>.sh ...`.

Store an encrypted copy of `.env.production` separately from the VPS and its
database backups. Never commit it, paste it into logs, or pass bearer tokens in
URLs.

The owner token protects chat, memory, and future task APIs. The separate
device-admin token protects capability listing, pairing creation, device
listing/revocation, and command dispatch. Pair claims use one-time pairing
secrets; Agent WebSockets use individual device credentials.

## 4. Validate, build, migrate, and start

Define this helper for the current shell. It also supports an externally stored
environment file:

```bash
jarvis_env_file="${JARVIS_ENV_FILE:-.env.production}"
dc() {
  sudo docker compose --env-file "$jarvis_env_file" -f compose.prod.yml "$@"
}
```

Validate the interpolated configuration without printing secrets, build the
images, and start PostgreSQL:

```bash
dc config --quiet
dc build --pull
dc up -d --wait postgres
```

For an existing installation, create and verify a backup before migrating.
Bootstrap or reconcile the least-privilege application role, run the one-off
migration service as that role, and start the stack:

```bash
dc run --rm db-bootstrap
dc run --rm migrate
dc up -d --wait
dc ps
dc logs --tail=100 core postgres caddy
```

The role bootstrap is idempotent, removes unexpected role memberships and
role-level settings, and is required explicitly for an existing PostgreSQL
volume; a fresh volume also runs it during initialization.
Application startup never creates or changes tables. A release is not ready
until `alembic upgrade head` has succeeded through the `migrate` service.

## 5. Verify the release

Check liveness and database readiness through Caddy:

```bash
curl --fail --silent --show-error https://core.example.com/api/v1/health
curl --fail --silent --show-error https://core.example.com/api/v1/health/ready
```

Replace the example hostname. Then verify:

- A request to `/api/v1/memories` without a bearer token returns `401`.
- The same request with the owner token succeeds.
- Device-administration routes reject the owner token and accept only the
  device-admin token.
- An authenticated chat request reaches the configured AI provider.
- An Agent connects with WSS at `/api/v1/devices/ws` using the exact
  `jarvis-device.v1` subprotocol and reconnects after `dc restart core`.

Caddy rejects HTTP request bodies larger than 1 MB. WebSocket messages remain
subject to `JARVIS_DEVICE_MAX_MESSAGE_BYTES`, which is 64 KiB in the template.

The Gemini live check can fail when Gemini API use or the selected model is not
supported from the VPS region. Confirm provider availability and policy before
deploying; do not treat moving traffic as a way to bypass provider eligibility.

## 6. Back up and restore

Back up PostgreSQL before every migration and on a daily schedule. Keep
encrypted copies off the VPS with retention appropriate to the data. The
backup helper creates a custom-format dump, validates its archive structure,
and atomically publishes it with a SHA-256 checksum:

```bash
sudo bash scripts/backup_postgres.sh
# Or select a dedicated destination:
sudo bash scripts/backup_postgres.sh /var/backups/jarvis-core
```

Copy both the `.dump` and sibling `.dump.sha256` off the server. Also back up
the deployed revision identifier and encrypted production environment. A
Docker volume by itself is not a complete backup strategy.

The restore helper deliberately replaces the configured production database.
It verifies the checksum and archive, creates a separate safety backup, stops
Core, replaces the database, applies current migrations, and restarts Core
only when it was previously running:

```bash
sudo bash scripts/restore_postgres.sh \
  backups/<verified-backup>.dump \
  --confirm-database-replacement
```

If restoration fails, Core remains stopped so it cannot use a partial database;
inspect the error before taking further action. Rehearse restore using a copy
of the production environment and database, not the live service, before
relying on it for disaster recovery.

## 7. Update and roll back

For each release: fetch and check out the reviewed tag, record its exact SHA,
take a backup, build, run `db-bootstrap` and `migrate`, start the stack, and
repeat all verification checks.

The production image installs the direct runtime versions recorded in
`constraints-production.txt`, and the Python, PostgreSQL, and Caddy images are
pinned by immutable digest. When application dependencies or base images
change, review and refresh those pins, rebuild the image, and run the full test
suite before publishing the release.

If application verification fails, inspect `dc logs`. A code rollback means
checking out the previously recorded revision, rebuilding, and restarting Core.
Only do this when the previous code is compatible with the migrated schema.

Do not blindly run `alembic downgrade` in production. In particular,
downgrading revision `0002_persist_devices` removes persisted device and
credential data. When a schema rollback is unavoidable, restore the verified
pre-deployment database backup through the rehearsed recovery procedure.

## 8. Ongoing operations

- Monitor HTTPS certificate renewal, disk use, container restarts, readiness,
  PostgreSQL backups, and restore drills.
- Review `docker compose ... logs` without exposing environment values or
  authorization headers.
- Apply host and container security updates through reviewed releases.
- Preserve Caddy's data volume so certificate state survives restarts.
- Pair only trusted Agents and grant the minimum capabilities required.
- Keep Agent-side application, path, and action allowlists in force; Core
  capability grants are not a substitute for local enforcement.
