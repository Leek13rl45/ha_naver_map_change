"""HTTP views: the tile proxy and the MapLibre style endpoint.

Why a server-side proxy at all: API keys must never reach an unauthenticated
client (``vworld`` and ``custom`` carry the key in the URL), the naver version
code has to stay a server concern so the injected frontend module can be
stateless, caching is the only way to keep upstream traffic low, and mounting
under ``/api/map_tiles/`` lets core's ``withMapTilesToken()`` attach its
rotating token for us (docs/05-UPSTREAM-FINDINGS.md section 6,
docs/02-HA-PLATFORM-2026.md section 3.3).

Note on the route extension: our tile route ends in ``.png`` for consistency
with core's ``/api/map_tiles/raster/{z}/{x}/{y}.png``, but the bytes we serve
are whatever upstream sent - for naver that is ``image/jpeg`` at 256px
(docs/05-UPSTREAM-FINDINGS.md sections 2 and 3). This mismatch is harmless
because MapLibre and Leaflet both decode by ``Content-Type`` and ignore the
extension, and the proxy passes the upstream ``Content-Type`` through verbatim
(design decision D11).
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import TYPE_CHECKING

from aiohttp import ClientError, ClientTimeout, hdrs, web
from homeassistant.components.http import KEY_AUTHENTICATED, HomeAssistantView
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.json import json_bytes

from .cache import TileAsset, effective_ttl
from .const import (
    CORE_MAP_TILES_DATA_KEY,
    DOMAIN,
    FALLBACK_PROVIDER,
    FETCH_CHUNK_BYTES,
    ISSUE_MISSING_TOKEN_STORE,
    ISSUE_VERSION_UNAVAILABLE,
    MAX_FETCH_BYTES,
    STYLE_MAX_AGE,
    STYLE_URL_PATH,
    STYLE_VARIANT_QUERY,
    TILE_MAX_AGE,
    TILE_TTL,
    UPSTREAM_TIMEOUT_S,
    VARIANT_DARK,
    VARIANT_LIGHT,
    VARIANTS,
)
from .providers import (
    PROVIDERS,
    TileProvider,
    TileUrlError,
    build_proxy_tile_template,
    build_style,
    build_tile_url,
    resolve_headers,
    validate_tile_coords,
)

if TYPE_CHECKING:
    from . import NaverMapRuntimeData

_LOGGER = logging.getLogger(__name__)

# Registered aiohttp route. The digit constraints mirror core's raster view
# (``/api/map_tiles/raster/{z:[0-9]+}/...``) so non-numeric and negative parts
# are rejected by the routing layer; validate_tile_coords() still repeats the
# check because it is the tested, provider-aware defence (requirement R1).
TILE_ROUTE = f"/api/map_tiles/{DOMAIN}/{{z:[0-9]+}}/{{x:[0-9]+}}/{{y:[0-9]+}}.png"

# One timeout object, shared by every upstream call (core keeps its equivalent
# in const.py; ours lives here so const.py stays free of aiohttp).
UPSTREAM_TIMEOUT = ClientTimeout(total=UPSTREAM_TIMEOUT_S)


class _NaverMapView(HomeAssistantView):
    """Shared plumbing for the tile and style views."""

    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the view.

        The view holds only ``hass``. It must not capture the config entry:
        ``hass.http.register_view`` has no counterpart to unregister with, so a
        view outlives its entry and a captured entry would go stale across a
        reload (design decision D4).
        """
        self._hass = hass

    def _runtime_data(self) -> NaverMapRuntimeData | None:
        """Return the runtime data of the loaded entry, or None."""
        for entry in self._hass.config_entries.async_entries(DOMAIN):
            if entry.state is not ConfigEntryState.LOADED:
                continue
            runtime: NaverMapRuntimeData | None = getattr(entry, "runtime_data", None)
            if runtime is not None:
                return runtime
        return None

    def _authenticate(self, request: web.Request) -> None:
        """Authenticate via the standard middleware or a map_tiles query token.

        A copy of core ``_MapTilesView._authenticate`` (read from 2026.9.0), so
        that our tiles are exactly as protected as core's. The token store is
        read as ``hass.data["map_tiles"]`` - a ``deque[str]`` of at most two
        live tokens - rather than importing core's private ``DATA_ACCESS_TOKENS``
        (requirement R2, docs/03 section 3.4).
        """
        access_tokens = self._hass.data.get(CORE_MAP_TILES_DATA_KEY)
        if access_tokens is None:
            # Core map_tiles is loaded by the frontend integration on every
            # install with a UI, so this means core changed something. Refuse
            # rather than become an open proxy (docs/03 section 6.3).
            self._async_create_issue(ISSUE_MISSING_TOKEN_STORE)
            raise web.HTTPForbidden
        if request[KEY_AUTHENTICATED] or request.query.get("token") in access_tokens:
            return
        if hdrs.AUTHORIZATION in request.headers:
            # A real Bearer attempt, so let the ban middleware count it.
            raise web.HTTPUnauthorized
        # Most likely a query token that expired while a dashboard sat open, so
        # 403 rather than banning the user's own IP over it (core's reasoning).
        raise web.HTTPForbidden

    def _async_create_issue(self, issue_id: str) -> None:
        """Register a repairs issue, without letting it break the request."""
        try:
            ir.async_create_issue(
                self._hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=issue_id,
            )
        except Exception:  # noqa: BLE001 - never fail a map request over a hint
            _LOGGER.debug("Could not create repairs issue %s", issue_id)


class NaverMapTileView(_NaverMapView):
    """Proxy upstream tiles, from the cache on a hit."""

    url = TILE_ROUTE
    name = f"api:{DOMAIN}:tile"

    async def get(
        self, request: web.Request, z: str, x: str, y: str
    ) -> web.StreamResponse:
        """Handle a GET request for one tile."""
        self._authenticate(request)

        runtime = self._runtime_data()
        if runtime is None:
            # The entry is gone or not loaded. The view itself cannot be
            # unregistered, so this is the honest answer (design decision D4).
            return web.Response(status=HTTPStatus.SERVICE_UNAVAILABLE)

        provider = runtime.provider
        variant = (
            VARIANT_DARK
            if request.query.get(STYLE_VARIANT_QUERY) == VARIANT_DARK
            and runtime.dark_template is not None
            else VARIANT_LIGHT
        )

        # Upstream answers out-of-range coordinates with 200 and a blank tile
        # (docs/05 section 4), so this check is ours to make (D8).
        if (coords := validate_tile_coords(provider, z, x, y)) is None:
            return web.Response(status=HTTPStatus.NOT_FOUND)
        zoom, column, row = coords

        try:
            url = build_tile_url(
                provider,
                version=runtime.version,
                api_key=runtime.api_key,
                url_template=runtime.template_for(variant),
                variant=variant,
                z=zoom,
                x=column,
                y=row,
                ha_version=runtime.ha_version,
            )
        except TileUrlError as err:
            # Typically a naver version code that was never fetched.
            _LOGGER.debug("Cannot build tile URL: %s", err)
            self._async_create_issue(ISSUE_VERSION_UNAVAILABLE)
            return web.Response(status=HTTPStatus.SERVICE_UNAVAILABLE)

        # Version and variant are part of the key, so a new version code
        # separates keys by itself and needs no invalidation (D9, AC12).
        key = (provider.id, runtime.version or "", variant, zoom, column, row)

        async def _fetch() -> TileAsset | None:
            return await self._async_fetch(runtime, url)

        asset = await runtime.cache.async_get(key, TILE_TTL, _fetch)
        if asset is None:
            return web.Response(status=HTTPStatus.BAD_GATEWAY)

        # Whitelist, not passthrough: only Content-Type (via content_type
        # below) and Content-Encoding come from upstream. VWorld hands out a
        # JSESSIONID Set-Cookie even on failures, and an upstream ETag or Server
        # header says nothing about our response
        # (docs/05 section 7.2, design decision D7).
        headers = {hdrs.CACHE_CONTROL: f"private, max-age={TILE_MAX_AGE}"}
        if asset.encoding:
            headers[hdrs.CONTENT_ENCODING] = asset.encoding
        return web.Response(
            body=asset.body, content_type=asset.content_type, headers=headers
        )

    async def _async_fetch(
        self, runtime: NaverMapRuntimeData, url: str
    ) -> TileAsset | None:
        """Fetch one tile upstream, returning None on any upstream failure.

        Nothing that is not an image is ever cached or served: VWorld answers an
        invalid key with HTTP 200 and an OWS ExceptionReport XML body, so a
        status-only check would leak an authentication failure through as a tile
        (docs/05 section 7.2, design decision D7).
        """
        session = async_get_clientsession(self._hass)
        headers = resolve_headers(runtime.provider, ha_version=runtime.ha_version)
        try:
            async with session.get(
                url,
                headers=headers,
                timeout=UPSTREAM_TIMEOUT,
                # Keep the body in the encoding upstream sent, so it is cached
                # as-is instead of being recompressed for every client.
                auto_decompress=False,
            ) as response:
                if response.status == HTTPStatus.BAD_REQUEST:
                    # An expired version code (docs/05 section 5). Trigger a
                    # refresh, throttled so a burst of 400s cannot turn into a
                    # burst of upstream calls (D7).
                    _LOGGER.debug("Upstream %s returned 400, refreshing version", url)
                    runtime.request_version_refresh()
                    return None
                if response.status >= HTTPStatus.BAD_REQUEST:
                    _LOGGER.debug("Upstream %s returned %s", url, response.status)
                    return None

                content_type = response.headers.get(hdrs.CONTENT_TYPE, "")
                media_type = content_type.partition(";")[0].strip().lower()
                if not media_type.startswith("image/"):
                    _LOGGER.warning(
                        "Upstream %s answered %s with %r, refusing it",
                        url,
                        response.status,
                        content_type or "no Content-Type",
                    )
                    return None

                chunks: list[bytes] = []
                read = 0
                async for chunk in response.content.iter_chunked(FETCH_CHUNK_BYTES):
                    read += len(chunk)
                    if read > MAX_FETCH_BYTES:
                        _LOGGER.warning(
                            "Upstream %s body exceeds %s bytes, refusing it",
                            url,
                            MAX_FETCH_BYTES,
                        )
                        return None
                    chunks.append(chunk)
                encoding = response.headers.get(hdrs.CONTENT_ENCODING)
                cache_control = response.headers.get(hdrs.CACHE_CONTROL, "")
        except (ClientError, TimeoutError) as err:
            _LOGGER.debug("Upstream %s failed: %s", url, err)
            return None

        return TileAsset(
            body=b"".join(chunks),
            content_type=media_type,
            encoding=encoding,
            # Honor upstream max-age but cap it: naver sends one year, which
            # would outlive the version code it was fetched under (R3).
            ttl=effective_ttl(cache_control, TILE_TTL),
        )


class NaverMapStyleView(_NaverMapView):
    """Serve a raster-only MapLibre style pointing back at our tile proxy."""

    url = STYLE_URL_PATH
    name = f"api:{DOMAIN}:style"
    # No token check: core's loadStyle() fetches the style with a plain fetch
    # and no token (docs/02 section 3.2). This is only safe because the
    # document contains no API key and no upstream hostname - see build_style().

    async def get(self, request: web.Request, variant: str) -> web.StreamResponse:
        """Handle a GET request for the style document."""
        if variant not in VARIANTS:
            return web.Response(status=HTTPStatus.NOT_FOUND)

        provider, attribution = self._resolve_style_provider(variant)
        style = build_style(
            provider,
            variant=variant,
            tile_url_template=build_proxy_tile_template(provider, variant=variant),
            attribution=attribution,
        )
        # This endpoint never fails: a 404, a 500 or an invalid document would
        # leave the user with an empty basemap, and "failure falls back to the
        # default map" is a hard constraint (docs/03 section 0.3, D10).
        return web.Response(
            body=json_bytes(style),
            content_type="application/json",
            headers={hdrs.CACHE_CONTROL: f"private, max-age={STYLE_MAX_AGE}"},
        )

    def _resolve_style_provider(self, variant: str) -> tuple[TileProvider, str | None]:
        """Return the provider whose style to serve, plus its attribution.

        Falls back to ``osm`` when the configured provider cannot produce tile
        URLs yet - a naver version code that was never fetched, for instance -
        so the answer is always a usable style (design decision D10).
        """
        fallback = PROVIDERS[FALLBACK_PROVIDER]

        runtime = self._runtime_data()
        if runtime is None:
            return fallback, None
        if not runtime.can_build_tile_urls():
            self._async_create_issue(ISSUE_VERSION_UNAVAILABLE)
            return fallback, None
        return runtime.provider, runtime.attribution


def async_register_views(hass: HomeAssistant) -> None:
    """Register both views. Called once per process (design decision D5)."""
    hass.http.register_view(NaverMapTileView(hass))
    hass.http.register_view(NaverMapStyleView(hass))
