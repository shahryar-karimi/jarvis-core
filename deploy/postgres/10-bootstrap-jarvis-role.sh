#!/bin/sh
set -e

: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_ADMIN_USER:?POSTGRES_ADMIN_USER is required}"
: "${POSTGRES_ADMIN_PASSWORD:?POSTGRES_ADMIN_PASSWORD is required}"
: "${JARVIS_DB_USER:?JARVIS_DB_USER is required}"
: "${JARVIS_DB_PASSWORD:?JARVIS_DB_PASSWORD is required}"

if [ "${JARVIS_DB_USER}" = "${POSTGRES_ADMIN_USER}" ]; then
    printf '%s\n' \
        'JARVIS_DB_USER must differ from the PostgreSQL bootstrap administrator.' >&2
    exit 64
fi

case "${JARVIS_DB_USER}" in
    postgres|pg_*)
        printf '%s\n' \
            'JARVIS_DB_USER cannot be postgres or use the reserved pg_ prefix.' >&2
        exit 64
        ;;
esac

if [ "${POSTGRES_ADMIN_PASSWORD}" = "${JARVIS_DB_PASSWORD}" ]; then
    printf '%s\n' 'Database administrator and app-role passwords must differ.' >&2
    exit 64
fi

if [ "${#POSTGRES_ADMIN_PASSWORD}" -lt 32 ] \
    || [ "${#JARVIS_DB_PASSWORD}" -lt 32 ]; then
    printf '%s\n' 'Database passwords must each contain at least 32 characters.' >&2
    exit 64
fi

admin_password_lower="$(
    printf '%s' "${POSTGRES_ADMIN_PASSWORD}" | tr '[:upper:]' '[:lower:]'
)"
app_password_lower="$(
    printf '%s' "${JARVIS_DB_PASSWORD}" | tr '[:upper:]' '[:lower:]'
)"

case "${admin_password_lower}" in
    password|postgres|jarvis|changeme|change-me|replace-*|example*)
        printf '%s\n' 'POSTGRES_ADMIN_PASSWORD is still a known placeholder.' >&2
        exit 64
        ;;
esac
case "${app_password_lower}" in
    password|postgres|jarvis|changeme|change-me|replace-*|example*)
        printf '%s\n' 'JARVIS_DB_PASSWORD is still a known placeholder.' >&2
        exit 64
        ;;
esac

if [ "${JARVIS_DB_VALIDATE_ONLY:-0}" = "1" ]; then
    exit 0
fi

if [ -n "${JARVIS_DB_BOOTSTRAP_HOST:-}" ]; then
    export PGHOST="${JARVIS_DB_BOOTSTRAP_HOST}"
fi

PGPASSWORD="${POSTGRES_ADMIN_PASSWORD}" psql \
    --set=ON_ERROR_STOP=1 \
    --username="${POSTGRES_ADMIN_USER}" \
    --dbname="${POSTGRES_DB}" <<'EOSQL'
\getenv database POSTGRES_DB
\getenv admin_user POSTGRES_ADMIN_USER
\getenv app_user JARVIS_DB_USER
\getenv app_password JARVIS_DB_PASSWORD

SELECT format('CREATE ROLE %I LOGIN', :'app_user')
WHERE NOT EXISTS (
    SELECT 1
    FROM pg_catalog.pg_roles
    WHERE rolname = :'app_user'
)
\gexec

ALTER ROLE :"app_user" WITH
    LOGIN
    PASSWORD :'app_password'
    NOSUPERUSER
    NOCREATEDB
    NOCREATEROLE
    NOREPLICATION
    NOBYPASSRLS
    NOINHERIT
    CONNECTION LIMIT 32
    VALID UNTIL 'infinity';

ALTER ROLE :"app_user" RESET ALL;

-- A pre-existing role may have inherited powerful cluster roles, or other
-- identities may have been allowed to assume it. Remove both directions of
-- membership before granting the deliberately narrow database privileges.
SELECT format(
    'REVOKE %I FROM %I',
    granted_role.rolname,
    member_role.rolname
)
FROM pg_catalog.pg_auth_members AS membership
JOIN pg_catalog.pg_roles AS granted_role
    ON granted_role.oid = membership.roleid
JOIN pg_catalog.pg_roles AS member_role
    ON member_role.oid = membership.member
WHERE member_role.rolname = :'app_user'
   OR granted_role.rolname = :'app_user'
\gexec

-- The bootstrap administrator owns the database and public schema. The
-- application role owns only the objects it creates through Alembic.
ALTER DATABASE :"database" OWNER TO :"admin_user";
REVOKE ALL PRIVILEGES ON DATABASE :"database" FROM :"app_user";
REVOKE ALL PRIVILEGES ON SCHEMA public FROM :"app_user";

GRANT CONNECT ON DATABASE :"database" TO :"app_user";
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO :"app_user";
EOSQL

printf 'Ensured restricted JARVIS database role: %s\n' "${JARVIS_DB_USER}"
