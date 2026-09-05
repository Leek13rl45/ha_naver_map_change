"""Basemap tile providers and the pure logic that turns them into URLs.

Design decision D1: this module must stay importable without Home Assistant, so
that URL building, coordinate validation, style generation and TileJSON parsing
are unit-testable on their own. Only the standard library is imported at
runtime; ``homeassistant`` and ``aiohttp`` types appear behind ``TYPE_CHECKING``
only. ``view.py`` keeps all HTTP wiring, this module keeps all decisions.

Every constant below traces to ``docs/05-UPSTREAM-FINDINGS.md`` section 8, which
records the ``curl`` measurements. Nothing here is guessed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .const import (
    MAX_COORDINATE_DIGITS,
    NAVER_MAP_TYPES,
    NAVER_MAP_TYPES_QUERY,
    SCALE_NORMAL,
    SCALE_RETINA,
    STYLE_VARIANT_QUERY,
    TILE_SCALE_QUERY,
    TILE_URL_TEMPLATE,
    VARIANT_DARK,
    VARIANT_LIGHT,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from aiohttp import ClientSession


class TileUrlError(ValueError):
    """Raised when a tile URL cannot be built from the given inputs."""


@dataclass(frozen=True, kw_only=True)
class TileProvider:
    """An upstream basemap source.

    ``url_template`` may contain ``{z}``, ``{x}``, ``{y}`` and optionally
    ``{version}``, ``{api_key}`` and ``{ha_version}``. Substitution is explicit
    ``str.replace``, never ``str.format`` (design decision D2).
    """

    id: str
    name: str
    url_template: str
    url_template_dark: str | None = None
    # The @2x (512px) templates, design decision D12. None means "this upstream
    # has no high-DPI variant we have measured", and a request for scale 2 then
    # falls back to the 1x template silently rather than failing: a slightly
    # blurry map beats no map (hard constraint 3). Nothing here is guessed -
    # only naver has a measured @2x (docs/05-UPSTREAM-FINDINGS.md section 3).
    url_template_retina: str | None = None
    url_template_dark_retina: str | None = None
    # Request headers the proxy adds. Naver needs none (finding F2), but the
    # field is kept so a future upstream gate can be answered by editing a
    # provider definition instead of the fetch code (docs/04 section 7).
    headers: Mapping[str, str] = field(default_factory=dict)
    # Always our own string: upstream sends attribution "" (findings section 1),
    # and the display obligation must be guaranteed in code.
    attribution: str = ""
    min_zoom: int = 1
    max_zoom: int = 20
    max_native_zoom: int = 19
    tile_size: int = 256
    needs_api_key: bool = False
    needs_url_template: bool = False
    # Data-driven version refresh (design decision D3): when set, this TileJSON
    # URL is fetched and its "version" key becomes {version} in the tile URL.
    # Replaces the spec's name-based ``version_refresher`` dispatch.
    version_meta_url: str | None = None
    # True when this definition could not be verified against a live upstream.
    unverified: bool = False


PROVIDER_NAVER = "naver"
PROVIDER_OSM = "osm"
PROVIDER_VWORLD = "vworld"
PROVIDER_CUSTOM = "custom"

# The four naver templates are assembled from one shape, with the style name as
# the only variable, so light/dark and 1x/2x can never drift apart. ``mt`` is
# the layer selector; its measured per-component byte counts are tabulated on
# NAVER_MAP_TYPES in const.py (design decision D14).
_NAVER_QUERY = f"?{NAVER_MAP_TYPES_QUERY}={NAVER_MAP_TYPES}"


def _naver_template(style: str, *, retina: bool = False) -> str:
    """Return the naver tile template for one style family and scale."""
    suffix = "@2x.png" if retina else ".png"
    return (
        f"https://map.pstatic.net/nrb/styles/{style}"
        f"/{{version}}/{{z}}/{{x}}/{{y}}{suffix}{_NAVER_QUERY}"
    )


# Kept as names so the two style families are visible at a glance.
_NAVER_STYLE_LIGHT = "basic"
# Design decision D15. The dark family exists; finding F8 ("no dark family")
# was simply wrong - the prefix is "d", which the original survey never tried.
# ``dterrain`` also answers 200 but is not used here: this integration serves
# the ``basic`` family only. ``dsatellite``/``dhybrid``/``dnavi``/``dbasic_ko``
# are 404 (docs/05-UPSTREAM-FINDINGS.md section 1, F8 correction).
_NAVER_STYLE_DARK = "dbasic"

_NAVER = TileProvider(
    id=PROVIDER_NAVER,
    name="NAVER Map (unofficial)",
    # Measured: findings section 1 (TileJSON "tiles[0]") and sections 2-3.
    #
    # ``.png``, not ``.jpg`` (design decision D13, reversing the v2.0.1 choice):
    # a map tile is mostly glyphs and hairlines, which is precisely what JPEG's
    # block artefacts destroy, and the 1.x implementation used .png for that
    # reason. Measured 200 image/png, 19,420 bytes, 256x256.
    #
    # ``?mt=`` (design decision D14): without it upstream composes its own
    # default layer set, which measurably is *not* the same tile - bus stops,
    # subway exits and transit detail are missing from it.
    url_template=_naver_template(_NAVER_STYLE_LIGHT),
    # Design decision D15, correcting finding F8. Measured 200 image/png,
    # 19,444 bytes at 1x and 53,195 bytes at @2x, carrying the same Korean
    # labels, bus stops, subway exits and line colours as the light family.
    url_template_dark=_naver_template(_NAVER_STYLE_DARK),
    # Measured: 200 image/png, 50,772 bytes, `file` reports 512x512
    # (docs/05-UPSTREAM-FINDINGS.md section 3).
    url_template_retina=_naver_template(_NAVER_STYLE_LIGHT, retina=True),
    url_template_dark_retina=_naver_template(_NAVER_STYLE_DARK, retina=True),
    headers={},  # F2: measured 200 with zero headers.
    attribution="© NAVER",
    min_zoom=0,  # TileJSON minzoom
    max_zoom=20,  # aligned with core MAP_MAX_ZOOM
    max_native_zoom=21,  # TileJSON maxzoom (findings section 4)
    tile_size=256,
    # One refresher covers both families: ``dbasic.json`` publishes the *same*
    # version code as ``basic.json`` (measured, D15), and dbasic's TileJSON is
    # otherwise identical too - minzoom 0, maxzoom 21. A second refresher would
    # be a second upstream call for a value we already have.
    version_meta_url="https://map.pstatic.net/nrb/styles/basic.json",
)

_OSM = TileProvider(
    id=PROVIDER_OSM,
    name="OpenStreetMap",
    url_template="https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    # Deliberately None: core 2026.9 deleted its own @2x branch with the comment
    # "OSM serves no @2x variant" (docs/02-HA-PLATFORM-2026.md section 3.2).
    # Inventing a template here would produce 404s on every tile.
    url_template_retina=None,
    # OSMF policy wants an identifying User-Agent; {ha_version} is filled in by
    # resolve_headers() so this module needs no homeassistant import.
    headers={"User-Agent": "HomeAssistant/{ha_version} (+naver_map_change)"},
    attribution="© OpenStreetMap contributors",
    min_zoom=1,
    max_zoom=20,
    max_native_zoom=19,
    tile_size=256,
)

_VWORLD = TileProvider(
    id=PROVIDER_VWORLD,
    name="VWorld (국토교통부 공간정보 오픈플랫폼)",
    # UNVERIFIED - could not be measured because no valid key was available
    # (findings section 7.2). Note the {z}/{y}/{x} order, taken from docs/04
    # section 4.1; it must be confirmed with a valid key.
    url_template="https://api.vworld.kr/req/wmts/1.0.0/{api_key}/Base/{z}/{y}/{x}.png",
    # None, not a guess: the 1x template itself is still unverified, so a
    # high-DPI form of it would be a guess on top of a guess (findings 7.2).
    url_template_retina=None,
    headers={},
    attribution="© 국토교통부 공간정보 오픈플랫폼(VWorld)",
    min_zoom=1,
    max_zoom=20,
    max_native_zoom=19,
    tile_size=256,
    needs_api_key=True,
    unverified=True,
)

_CUSTOM = TileProvider(
    id=PROVIDER_CUSTOM,
    name="Custom tile URL",
    # Placeholder only; the user supplies the real template, which is passed to
    # build_tile_url() as url_template and never format()-ed (D2).
    url_template="{z}/{x}/{y}",
    # None on purpose: an arbitrary third-party XYZ endpoint cannot be assumed
    # to understand @2x, and appending it blindly would break every custom
    # provider that does not. A future extension could ask the user for a
    # second, optional high-DPI template; it is not invented on their behalf.
    url_template_retina=None,
    headers={},
    attribution="",
    min_zoom=1,
    max_zoom=20,
    max_native_zoom=19,
    tile_size=256,
    needs_url_template=True,
)

PROVIDERS: Mapping[str, TileProvider] = {
    provider.id: provider for provider in (_NAVER, _OSM, _VWORLD, _CUSTOM)
}

# Placeholders we substitute, and nothing else (D2).
_PLACEHOLDER_RE = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")
# The version code upstream publishes is a digit string; keep the accepted set
# narrow so a hostile TileJSON cannot inject path segments into the tile URL.
_VERSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def get_provider(provider_id: str | None) -> TileProvider | None:
    """Return the provider with this id, or None when unknown."""
    if not provider_id:
        return None
    return PROVIDERS.get(provider_id)


def build_tile_url(
    provider: TileProvider,
    *,
    version: str | None = None,
    api_key: str | None = None,
    url_template: str | None = None,
    variant: str = VARIANT_LIGHT,
    scale: int = SCALE_NORMAL,
    z: int,
    x: int,
    y: int,
    ha_version: str | None = None,
) -> str:
    """Build the upstream tile URL for one tile.

    Substitution is explicit ``str.replace`` rather than ``str.format`` (design
    decision D2): the ``custom`` provider template is user input, and
    ``str.format`` both raises on stray braces and treats the string as a format
    string, which is a needless hazard. Any placeholder still present after
    substitution means a required value was missing, which is an error rather
    than a silently broken URL.

    ``scale`` is the device pixel ratio bucket (design decision D12).
    ``scale == 2`` asks for the @2x template; a provider without one falls back
    to its normal template *silently*, because "failure falls back to the
    default behaviour" is a hard constraint and a 404 here would punch holes in
    the basemap.
    """
    template = url_template or _select_template(provider, variant, scale)

    url = template.replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))
    if version:
        url = url.replace("{version}", version)
    if api_key:
        url = url.replace("{api_key}", api_key)
    if ha_version:
        url = url.replace("{ha_version}", ha_version)

    if (leftover := _PLACEHOLDER_RE.search(url)) is not None:
        raise TileUrlError(
            f"Tile URL template for provider {provider.id} still contains "
            f"{leftover.group(0)}: no value was available for it"
        )
    return url


def retina_template(
    provider: TileProvider, variant: str = VARIANT_LIGHT
) -> str | None:
    """Return the @2x template for this variant, or None when there is none.

    A dark ``@2x`` template only makes sense for a provider that has its own
    dark family; when the dark variant reuses the light tiles (naver, F8), the
    light ``@2x`` template is the correct high-DPI answer for it too.
    """
    if variant == VARIANT_DARK and provider.url_template_dark:
        return provider.url_template_dark_retina
    return provider.url_template_retina


def supports_retina(provider: TileProvider, variant: str = VARIANT_LIGHT) -> bool:
    """Return whether this provider/variant has a measured @2x template."""
    return retina_template(provider, variant) is not None


def _select_template(
    provider: TileProvider, variant: str, scale: int = SCALE_NORMAL
) -> str:
    """Return the template for this variant and scale.

    Falls back light-ward and 1x-ward rather than raising: an absent dark or
    absent @2x template is a normal state, not an error.
    """
    if scale == SCALE_RETINA and (retina := retina_template(provider, variant)):
        return retina
    if variant == VARIANT_DARK and provider.url_template_dark:
        return provider.url_template_dark
    return provider.url_template


def build_proxy_tile_template(
    provider: TileProvider,
    *,
    variant: str = VARIANT_LIGHT,
    base: str = TILE_URL_TEMPLATE,
) -> str:
    """Return the tile template that goes into the style document.

    Always one of our own root-relative proxy paths, so no API key and no
    upstream hostname can reach an unauthenticated client (docs/03 3.4, AC6).
    The dark variant is expressed as a query parameter rather than a second
    route: ``withMapTilesToken()`` preserves the query string when it appends
    the rotating token (docs/02 section 3.3).
    """
    if variant == VARIANT_DARK and provider.url_template_dark:
        return f"{base}?{STYLE_VARIANT_QUERY}={VARIANT_DARK}"
    return base


def _with_scale(
    tile_url_template: str, provider: TileProvider, variant: str, scale: int
) -> str:
    """Append ``?scale=2`` to our proxy template when @2x is really available.

    A query parameter rather than a path segment, for the same reason the dark
    variant is one: ``withMapTilesToken()`` appends the rotating token onto the
    existing query string, so ``?variant=dark&scale=2&token=...`` all coexist on
    one route (docs/02 section 3.3).
    """
    if scale != SCALE_RETINA or not supports_retina(provider, variant):
        return tile_url_template
    separator = "&" if "?" in tile_url_template else "?"
    return f"{tile_url_template}{separator}{TILE_SCALE_QUERY}={SCALE_RETINA}"


def validate_tile_coords(
    provider: TileProvider, z_raw: str, x_raw: str, y_raw: str
) -> tuple[int, int, int] | None:
    """Validate raw z/x/y path parts, returning None when they are unusable.

    Validation is entirely our responsibility: upstream answers out-of-range
    coordinates with 200 and a blank tile instead of 404 (findings section 4),
    so without this an arbitrary coordinate stream could pollute the cache
    without bound. Order per design decision D8.
    """
    # 1. Cap the digit count before int(), which is expensive on huge digit
    #    strings (same defence as core views.py).
    if any(len(part) > MAX_COORDINATE_DIGITS for part in (z_raw, x_raw, y_raw)):
        return None

    # 2. Reject anything that is not an integer.
    try:
        z, x, y = int(z_raw), int(x_raw), int(y_raw)
    except (TypeError, ValueError):
        return None

    # 3. Zoom must be inside what the provider actually serves.
    if z < provider.min_zoom or z > provider.max_native_zoom:
        return None

    # 4. Column and row must be inside the pyramid for that zoom.
    limit = 2**z
    if not (0 <= x < limit) or not (0 <= y < limit):
        return None

    return z, x, y


def build_style(
    provider: TileProvider,
    *,
    variant: str = VARIANT_LIGHT,
    tile_url_template: str,
    attribution: str | None = None,
    scale: int = SCALE_NORMAL,
) -> dict[str, Any]:
    """Build a raster-only MapLibre style document (Style Spec v8).

    ``glyphs`` and ``sprite`` are deliberately absent: a raster style needs
    neither, and core ``loadStyle()`` passes a missing ``sprite`` straight
    through (docs/02 section 3.2).

    ``scale`` asks the tile route for @2x bytes by appending ``?scale=2`` to the
    template. It is ignored unless this provider/variant actually has a measured
    @2x template, so a client on a Retina display still gets a working style
    from ``osm``, ``vworld`` or ``custom``.

    ★ ``tileSize`` stays ``provider.tile_size`` (256) even at scale 2, and that
    is the whole point rather than an oversight. In a MapLibre raster source
    ``tileSize`` is the *logical* size the tile covers, not the pixel size of
    the image that comes back: declaring a 512px image as ``tileSize: 256`` is
    exactly how you say "draw this at double pixel density". Setting
    ``tileSize: 512`` instead shifts the zoom pyramid by one level, so the
    basemap would render at the wrong scale and the wrong place. The rule is:
    the @2x switch changes the bytes we fetch and nothing about the geometry.
    """
    source: dict[str, Any] = {
        "type": "raster",
        "tiles": [_with_scale(tile_url_template, provider, variant, scale)],
        # See the ★ note above: never provider.tile_size * scale.
        "tileSize": provider.tile_size,
        "minzoom": provider.min_zoom,
        "maxzoom": min(provider.max_native_zoom, provider.max_zoom),
        # From the provider constant, never from upstream: upstream sends ""
        # (findings section 1) and the display obligation is not optional.
        "attribution": attribution if attribution is not None else provider.attribution,
    }
    return {
        "version": 8,
        "name": f"naver_map_change {provider.id} {variant}",
        "sources": {"basemap": source},
        "layers": [{"id": "basemap", "type": "raster", "source": "basemap"}],
    }


def resolve_headers(
    provider: TileProvider, *, ha_version: str | None = None
) -> dict[str, str]:
    """Return the upstream request headers, with {ha_version} substituted."""
    resolved: dict[str, str] = {}
    for name, value in provider.headers.items():
        if ha_version:
            value = value.replace("{ha_version}", ha_version)
        elif "{ha_version}" in value:
            # No version to substitute: drop the header rather than send a
            # literal placeholder upstream.
            continue
        resolved[name] = value
    return resolved


def parse_tilejson_version(payload: str | bytes) -> str | None:
    """Return the ``version`` field of a TileJSON document, or None.

    Upstream publishes TileJSON 2.1.0 whose ``version`` is the code that the
    tile path needs (findings section 1). No regex fallback: the old
    implementation's second attempt hit the same URL and could not help.
    """
    try:
        document = json.loads(payload)
    except (TypeError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    version = document.get("version")
    if isinstance(version, int):
        version = str(version)
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        return None
    return version


async def async_fetch_tilejson_version(
    session: ClientSession,
    url: str,
    *,
    timeout: Any = None,
    headers: Mapping[str, str] | None = None,
) -> str | None:
    """Fetch a TileJSON document and return its version code, or None.

    The session is passed in so this module needs no Home Assistant import
    (design decision D1); ``view.py`` / ``__init__.py`` supply
    ``async_get_clientsession(hass)``. Any failure returns None and the caller
    keeps the last known good version.
    """
    kwargs: dict[str, Any] = {}
    if timeout is not None:
        kwargs["timeout"] = timeout
    if headers:
        kwargs["headers"] = dict(headers)
    try:
        async with session.get(url, **kwargs) as response:
            if response.status != 200:
                return None
            payload = await response.read()
    except Exception:  # noqa: BLE001 - a refresh failure must never propagate
        return None
    return parse_tilejson_version(payload)
