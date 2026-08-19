import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    return modules


def test_memory_routes_do_not_import_infrastructure() -> None:
    route_path = PROJECT_ROOT / "app" / "api" / "routes" / "memories.py"
    modules = imported_modules(route_path)

    assert not {
        module
        for module in modules
        if module == "app.infrastructure" or module.startswith("app.infrastructure.")
    }


def test_domain_and_application_do_not_import_outer_layers() -> None:
    forbidden_prefixes = (
        "app.api",
        "app.infrastructure",
        "fastapi",
        "google",
        "sqlalchemy",
    )
    violations: list[str] = []

    for layer in ("domain", "application"):
        for path in (PROJECT_ROOT / "app" / layer).rglob("*.py"):
            for module in imported_modules(path):
                if module.startswith(forbidden_prefixes):
                    violations.append(f"{path.relative_to(PROJECT_ROOT)} -> {module}")

    assert violations == []
