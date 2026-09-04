"""Source-level guards for the hard constraints.

These assert on the integration's own source text, so they need neither Home
Assistant nor pytest fixtures. They exist because the constraints they check are
the reason for this rewrite: the previous implementation patched files under
``site-packages`` and recompressed frontend bundles
(docs/03-REDESIGN-SPEC.md sections 0 and 7).
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _bootstrap import INTEGRATION_DIR  # noqa: E402

PYTHON_FILES = sorted(INTEGRATION_DIR.glob("*.py"))

# docs/03-REDESIGN-SPEC.md section 7, plus the modules that made the old
# approach possible.
FORBIDDEN_SUBSTRINGS = (
    "site-packages",
    "hass_frontend",
    "brotli",
    ".js.bak",
    "urllib",
    "RETINA",
    "cartocdn",
    "find_hass_frontend_dirs",
    "find_map_js_file",
    "patch_js_file",
    "restore_js_file",
    "recompress_js",
    "CARTO_TILE_PATTERN",
    "register_static_path(",
)

FORBIDDEN_IMPORTS = {
    "brotli",
    "gzip",
    "shutil",
    "urllib",
    "urllib.request",
    "sys",
}

# Blocking calls the event loop detector watches for (docs/02 section 4.7).
BLOCKING_CALLS = (
    "open(",
    "os.listdir",
    "os.walk",
    "os.scandir",
    "os.stat",
    "glob.glob",
    "glob.iglob",
    "time.sleep",
    "read_text(",
    "write_text(",
    "read_bytes(",
    "write_bytes(",
    "import_module",
)


class TestForbiddenCode(unittest.TestCase):
    """Nothing from the old file-patching implementation came along."""

    def test_no_forbidden_substrings(self) -> None:
        for path in PYTHON_FILES:
            source = path.read_text(encoding="utf-8")
            for forbidden in FORBIDDEN_SUBSTRINGS:
                self.assertNotIn(
                    forbidden, source, f"{path.name} contains {forbidden!r}"
                )

    def test_no_forbidden_imports(self) -> None:
        for path in PYTHON_FILES:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = {alias.name.split(".")[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom):
                    names = {(node.module or "").split(".")[0]}
                else:
                    continue
                overlap = names & FORBIDDEN_IMPORTS
                self.assertFalse(
                    overlap, f"{path.name} imports {sorted(overlap)}"
                )

    def test_no_blocking_calls_in_module_source(self) -> None:
        """The only file access is a Path join, which does no I/O."""
        for path in PYTHON_FILES:
            source = path.read_text(encoding="utf-8")
            for call in BLOCKING_CALLS:
                self.assertNotIn(call, source, f"{path.name} calls {call}")

    def test_no_apply_or_restore_service(self) -> None:
        services = (INTEGRATION_DIR / "services.yaml").read_text(encoding="utf-8")
        self.assertNotIn("apply:", services)
        self.assertNotIn("restore:", services)
        self.assertIn("refresh_version:", services)
        self.assertIn("clear_cache:", services)

    def test_no_strings_json(self) -> None:
        """strings.json is core-only (docs/02 section 4.5)."""
        self.assertFalse((INTEGRATION_DIR / "strings.json").exists())
        self.assertTrue((INTEGRATION_DIR / "translations" / "en.json").exists())
        self.assertTrue((INTEGRATION_DIR / "translations" / "ko.json").exists())


class TestManifest(unittest.TestCase):
    """The manifest matches docs/03 section 3.8."""

    def setUp(self) -> None:
        self.manifest = json.loads(
            (INTEGRATION_DIR / "manifest.json").read_text(encoding="utf-8")
        )

    def test_no_requirements(self) -> None:
        """No third-party package: aiohttp comes from Home Assistant."""
        self.assertEqual(self.manifest["requirements"], [])

    def test_fields(self) -> None:
        self.assertEqual(self.manifest["domain"], "naver_map_change")
        self.assertEqual(self.manifest["integration_type"], "service")
        self.assertIs(self.manifest["config_flow"], True)
        self.assertIs(self.manifest["single_config_entry"], True)
        self.assertEqual(self.manifest["iot_class"], "cloud_polling")
        self.assertEqual(
            self.manifest["dependencies"], ["http", "frontend", "map_tiles"]
        )
        self.assertIn("issue_tracker", self.manifest)
        self.assertIn("codeowners", self.manifest)

    def test_version_matches_the_constant(self) -> None:
        """design decision D6: the constant is not read from this file."""
        from _bootstrap import const

        self.assertEqual(self.manifest["version"], const.INTEGRATION_VERSION)

    def test_keys_are_sorted_for_hassfest(self) -> None:
        """hassfest requires domain, name, then alphabetical order.

        The example in docs/03 section 3.8 is not in that order, and hassfest
        fails the build over it ("Manifest keys are not sorted correctly"), so
        this is asserted rather than left to a CI round trip.
        """
        keys = list(self.manifest)
        self.assertEqual(keys[:2], ["domain", "name"])
        self.assertEqual(keys[2:], sorted(keys[2:]))


class TestTranslations(unittest.TestCase):
    """Both languages carry the same keys."""

    def _flatten(self, value: object, prefix: str = "") -> set[str]:
        if isinstance(value, dict):
            keys: set[str] = set()
            for name, child in value.items():
                keys |= self._flatten(child, f"{prefix}.{name}")
            return keys
        return {prefix}

    def test_same_keys_in_both_languages(self) -> None:
        english = json.loads(
            (INTEGRATION_DIR / "translations" / "en.json").read_text(encoding="utf-8")
        )
        korean = json.loads(
            (INTEGRATION_DIR / "translations" / "ko.json").read_text(encoding="utf-8")
        )
        self.assertEqual(self._flatten(english), self._flatten(korean))

    def test_required_sections(self) -> None:
        english = json.loads(
            (INTEGRATION_DIR / "translations" / "en.json").read_text(encoding="utf-8")
        )
        for section in ("config", "options", "services", "exceptions", "issues"):
            self.assertIn(section, english)


class TestInjectedModule(unittest.TestCase):
    """The frontend module stays small, guarded and logic-free."""

    def setUp(self) -> None:
        self.source = (
            INTEGRATION_DIR / "frontend" / "naver-basemap.js"
        ).read_text(encoding="utf-8")

    def test_under_fifty_lines_of_code(self) -> None:
        code_lines = [
            line
            for line in self.source.splitlines()
            if line.strip() and not line.strip().startswith("//")
        ]
        self.assertLessEqual(len(code_lines), 50, len(code_lines))

    def test_double_wrap_guard(self) -> None:
        self.assertIn("__naverMapChangePatched", self.source)

    def test_every_path_is_inside_a_try(self) -> None:
        self.assertGreaterEqual(self.source.count("try {"), 2)
        self.assertGreaterEqual(self.source.count("catch"), 2)

    def test_rewrites_exactly_the_core_style_paths(self) -> None:
        from _bootstrap import const

        for path in const.CORE_STYLE_PATHS:
            self.assertIn(path, self.source)
        for variant in const.VARIANTS:
            self.assertIn(
                f"/api/map_tiles/naver_map_change/style/{variant}.json", self.source
            )

    def test_contains_no_secret_and_no_upstream_host(self) -> None:
        for forbidden in ("pstatic.net", "vworld.kr", "api_key", "token"):
            self.assertNotIn(forbidden, self.source)

    def test_does_not_touch_local_storage_or_eval(self) -> None:
        self.assertIsNone(re.search(r"\beval\b|localStorage", self.source))


if __name__ == "__main__":
    unittest.main()
