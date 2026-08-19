from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKUP_SCRIPT = PROJECT_ROOT / "scripts" / "backup_postgres.sh"
RESTORE_SCRIPT = PROJECT_ROOT / "scripts" / "restore_postgres.sh"


def script_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_backup_uses_bootstrap_admin_without_preserving_owner_acl() -> None:
    script = script_text(BACKUP_SCRIPT)

    assert 'PGPASSWORD="$POSTGRES_ADMIN_PASSWORD"' in script
    assert '--username="$POSTGRES_ADMIN_USER"' in script
    assert 'PGPASSWORD="$POSTGRES_PASSWORD"' not in script
    assert '--username="$POSTGRES_USER"' not in script
    assert "--format=custom" in script
    assert "--no-owner" in script
    assert "--no-privileges" in script
    assert script.index('mv -- "${partial_checksum}" "${checksum}"') < script.index(
        'mv -- "${partial}" "${target}"'
    )


def test_restore_recreates_objects_for_restricted_application_role() -> None:
    script = script_text(RESTORE_SCRIPT)

    stop_core = script.index('"${compose[@]}" stop core')
    safety_backup = script.index('bash "${script_dir}/backup_postgres.sh"')
    first_bootstrap = script.index('"${compose[@]}" run --rm db-bootstrap')
    drop_database = script.index("    dropdb \\")
    second_bootstrap = script.index(
        '"${compose[@]}" run --rm db-bootstrap',
        first_bootstrap + 1,
    )
    restore_archive = script.index("    exec pg_restore \\")
    migrate = script.index('"${compose[@]}" run --rm migrate')

    assert stop_core < safety_backup < first_bootstrap < drop_database
    assert drop_database < second_bootstrap < restore_archive < migrate
    assert '--username="$POSTGRES_ADMIN_USER"' in script
    assert '--owner="$POSTGRES_ADMIN_USER"' in script
    assert '--owner="$JARVIS_DB_USER"' not in script
    assert '--role="$JARVIS_DB_USER"' in script
    assert "--single-transaction" in script
    assert "--no-owner" in script
    assert "--no-privileges" in script
    assert 'PGPASSWORD="$POSTGRES_PASSWORD"' not in script
    assert '--username="$POSTGRES_USER"' not in script


def test_restore_keeps_destructive_safeguards_before_database_drop() -> None:
    script = script_text(RESTORE_SCRIPT)
    drop_database = script.index("    dropdb \\")

    assert script.index("--confirm-database-replacement") < drop_database
    assert script.index("Backup checksum verification failed") < drop_database
    assert script.index("pg_restore --list") < drop_database
    assert script.index("backup_postgres.sh") < drop_database
    assert script.index("postgres|template0|template1") < drop_database
    assert script.index('"$JARVIS_DB_USER" = "$POSTGRES_ADMIN_USER"') < drop_database
    assert script.index("trap finish EXIT") < drop_database
    assert "JARVIS Core was left stopped for safety" in script


@pytest.mark.skipif(os.name == "nt", reason="Bash syntax check runs in Linux CI")
def test_operational_scripts_have_valid_bash_syntax() -> None:
    subprocess.run(
        ["bash", "-n", str(BACKUP_SCRIPT), str(RESTORE_SCRIPT)],
        check=True,
        cwd=PROJECT_ROOT,
    )
