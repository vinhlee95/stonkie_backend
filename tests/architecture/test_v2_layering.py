import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _imports_for(path: str) -> set[str]:
    tree = ast.parse((PROJECT_ROOT / path).read_text())
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def test_analyze_v2_route_keeps_layer_1_import_boundary():
    imports = _imports_for("api/analyze_v2.py")

    forbidden_prefixes = (
        "connectors.",
        "models.",
        "services.question_analyzer.handlers",
        "services.question_analyzer.handlers_v2",
        "services.question_analyzer.comparison_handler",
        "services.question_analyzer.comparison_handler_v2",
    )

    violations = sorted(
        module
        for module in imports
        if module == "connectors" or module == "models" or module.startswith(forbidden_prefixes)
    )
    assert violations == []


def test_v2_service_modules_do_not_import_fastapi_or_starlette():
    service_paths = [
        "services/analyze_v2_stream.py",
        "services/financial_analyzer_v2.py",
        "services/analyze_retrieval/retrieval.py",
        "services/analyze_retrieval/ranking.py",
        "services/analyze_retrieval/goggle.py",
        "services/analyze_retrieval/market.py",
        "services/analyze_retrieval/publisher.py",
        "services/analyze_retrieval/source_policy.py",
    ]

    violations: list[str] = []
    for path in service_paths:
        for module in _imports_for(path):
            if (
                module == "fastapi"
                or module.startswith("fastapi.")
                or module == "starlette"
                or module.startswith("starlette.")
            ):
                violations.append(f"{path}: {module}")

    assert violations == []


def test_brave_client_has_only_documented_service_schema_import_exception():
    imports = _imports_for("connectors/brave_client.py")

    service_imports = sorted(module for module in imports if module == "services" or module.startswith("services."))
    assert service_imports == ["services.analyze_retrieval.schemas", "services.market_recap.schemas"]
