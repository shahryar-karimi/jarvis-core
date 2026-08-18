#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
project_root="$(cd -- "${script_dir}/.." && pwd -P)"
compose_file="${JARVIS_COMPOSE_FILE:-${project_root}/compose.prod.yml}"
env_file="${JARVIS_ENV_FILE:-${project_root}/.env.production}"
confirmation="--confirm-database-replacement"

usage() {
    printf 'Usage: %s BACKUP.dump %s\n' "${0##*/}" "${confirmation}" >&2
}

if (( $# != 2 )) || [[ "${2}" != "${confirmation}" ]]; then
    usage
    printf 'Restore replaces the production database and requires explicit confirmation.\n' >&2
    exit 64
fi

archive="${1}"
checksum="${archive}.sha256"

if [[ ! -f "${archive}" || ! -r "${archive}" ]]; then
    printf 'Backup archive is not a readable file: %s\n' "${archive}" >&2
    exit 66
fi
if [[ ! -f "${checksum}" || ! -r "${checksum}" ]]; then
    printf 'Checksum file is required: %s\n' "${checksum}" >&2
    exit 66
fi
if [[ ! -f "${compose_file}" || ! -f "${env_file}" ]]; then
    printf 'compose.prod.yml and .env.production are required.\n' >&2
    exit 66
fi
if ! command -v docker >/dev/null 2>&1; then
    printf 'docker is required.\n' >&2
    exit 69
fi
if ! command -v sha256sum >/dev/null 2>&1; then
    printf 'sha256sum is required.\n' >&2
    exit 69
fi

archive_dir="$(cd -- "$(dirname -- "${archive}")" && pwd -P)"
archive_name="$(basename -- "${archive}")"
archive="${archive_dir}/${archive_name}"
checksum="${archive}.sha256"

expected_digest="$(cut -d ' ' -f 1 < "${checksum}")"
actual_digest="$(sha256sum -- "${archive}" | cut -d ' ' -f 1)"
if [[ ! "${expected_digest}" =~ ^[[:xdigit:]]{64}$ ]] \
    || [[ "${actual_digest}" != "${expected_digest}" ]]; then
    printf 'Backup checksum verification failed; the database was not changed.\n' >&2
    exit 65
fi

compose=(
    docker compose
    --project-directory "${project_root}"
    --env-file "${env_file}"
    -f "${compose_file}"
)

"${compose[@]}" config --quiet
"${compose[@]}" up -d --wait postgres
"${compose[@]}" exec -T postgres pg_restore --list \
    < "${archive}" >/dev/null

"${compose[@]}" exec -T postgres sh -eu -c '
    : "${POSTGRES_DB:?POSTGRES_DB is required}"
    : "${POSTGRES_ADMIN_USER:?POSTGRES_ADMIN_USER is required}"
    : "${POSTGRES_ADMIN_PASSWORD:?POSTGRES_ADMIN_PASSWORD is required}"
    : "${JARVIS_DB_USER:?JARVIS_DB_USER is required}"
    : "${JARVIS_DB_PASSWORD:?JARVIS_DB_PASSWORD is required}"
    if [ "$JARVIS_DB_USER" = "$POSTGRES_ADMIN_USER" ]; then
        printf "Refusing to restore with the administrator as the application role.\n" >&2
        exit 64
    fi
    case "$POSTGRES_DB" in
        postgres|template0|template1)
            printf "Refusing to replace a PostgreSQL system database.\n" >&2
            exit 64
            ;;
    esac
'

core_was_running=false
if [[ -n "$("${compose[@]}" ps --status running -q core)" ]]; then
    core_was_running=true
fi

"${compose[@]}" stop core

restore_succeeded=false
finish() {
    if [[ "${restore_succeeded}" != true ]]; then
        printf '%s\n' \
            'Restore did not complete. JARVIS Core was left stopped for safety.' >&2
    fi
}
trap finish EXIT

printf 'Creating a safety backup with JARVIS Core stopped...\n'
bash "${script_dir}/backup_postgres.sh" "${project_root}/backups/pre-restore"

# Ensure the restricted role exists before replacing the database. The
# bootstrap is intentionally explicit because existing volumes do not rerun
# docker-entrypoint-initdb.d scripts.
"${compose[@]}" run --rm db-bootstrap

"${compose[@]}" exec -T postgres sh -eu -c '
    export PGPASSWORD="$POSTGRES_ADMIN_PASSWORD"
    dropdb \
        --host=127.0.0.1 \
        --username="$POSTGRES_ADMIN_USER" \
        --maintenance-db=postgres \
        --force \
        --if-exists \
        -- "$POSTGRES_DB"
    createdb \
        --host=127.0.0.1 \
        --username="$POSTGRES_ADMIN_USER" \
        --maintenance-db=postgres \
        --owner="$POSTGRES_ADMIN_USER" \
        -- "$POSTGRES_DB"
'

# Reapply database- and schema-level grants to the newly created database.
"${compose[@]}" run --rm db-bootstrap

"${compose[@]}" exec -T postgres sh -eu -c '
    export PGPASSWORD="$POSTGRES_ADMIN_PASSWORD"
    exec pg_restore \
        --host=127.0.0.1 \
        --username="$POSTGRES_ADMIN_USER" \
        --dbname="$POSTGRES_DB" \
        --role="$JARVIS_DB_USER" \
        --exit-on-error \
        --single-transaction \
        --no-owner \
        --no-privileges
' < "${archive}"

"${compose[@]}" run --rm migrate

if [[ "${core_was_running}" == true ]]; then
    "${compose[@]}" up -d --wait core
fi

restore_succeeded=true
trap - EXIT
printf 'Restore completed from: %s\n' "${archive}"
