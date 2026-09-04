"""Test configuration.

The tests in ``tests/`` are pure-logic tests: they need nothing but the
standard library, so they run wherever ``pytest`` (or ``unittest``) does. The
Home Assistant integration tests live in ``tests/ha/`` and are skipped
automatically when ``pytest-homeassistant-custom-component`` is not installed,
so a bare environment still gets a clean run of the pure tests.
"""

from __future__ import annotations

import importlib.util

collect_ignore: list[str] = []
collect_ignore_glob: list[str] = []

if importlib.util.find_spec("pytest_homeassistant_custom_component") is None:
    # Both: the directory itself (whose conftest.py would import the plugin)
    # and everything under it.
    collect_ignore.append("ha")
    collect_ignore_glob.append("ha/*")
