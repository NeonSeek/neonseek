import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "neonseek"
QT_MODULES = ("PySide6", "PyQt5", "PyQt6", "shiboken6")


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_core_does_not_import_qt():
    # gui is a thin layer, everything outside ui/ must stay qt free
    offenders = []
    for path in PACKAGE.rglob("*.py"):
        if "ui" in path.relative_to(PACKAGE).parts:
            continue
        for module in _imported_modules(path):
            if module.split(".")[0] in QT_MODULES:
                offenders.append(f"{path.relative_to(PACKAGE)}: {module}")
    assert offenders == []
