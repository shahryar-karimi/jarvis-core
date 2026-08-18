#!/bin/sh
set -eu

# Validate both database identities before initdb can persist an unsafe
# bootstrap account in a new volume. The same validation is repeated by the
# idempotent role bootstrap used for fresh and existing databases.
JARVIS_DB_VALIDATE_ONLY=1 \
    /bin/sh /docker-entrypoint-initdb.d/10-bootstrap-jarvis-role.sh

exec /usr/local/bin/docker-entrypoint.sh "$@"
