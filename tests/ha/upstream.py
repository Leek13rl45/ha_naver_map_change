"""Upstream fixtures data, shared by the Home Assistant tests.

Kept out of conftest.py so the test modules can import it directly (the ``ha``
directory is deliberately not a package, so relative imports are unavailable).
All values are verbatim from docs/05-UPSTREAM-FINDINGS.md.
"""

from __future__ import annotations

from typing import Any

TILEJSON_URL = "https://map.pstatic.net/nrb/styles/basic.json"
VERSION = "1787907321"
NEW_VERSION = "1799999999"

# Verbatim from docs/05-UPSTREAM-FINDINGS.md section 1.
TILEJSON_BODY: dict[str, Any] = {
    "tilejson": "2.1.0",
    "name": "",
    "attribution": "",
    "scheme": "xyz",
    "minzoom": 0,
    "maxzoom": 21,
    "version": VERSION,
    "format": "jpg",
    "tiles": [
        f"https://map.pstatic.net/nrb/styles/basic/{VERSION}/{{z}}/{{x}}/{{y}}.jpg"
    ],
}

# The proxy never decodes the body, so a short marker is enough.
TILE_BODY = b"\xff\xd8\xff\xd9tile-bytes"


def tile_url(version: str = VERSION, z: int = 12, x: int = 3492, y: int = 1586) -> str:
    """Return the upstream naver tile URL for these coordinates."""
    return f"https://map.pstatic.net/nrb/styles/basic/{version}/{z}/{x}/{y}.jpg"
