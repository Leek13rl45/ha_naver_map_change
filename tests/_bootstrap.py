"""Import the pure-logic modules without executing the package __init__.

``custom_components/naver_map_change/__init__.py`` imports Home Assistant, which
is not needed - and may not be installed - for the pure-logic tests. A synthetic
package named ``nmc`` is registered with its ``__path__`` pointing at the
integration folder, so ``from .const import ...`` inside those modules resolves
to ``nmc.const`` without the real package __init__ ever running.
"""

# ruff: noqa: I001 - sys.path has to be set up before these imports.

from __future__ import annotations

import pathlib
import sys
import types

INTEGRATION_DIR = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "naver_map_change"
)

if "nmc" not in sys.modules:
    _package = types.ModuleType("nmc")
    _package.__path__ = [str(INTEGRATION_DIR)]  # type: ignore[attr-defined]
    _package.__package__ = "nmc"
    sys.modules["nmc"] = _package

from nmc import cache as cache_mod  # noqa: E402
from nmc import const  # noqa: E402
from nmc import providers  # noqa: E402

__all__ = ["INTEGRATION_DIR", "cache_mod", "const", "providers"]
