"""Routers call services only: no connector or I/O-client imports beyond DTO types."""

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Routers that predate the rule and still build connectors/clients. Shrink this list; never grow it.
KNOWN_OUTLIERS = {"api/deps.py", "api/markets.py", "api/quotes.py", "api/tickers.py"}


def _connector_imports(path: Path) -> list[str]:
    """Names a module takes from `connectors` that aren't DTO types."""
    bad = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            bad += [alias.name for alias in node.names if alias.name.split(".")[0] == "connectors"]
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "connectors":
            bad += [f"{node.module}.{alias.name}" for alias in node.names if not alias.name.endswith("Dto")]
    return bad


def test_routers_import_only_dto_types_from_connectors():
    violations = {}
    for path in sorted((PROJECT_ROOT / "api").glob("*.py")):
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        if rel in KNOWN_OUTLIERS:
            continue
        bad = _connector_imports(path)
        if bad:
            violations[rel] = bad
    assert violations == {}, "Routers must call services, not connectors/clients: " + repr(violations)


def test_known_outliers_still_exist():
    # Keeps the allowlist honest: drop an entry once its router is fixed.
    for rel in KNOWN_OUTLIERS:
        assert _connector_imports(
            PROJECT_ROOT / rel
        ), f"{rel} no longer imports connectors; remove it from KNOWN_OUTLIERS"
