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
INTEGRATION_VERSION = "2.1.0"

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
# Sizing note for design decisions D12/D13/D14. Measured, with the mt selector
# below: 1x .png is 19,420 bytes and 2x .png is 50,772 bytes, so a Retina
# client's tile costs about 2.6x - and about 3x the 16,360-byte .jpg tile v2.0.1
# used to serve. At this ceiling that is roughly 650 tiles cached instead of
# ~2000. Deliberately not raised: it is a user option (CONF_CACHE_MAX_BYTES),
# eviction is LRU so the working set simply becomes the most recent third, and
# growing everyone's default memory budget would be the wrong trade on the
# Raspberry Pi installs this has to fit.
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

# --- Tile scale (device pixel ratio) ------------------------------------
# Naver serves an @2x variant that really is 512x512
# (docs/05-UPSTREAM-FINDINGS.md section 3). Which one a client gets is a query
# parameter on both our routes, never a path segment: core's
# withMapTilesToken() appends its rotating token onto ``parsed.search``, so a
# query survives tokenization while a new path segment would need a second
# route (docs/02-HA-PLATFORM-2026.md section 3.3). This is the same mechanism
# the dark variant already uses.
TILE_SCALE_QUERY = "scale"
STYLE_DPR_QUERY = "dpr"
SCALE_NORMAL = 1
SCALE_RETINA = 2

# --- Naver layer selector (design decision D14) --------------------------
# ``mt`` is an undocumented upstream query parameter that selects which layer
# groups a tile is composed from. The value below is the one the 1.x
# implementation used (``git show v1.3.0:.../__init__.py``,
# ``build_naver_tile_url()``) and it is what the user chose: everything.
#
# Measured per component at z17 over Gangnam station, on the @2x .png tile:
#
#   mt                  bytes   contents
#   bg                    155   background only, effectively a blank tile
#   bg.ol              34,041   + roads, buildings, subway lines. NO labels
#   bg.lko             24,201   + Korean labels
#   bg.ol.lko          47,916   labels yes, bus stops no
#   bg.ol.ts           35,439   + transit
#   bg.ol.ar           34,041   identical md5 to bg.ol on this tile
#   bg.ol.ts.ar.lko    50,772   <- this constant: everything
#   (parameter absent)  49,967   upstream default, and NOT the same tile
#
# Component meanings are an *inference*, not documentation: bg = background,
# ol = roads/outlines, ts = transit, ar = areas, lko = Korean labels. Unknown
# components (``te``, ``tr``, ...) are answered with HTTP 400, so this string
# must not be assembled from guesses - it is only ever the measured value.
NAVER_MAP_TYPES = "bg.ol.ts.ar.lko"
NAVER_MAP_TYPES_QUERY = "mt"

# --- Config / options keys ----------------------------------------------
CONF_PROVIDER = "provider"
CONF_API_KEY = "api_key"
CONF_URL_TEMPLATE = "url_template"
CONF_ATTRIBUTION = "attribution"
CONF_DARK_VARIANT = "dark_variant"
CONF_CACHE_MAX_BYTES = "cache_max_bytes"
CONF_RETINA = "retina"

DEFAULT_PROVIDER = "naver"
DEFAULT_DARK_VARIANT = True
# On by default: a 256px tile stretched over a devicePixelRatio 2 display is
# visibly blurry, which is what real users reported. It stays switchable
# because @2x costs roughly 3.2x the bytes (see CACHE_MAX_BYTES above), and
# that is a real cost on a metered mobile connection.
DEFAULT_RETINA = True
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
