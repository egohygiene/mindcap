"""Architecture boundary tests.

These tests verify that the dependency direction between the ``mindcap``
library and the ``mindcap_cli`` CLI adapter is strictly enforced:

* ``mindcap`` must never import ``mindcap_cli`` (except the compat shim)
* Core models must not import Typer
* Application and contracts layers must not have top-level Typer or Rich imports
* Provider plugins must not import CLI command modules or Typer
* Storage must not import CLI presentation

The tests use Python's ``ast`` module so they run without installing all
optional provider dependencies.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent.parent / "src"
MINDCAP_SRC = SRC / "mindcap"
MINDCAP_CLI_SRC = SRC / "mindcap_cli"


def _collect_imports(path: Path) -> list[str]:
    """Return all module names imported from *path* (recursively).

    Walks the full AST to find all imports including those inside functions.
    """
    imports: list[str] = []
    for py_file in path.rglob("*.py") if path.is_dir() else [path]:
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
    return imports


def _collect_toplevel_imports(path: Path) -> list[str]:
    """Return only module-level (top-level) import names from *path*.

    Excludes imports nested inside function or class bodies so that lazy
    imports used during a gradual migration are allowed.
    """
    imports: list[str] = []
    for py_file in path.rglob("*.py") if path.is_dir() else [path]:
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
    return imports


def _imports_any(path: Path, prefixes: tuple[str, ...]) -> list[str]:
    """Return full-tree import strings from *path* that match any prefix."""
    return [
        imp
        for imp in _collect_imports(path)
        if any(imp.startswith(p) for p in prefixes)
    ]


def _toplevel_imports_any(path: Path, prefixes: tuple[str, ...]) -> list[str]:
    """Return top-level import strings from *path* that match any prefix."""
    return [
        imp
        for imp in _collect_toplevel_imports(path)
        if any(imp.startswith(p) for p in prefixes)
    ]


# ---------------------------------------------------------------------------
# mindcap library must not import mindcap_cli
# ---------------------------------------------------------------------------


def test_mindcap_library_never_imports_mindcap_cli() -> None:
    """The reusable library must not depend on the CLI adapter.

    The backward-compatibility shim at ``mindcap/cli.py`` is explicitly
    excluded from this check because it exists solely to re-export the
    Typer application for callers that still use the old import path.
    """
    bad: list[str] = []
    for py_file in MINDCAP_SRC.rglob("*.py"):
        # cli.py and __main__.py are entry-point wiring — they may import mindcap_cli.
        if py_file.name in ("cli.py", "__main__.py") and py_file.parent == MINDCAP_SRC:
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("mindcap_cli"):
                        bad.append(f"{py_file.name}: {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:  # noqa: SIM102
                if node.module.startswith("mindcap_cli"):
                    bad.append(f"{py_file.name}: {node.module}")
    assert not bad, (
        "mindcap library imports mindcap_cli — dependency direction is inverted:\n"
        + "\n".join(bad)
    )


# ---------------------------------------------------------------------------
# Core models must not import Typer
# ---------------------------------------------------------------------------


def test_core_models_do_not_import_typer() -> None:
    core_dir = MINDCAP_SRC / "core"
    if not core_dir.exists():
        pytest.skip("core directory does not exist")
    bad = _imports_any(core_dir, ("typer",))
    assert not bad, f"core/ imports typer: {bad}"


# ---------------------------------------------------------------------------
# Application services must not have top-level Typer or Rich imports
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_path",
    [
        MINDCAP_SRC / "application",
        MINDCAP_SRC / "core",
        MINDCAP_SRC / "contracts",
    ],
)
def test_application_layer_no_toplevel_typer(module_path: Path) -> None:
    if not module_path.exists():
        pytest.skip(f"{module_path} does not exist")
    bad = _toplevel_imports_any(module_path, ("typer",))
    assert not bad, (
        f"{module_path.name} has top-level typer imports — "
        "CLI dependency leaked into library:\n" + "\n".join(bad)
    )


@pytest.mark.parametrize(
    "module_path",
    [
        MINDCAP_SRC / "application",
        MINDCAP_SRC / "contracts",
    ],
)
def test_application_layer_no_toplevel_rich(module_path: Path) -> None:
    if not module_path.exists():
        pytest.skip(f"{module_path} does not exist")
    bad = _toplevel_imports_any(module_path, ("rich",))
    assert not bad, (
        f"{module_path.name} has top-level rich imports — "
        "terminal dependency leaked into library:\n" + "\n".join(bad)
    )


# ---------------------------------------------------------------------------
# Provider plugins must not import CLI modules or Typer
# ---------------------------------------------------------------------------


def test_provider_plugins_do_not_import_mindcap_cli() -> None:
    plugins_dir = MINDCAP_SRC / "plugins"
    if not plugins_dir.exists():
        pytest.skip("plugins directory does not exist")
    bad = _imports_any(plugins_dir, ("mindcap_cli", "mindcap.cli"))
    assert not bad, "Provider plugins import CLI modules:\n" + "\n".join(bad)


def test_provider_plugins_do_not_import_typer() -> None:
    plugins_dir = MINDCAP_SRC / "plugins"
    if not plugins_dir.exists():
        pytest.skip("plugins directory does not exist")
    bad = _imports_any(plugins_dir, ("typer",))
    assert not bad, (
        "Provider plugins import typer — CLI dependency in provider layer:\n"
        + "\n".join(bad)
    )


# ---------------------------------------------------------------------------
# Storage must not import CLI presentation
# ---------------------------------------------------------------------------


def test_storage_does_not_import_mindcap_cli() -> None:
    storage_dir = MINDCAP_SRC / "storage"
    if not storage_dir.exists():
        pytest.skip("storage directory does not exist")
    bad = _imports_any(storage_dir, ("mindcap_cli", "mindcap.cli"))
    assert not bad, "Storage imports CLI modules:\n" + "\n".join(bad)


def test_storage_does_not_import_typer() -> None:
    storage_dir = MINDCAP_SRC / "storage"
    if not storage_dir.exists():
        pytest.skip("storage directory does not exist")
    bad = _imports_any(storage_dir, ("typer",))
    assert not bad, "Storage imports typer:\n" + "\n".join(bad)


# ---------------------------------------------------------------------------
# Registry and sync must not import CLI modules
# ---------------------------------------------------------------------------


def test_registry_does_not_import_mindcap_cli() -> None:
    registry_file = MINDCAP_SRC / "registry.py"
    if not registry_file.exists():
        pytest.skip("registry.py does not exist")
    bad = _imports_any(registry_file, ("mindcap_cli",))
    assert not bad, "registry.py imports mindcap_cli:\n" + "\n".join(bad)


def test_sync_does_not_import_mindcap_cli() -> None:
    sync_dir = MINDCAP_SRC / "sync"
    if not sync_dir.exists():
        pytest.skip("sync directory does not exist")
    bad = _imports_any(sync_dir, ("mindcap_cli",))
    assert not bad, "sync/ imports mindcap_cli:\n" + "\n".join(bad)
