"""Constants for the Naver Map Change integration.

This module intentionally imports nothing from ``homeassistant`` so that the
pure-logic modules that depend on it (``providers``, ``cache``) stay testable
without a Home Assistant installation (design decision D1).

Constant provenance:
* Values mirroring Home Assistant core ``map_tiles`` come from
  ``docs/02-HA-PLATFORM-2026.md`` section 3.4.
* Upstream-derived values come from ``docs/05-UPSTREAM-FINDINGS.md`` section 8.
No value in this file is guessed.
"""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "naver_map_change"

# Kept in sync with manifest.json "version" by hand. The manifest is *not* read
# at runtime because file reads on the event loop are forbidden (design D6,
# docs/02-HA-PLATFORM-2026.md section 4.7).
INTEGRATION_VERSION = "2.0.0"

# Minimum supported Home Assistant version. Earlier releases have a different
# map architecture (docs/02-HA-PLATFORM-2026.md section 3).
MIN_HA_VERSION = "2026.9.0"

# --- URL namespace -------------------------------------------------------
# We deliberately mount under the core map_tiles namespace: the frontend helper
# withMapTilesToken() attaches a valid rotating token to *every* URL whose
# pathname starts with "/api/map_tiles/" (docs/02-HA-PLATFORM-2026.md 3.3).
CORE_MAP_TILES_PATH = "/api/map_tiles"
URL_BASE = f"{CORE_MAP_TILES_PATH}/{DOMAIN}"

# aiohttp route templates (registered by view.py).
TILE_URL_PATH = f"{URL_BASE}/{{z}}/{{x}}/{{y}}.png"
STYLE_URL_PATH = f"{URL_BASE}/style/{{variant}}.json"

# Template handed to MapLibre inside the style document. MapLibre substitutes
# {z}/{x}/{y} itself, so the braces must survive into the response.
TILE_URL_TEMPLATE = f"{URL_BASE}/{{z}}/{{x}}/{{y}}.png"

# Key of the core map_tiles token store in hass.data. Kept as a local string
# instead of importing homeassistant.components.map_tiles.const to avoid
# depending on a core internal (docs/03-REDESIGN-SPEC.md 3.4).
CORE_MAP_TILES_DATA_KEY = "map_tiles"

# --- Injected frontend module -------------------------------------------
FRONTEND_URL_PATH = f"/{DOMAIN}_frontend"
FRONTEND_SCRIPT = "naver-basemap.js"

# Style URLs the core frontend fetches; the injected module rewrites these
# (docs/02-HA-PLATFORM-2026.md 3.2, VECTOR_STYLES).
CORE_STYLE_PATHS = ("/static/map/light.json", "/static/map/dark.json")

# --- Limits, aligned with core map_tiles/const.py -----------------------
UPSTREAM_TIMEOUT_S = 10
CACHE_MAX_BYTES = 32 * 1024 * 1024
MAX_FETCH_BYTES = 8 * 1024 * 1024
MAX_CONCURRENT_FETCHES = 16
FETCH_CHUNK_BYTES = 64 * 1024
TILE_TTL = 7 * 24 * 60 * 60
TILE_MAX_AGE = 7 * 24 * 60 * 60
MAX_COORDINATE_DIGITS = 8

# Periodic naver version-code refresh. Upstream declares cache-control
# max-age=300 on the TileJSON, so 6h is deliberately conservative
# (docs/05-UPSTREAM-FINDINGS.md section 1).
VERSION_REFRESH_INTERVAL = timedelta(hours=6)
# An upstream HTTP 400 means "version code expired"
# (docs/05-UPSTREAM-FINDINGS.md section 5). Refreshes triggered that way are
# throttled so a burst of 400s cannot turn into a burst of upstream calls (D7).
VERSION_REFRESH_MIN_INTERVAL = timedelta(minutes=5)

# The style document is cheap to rebuild and is how a provider switch reaches
# the browser, so it gets a short max-age. Core uses the same 5 minutes for its
# TileJSON (TILEJSON_MAX_AGE, docs/02 section 3.4).
STYLE_MAX_AGE = 5 * 60

# --- Style variants ------------------------------------------------------
VARIANT_LIGHT = "light"
VARIANT_DARK = "dark"
VARIANTS = (VARIANT_LIGHT, VARIANT_DARK)
STYLE_VARIANT_QUERY = "variant"

# --- Config / options keys ----------------------------------------------
CONF_PROVIDER = "provider"
CONF_API_KEY = "api_key"
CONF_URL_TEMPLATE = "url_template"
CONF_ATTRIBUTION = "attribution"
CONF_DARK_VARIANT = "dark_variant"
CONF_CACHE_MAX_BYTES = "cache_max_bytes"

DEFAULT_PROVIDER = "naver"
DEFAULT_DARK_VARIANT = True
# Provider used when the configured one cannot produce tile URLs yet (for
# example a naver version code that was never fetched). The style endpoint must
# still answer with a valid MapLibre document (design D10).
FALLBACK_PROVIDER = "osm"

# Connection-test tile: Seoul city hall at z12
# (docs/05-UPSTREAM-FINDINGS.md section 2).
TEST_TILE_Z = 12
TEST_TILE_X = 3492
TEST_TILE_Y = 1586

# --- Services ------------------------------------------------------------
SERVICE_REFRESH_VERSION = "refresh_version"
SERVICE_CLEAR_CACHE = "clear_cache"

# --- hass.data / repairs keys -------------------------------------------
DATA_REGISTERED = f"{DOMAIN}_registered"
ISSUE_MISSING_TOKEN_STORE = "missing_token_store"
ISSUE_VERSION_UNAVAILABLE = "version_unavailable"
ISSUE_RESTART_REQUIRED = "restart_required"
