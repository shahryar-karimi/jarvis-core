from __future__ import annotations

from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_production_compose_exposes_only_the_tls_proxy() -> None:
    compose = yaml.safe_load(
        (PROJECT_ROOT / "compose.prod.yml").read_text(encoding="utf-8"),
    )
    services = compose["services"]

    assert set(services) == {
        "postgres",
        "db-bootstrap",
        "migrate",
        "core",
        "caddy",
    }
    for private_service in ("postgres", "db-bootstrap", "migrate", "core"):
        assert "ports" not in services[private_service]

    assert services["caddy"]["ports"] == [
        "80:80",
        "443:443",
        "443:443/udp",
    ]
    assert compose["networks"]["database"]["internal"] is True
    assert services["postgres"]["networks"] == ["database"]
    assert services["core"]["networks"] == ["edge", "database"]


def test_production_core_is_single_process_and_hardened() -> None:
    compose = yaml.safe_load(
        (PROJECT_ROOT / "compose.prod.yml").read_text(encoding="utf-8"),
    )
    core = compose["services"]["core"]
    command = core["command"]

    worker_option = command.index("--workers")
    assert command[worker_option + 1] == "1"
    assert core["deploy"]["replicas"] == 1
    assert core["read_only"] is True
    assert core["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in core["security_opt"]
    assert "/api/v1/health/ready" in " ".join(core["healthcheck"]["test"])

    raw_database_values = core["environment"]
    assert all(value == "" for value in raw_database_values.values())
    assert "JARVIS_DATABASE_URL" not in raw_database_values


def test_database_roles_and_release_images_are_explicit() -> None:
    compose_text = (PROJECT_ROOT / "compose.prod.yml").read_text(
        encoding="utf-8",
    )
    compose = yaml.safe_load(compose_text)
    services = compose["services"]

    postgres_image = (
        "postgres:17.10-alpine3.23@sha256:"
        "8189a1f6e40904781fc9e2612687877791d21679866db58b1de996b31fc312e4"
    )
    assert services["postgres"]["image"] == postgres_image
    assert services["db-bootstrap"]["image"] == postgres_image
    assert services["caddy"]["image"] == (
        "caddy:2.11.4-alpine@sha256:"
        "5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648"
    )
    assert "${JARVIS_IMAGE_TAG:?JARVIS_IMAGE_TAG is required}" in compose_text
    assert "${JARVIS_VCS_REF:?JARVIS_VCS_REF is required}" in compose_text
    assert "postgres-entrypoint-wrapper.sh" in compose_text
    assert "10-bootstrap-jarvis-role.sh" in compose_text

    bootstrap = (PROJECT_ROOT / "deploy/postgres/10-bootstrap-jarvis-role.sh")
    bootstrap_text = bootstrap.read_text(encoding="utf-8")
    assert "NOSUPERUSER" in bootstrap_text
    assert "NOCREATEDB" in bootstrap_text
    assert "NOCREATEROLE" in bootstrap_text
    assert "NOREPLICATION" in bootstrap_text
    assert "NOBYPASSRLS" in bootstrap_text
    assert "NOINHERIT" in bootstrap_text
    assert "CONNECTION LIMIT 32" in bootstrap_text
    assert "VALID UNTIL 'infinity'" in bootstrap_text
    assert 'ALTER ROLE :"app_user" RESET ALL' in bootstrap_text
    assert "pg_catalog.pg_auth_members" in bootstrap_text
    assert 'ALTER DATABASE :"database" OWNER TO :"admin_user"' in bootstrap_text
    assert 'REVOKE ALL PRIVILEGES ON DATABASE :"database"' in bootstrap_text
    assert "REVOKE ALL PRIVILEGES ON SCHEMA public" in bootstrap_text


def test_container_and_proxy_freeze_production_safety_contracts() -> None:
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    caddyfile = (PROJECT_ROOT / "Caddyfile").read_text(encoding="utf-8")
    dockerignore = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert (
        "python:3.12.13-slim-bookworm@sha256:"
        "4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2"
        in dockerfile
    )
    assert dockerfile.startswith(
        "# syntax=docker/dockerfile:1@sha256:"
        "ecfaec9ed6d810b56388c508f4121597bfbba70d41a6dfeee4d8cad5f295fc32"
    )
    assert "--constraint constraints-production.txt" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "/api/v1/health/ready" in dockerfile
    assert '"--workers", "1"' in dockerfile
    assert '"--ws-max-size", "65536"' in dockerfile

    assert "{$JARVIS_DOMAIN}" in caddyfile
    assert "max_size 1MB" in caddyfile
    assert "reverse_proxy core:8000" in caddyfile
    assert "health_uri /api/v1/health/ready" in caddyfile
    assert "log_credentials" not in caddyfile

    assert ".env.*" in dockerignore
    assert "backups" in dockerignore
    assert "*.dump" in dockerignore
