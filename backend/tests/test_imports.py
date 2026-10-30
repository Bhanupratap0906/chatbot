"""Every module under ``backend/modules`` (plus ``main``) must import.

The app imports most workflow modules lazily at runtime, so a dangling import
inside a package ``__init__`` can go unnoticed by ``import main`` until a call
crashes. Walking every module at test time keeps that class of bug visible.
"""

import importlib
import pkgutil
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
MODULES_DIR = BACKEND_DIR / "modules"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _module_names() -> list[str]:
    # ``modules`` is a namespace package (no __init__.py), so walk the
    # directory itself; walking its parent would skip it entirely.
    names = {
        info.name
        for info in pkgutil.walk_packages([str(MODULES_DIR)], prefix="modules.")
    }
    names.add("main")
    return sorted(names)


@pytest.mark.parametrize("module_name", _module_names())
def test_module_imports(module_name: str) -> None:
    importlib.import_module(module_name)
