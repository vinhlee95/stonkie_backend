"""Router → service → helpers layering.

- A router (`api/*.py`) imports exactly one service entry, `services.<feature>`, and only `*Dto` types
  from `connectors`. It never builds connectors/clients.
- A feature package is any `services/<feature>/` with a `service.py`. Its `<Feature>Service` is the only
  entry point; every other module in it is a private helper:
    * nothing outside the package imports its submodules (only the package root);
    * its modules import other services only from `services.shared` or the shared libraries below,
      never another feature's service or helpers (no service-to-service calls);
    * only `service.py` constructs connectors/clients; helpers receive them as arguments.
"""

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Shared libraries feature packages may use besides services/shared/.
SHARED_LIBRARIES = ("services.analyze_retrieval", "services.analysis_progress", "services.shared")

# Routers that predate the rule. Shrink this list; never grow it.
KNOWN_ROUTER_OUTLIERS = {
    "api/analyze_v2.py",
    "api/companies.py",
    "api/deps.py",
    "api/markets.py",
    "api/quotes.py",
    "api/recap_analyze.py",
    "api/tickers.py",
}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text())


def _imported_modules(tree: ast.Module) -> list[str]:
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
            # `from services.portfolio import chat` imports the submodule services.portfolio.chat.
            modules += [
                f"{node.module}.{alias.name}"
                for alias in node.names
                if (PROJECT_ROOT / node.module.replace(".", "/") / f"{alias.name}.py").exists()
            ]
    return modules


def _router_violations(path: Path) -> list[str]:
    tree = _tree(path)
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "connectors":
            problems += [f"imports {node.module}.{a.name}" for a in node.names if not a.name.endswith("Dto")]
        elif isinstance(node, ast.Import):
            problems += [f"imports {a.name}" for a in node.names if a.name.split(".")[0] == "connectors"]
    services = {m for m in _imported_modules(tree) if m.split(".")[0] == "services"}
    if len(services) > 1:
        problems.append(f"imports several services: {sorted(services)}")
    problems += [f"imports a service submodule: {m}" for m in services if m.count(".") > 1]
    return problems


def _routers() -> list[Path]:
    return sorted((PROJECT_ROOT / "api").glob("*.py"))


def _feature_packages() -> list[Path]:
    return sorted(p.parent for p in (PROJECT_ROOT / "services").glob("*/service.py"))


def _rel(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def test_routers_import_one_service_and_no_connectors():
    violations = {
        _rel(p): problems
        for p in _routers()
        if _rel(p) not in KNOWN_ROUTER_OUTLIERS and (problems := _router_violations(p))
    }
    assert violations == {}, violations


def test_known_router_outliers_still_need_the_exemption():
    # Drop an entry once its router follows the rule.
    for rel in KNOWN_ROUTER_OUTLIERS:
        assert _router_violations(
            PROJECT_ROOT / rel
        ), f"{rel} now follows the rule; remove it from KNOWN_ROUTER_OUTLIERS"


def test_feature_package_helpers_are_private():
    for package in _feature_packages():
        dotted = _rel(package).replace("/", ".")
        offenders = []
        for path in PROJECT_ROOT.glob("**/*.py"):
            rel = _rel(path)
            if rel.startswith((_rel(package) + "/", "tests/", "venv/", ".")) or "/." in rel:
                continue
            offenders += [f"{rel} imports {m}" for m in _imported_modules(_tree(path)) if m.startswith(dotted + ".")]
        assert offenders == [], offenders


def test_feature_packages_do_not_call_other_services():
    for package in _feature_packages():
        dotted = _rel(package).replace("/", ".")
        offenders = [
            f"{_rel(path)} imports {m}"
            for path in package.glob("*.py")
            for m in _imported_modules(_tree(path))
            if m.split(".")[0] == "services" and not m.startswith((dotted, *SHARED_LIBRARIES)) and m != "services"
        ]
        assert offenders == [], offenders


def test_only_service_py_constructs_connectors():
    for package in _feature_packages():
        offenders = []
        for path in package.glob("*.py"):
            if path.name == "service.py":
                continue
            for node in ast.walk(_tree(path)):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    if node.func.id.endswith(("Connector", "Client")):
                        offenders.append(f"{_rel(path)}:{node.lineno} constructs {node.func.id}")
        assert offenders == [], offenders


def test_feature_package_has_one_service_class():
    for package in _feature_packages():
        services = [
            node.name
            for node in _tree(package / "service.py").body
            if isinstance(node, ast.ClassDef) and node.name.endswith("Service")
        ]
        assert len(services) == 1, f"{_rel(package)}/service.py defines {services}; expected exactly one *Service"
