"""Import-order regression tests.

``src.outputs`` -> ``src.platforms.models`` -> ``src.platforms.base`` ->
``src.distillation.progress`` -> ``src.distillation.engine`` -> ``src.outputs.base``
closed a cycle that raised ``ImportError: cannot import name 'ArtifactKind' from
partially initialized module`` whenever a package was imported first. Previously
this was masked by pytest's alphabetical collection order, which pre-imported
``src.distillation`` before ``tests/outputs`` ran.

Each case runs in a fresh interpreter: modules already imported in the pytest
process would hide the failure.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

IMPORT_CASES = (
    "src.outputs",
    "src.outputs.base",
    "src.platforms",
    "src.platforms.models",
    "src.distillation",
    "src.distillation.progress",
    "src.application",
    "src.orchestration",
    "src.dashboard",
)


@pytest.mark.parametrize("module", IMPORT_CASES)
def test_module_imports_standalone(module: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


LAZY_PACKAGES = ("src.outputs", "src.platforms", "src.distillation")


@pytest.mark.parametrize("module", LAZY_PACKAGES)
def test_lazy_exports_resolve(module: str) -> None:
    """Every public name must still resolve through the lazy ``__getattr__``."""

    program = (
        "import importlib\n"
        f"module = importlib.import_module({module!r})\n"
        "names = module.__all__\n"
        "assert names, 'lazy package must declare __all__'\n"
        "for name in names:\n"
        "    assert getattr(module, name) is not None, name\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
