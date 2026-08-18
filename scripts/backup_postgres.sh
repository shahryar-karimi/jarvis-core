#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
project_root="$(cd -- "${script_dir}/.." && pwd -P)"
compose_file="${JARVIS_COMPOSE_FILE:-${project_root}/compose.prod.yml}"
env_file="${JARVIS_ENV_FILE:-${project_root}/.env.production}"

usage() {
    printf 'Usage: %s [BACKUP_DIRECTORY]\n' "${0##*/}" >&2
}

if (( $# > 1 )); then
    usage
    exit 64
fi

backup_dir="${1:-${project_root}/backups}"

if [[ ! -f "${compose_file}" ]]; then
    printf 'Production Compose file not found: %s\n' "${compose_file}" >&2
    exit 66
fi
if [[ ! -f "${env_file}" ]]; then
    printf 'Production environment file not found: %s\n' "${env_file}" >&2
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

mkdir -p -- "${backup_dir}"
backup_dir="$(cd -- "${backup_dir}" && pwd -P)"

compose=(
    docker compose
    --project-directory "${project_root}"
    --env-file "${env_file}"
    -f "${compose_file}"
)

"${compose[@]}" config --quiet
"${compose[@]}" up -d --wait postgres

timestamp="$(date -u +'%Y%m%dT%H%M%SZ')"
filename="jarvis-postgres-${timestamp}.dump"
target="${backup_dir}/${filename}"
checksum="${target}.sha256"

if [[ -e "${target}" || -e "${checksum}" ]]; then
    printf 'Refusing to overwrite an existing backup for timestamp %s.\n' \
        "${timestamp}" >&2
    exit 73
fi

partial="$(mktemp "${backup_dir}/.jarvis-postgres-${timestamp}.XXXXXX.dump")"
partial_checksum="$(
    mktemp "${backup_dir}/.jarvis-postgres-${timestamp}.XXXXXX.sha256"
)"
cleanup() {
    rm -f -- "${partial}"
    rm -f -- "${partial_checksum}"
}
trap cleanup EXIT

"${compose[@]}" exec -T postgres sh -eu -c '
    export PGPASSWORD="$POSTGRES_ADMIN_PASSWORD"
    exec pg_dump \
        --host=127.0.0.1 \
        --username="$POSTGRES_ADMIN_USER" \
        --dbname="$POSTGRES_DB" \
        --format=custom \
        --no-owner \
        --no-privileges
' > "${partial}"

if [[ ! -s "${partial}" ]]; then
    printf 'pg_dump produced an empty archive; no backup was published.\n' >&2
    exit 74
fi

"${compose[@]}" exec -T postgres pg_restore --list \
    < "${partial}" >/dev/null

digest="$(sha256sum -- "${partial}" | cut -d ' ' -f 1)"
printf '%s  %s\n' "${digest}" "${filename}" > "${partial_checksum}"

# Publish the archive last. A visible .dump therefore always has its matching
# checksum, while an interrupted publication can leave only a harmless orphan
# checksum without an archive to restore.
mv -- "${partial_checksum}" "${checksum}"
mv -- "${partial}" "${target}"
trap - EXIT

printf 'Backup created: %s\n' "${target}"
printf 'Checksum created: %s\n' "${checksum}"
