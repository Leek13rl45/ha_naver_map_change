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

# The layer selector every naver tile request carries (design decision D14).
# Kept as a literal here rather than imported from const.py, so the test data
# fails loudly if the constant is changed by accident.
MAP_TYPES_QUERY = "?mt=bg.ol.ts.ar.lko"

# The proxy never decodes the body, so a short marker is enough.
TILE_BODY = b"\xff\xd8\xff\xd9tile-bytes"


# A distinct body, so a test can tell which of the two resolutions was served.
TILE_BODY_2X = b"\xff\xd8\xff\xd9tile-bytes-at-2x-which-is-larger"


def tile_url(version: str = VERSION, z: int = 12, x: int = 3492, y: int = 1586) -> str:
    """Return the upstream naver 1x tile URL (.png + mt, decisions D13/D14)."""
    base = f"https://map.pstatic.net/nrb/styles/basic/{version}/{z}/{x}/{y}"
    return f"{base}.png{MAP_TYPES_QUERY}"


def tile_url_2x(
    version: str = VERSION, z: int = 12, x: int = 3492, y: int = 1586
) -> str:
    """Return the upstream naver @2x tile URL (docs/05 section 3, D12-D14)."""
    base = f"https://map.pstatic.net/nrb/styles/basic/{version}/{z}/{x}/{y}"
    return f"{base}@2x.png{MAP_TYPES_QUERY}"
