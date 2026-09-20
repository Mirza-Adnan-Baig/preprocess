import ast
import pathlib

from tools.build_bundle import ALLOWED_IMPORTS, BUNDLE_PATH, build


def test_bundle_is_valid_python():
    compile(build(), "bundle", "exec")


def test_bundle_has_exactly_one_pipe_class():
    tree = ast.parse(build())
    pipes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Pipe"]
    assert len(pipes) == 1


def test_bundle_has_no_internal_imports_left():
    assert "from src.faro_docs" not in build()
    assert "from adapters" not in build()


def test_bundle_only_imports_allowed_packages():
    tree = ast.parse(build())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    assert imported <= ALLOWED_IMPORTS, f"nicht erlaubt: {imported - ALLOWED_IMPORTS}"


def test_bundle_declares_no_pip_requirements():
    header = build()[:2000]
    assert "requirements:" not in header


def test_bundle_is_deterministic():
    assert build() == build()


def test_committed_bundle_is_up_to_date():
    """Catches hand-edits to the generated file, the drift this design removes."""
    on_disk = pathlib.Path(BUNDLE_PATH).read_text(encoding="utf-8")
    assert on_disk == build(), "Bundle veraltet — `python -m tools.build_bundle` ausführen"
