"""The Naver Map Change integration.

Replaces the Home Assistant basemap with a Korean provider without touching a
single file outside ``/config``. Three pieces do the work:

1. a tile proxy at ``/api/map_tiles/naver_map_change/{z}/{x}/{y}.png``,
2. a MapLibre style endpoint at ``.../style/{light|dark}.json``,
3. a ~40 line frontend module that rewrites the style URL core fetches.

Everything lives inside this integration folder, so it survives a Home
Assistant Core update - unlike the previous implementation, which rewrote the
minified bundles of the installed frontend package and was erased by every
update (docs/01-AS-IS-ANALYSIS.md, docs/02-HA-PLATFORM-2026.md section 2).
Nothing in this integration reads, writes, backs up or compresses any file
outside its own folder.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any

from homeassistant.components.frontend import add_extra_js_url, remove_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import ConfigEntryError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType

from .cache import TileCache
from .const import (
    CACHE_MAX_BYTES,
    CONF_API_KEY,
    CONF_ATTRIBUTION,
    CONF_CACHE_MAX_BYTES,
    CONF_DARK_VARIANT,
    CONF_PROVIDER,
    CONF_RETINA,
    CONF_URL_TEMPLATE,
    DATA_REGISTERED,
    DEFAULT_DARK_VARIANT,
    DEFAULT_PROVIDER,
    DEFAULT_RETINA,
    DOMAIN,
    FRONTEND_SCRIPT,
    FRONTEND_URL_PATH,
    INTEGRATION_VERSION,
    ISSUE_RESTART_REQUIRED,
    ISSUE_VERSION_UNAVAILABLE,
    SCALE_NORMAL,
    SCALE_RETINA,
    SERVICE_CLEAR_CACHE,
    SERVICE_REFRESH_VERSION,
    VARIANT_DARK,
    VERSION_REFRESH_INTERVAL,
    VERSION_REFRESH_MIN_INTERVAL,
)
from .providers import (
    PROVIDER_CUSTOM,
    TileProvider,
    TileUrlError,
    async_fetch_tilejson_version,
    build_tile_url,
    get_provider,
    retina_template,
)
from .view import UPSTREAM_TIMEOUT, async_register_views

_LOGGER = logging.getLogger(__name__)

# No YAML configuration: everything is set up through the config flow.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type NaverMapConfigEntry = ConfigEntry[NaverMapRuntimeData]


@dataclass
class NaverMapRuntimeData:
    """Everything the views need at request time.

    Stored on ``entry.runtime_data`` rather than ``hass.data[DOMAIN]``, which is
    the documented pattern since 2024.4 and is cleaned up on unload
    (docs/02-HA-PLATFORM-2026.md section 4.3).
    """

    hass: HomeAssistant
    provider: TileProvider
    cache: TileCache
    api_key: str | None = None
    url_template: str | None = None
    attribution: str | None = None
    dark_variant: bool = DEFAULT_DARK_VARIANT
    # User veto on @2x tiles (design decision D12). On by default; turning it
    # off means a Retina client keeps getting 1x tiles and a third of the bytes.
    retina: bool = DEFAULT_RETINA
    version: str | None = None
    ha_version: str = HA_VERSION
    _last_version_refresh: float | None = field(default=None, init=False, repr=False)

    @property
    def dark_template(self) -> str | None:
        """Return the dark tile template, or None when there is none.

        Naver has no dark style family (docs/05 section 1), so this is None for
        the default provider and the light tiles are reused.
        """
        if not self.dark_variant:
            return None
        return self.provider.url_template_dark

    def template_for(self, variant: str, scale: int = SCALE_NORMAL) -> str | None:
        """Return an explicit URL template override for this variant and scale.

        None means "use the provider's own template"; a string is the
        provider's @2x template, the provider's dark template, or the user's
        ``custom`` template, in that order of precedence.

        The @2x template wins over the 1x dark override because
        ``retina_template()`` is already variant-aware: for a provider with its
        own dark family it returns the dark @2x template, and for one where dark
        reuses the light tiles it returns the light @2x one, which is the right
        answer in both cases. ``custom`` has no @2x template by definition, so a
        user template is never silently replaced.
        """
        if scale == SCALE_RETINA and (
            retina := retina_template(self.provider, variant)
        ) is not None:
            return retina
        if variant == VARIANT_DARK and self.dark_template:
            return self.dark_template
        if self.provider.id == PROVIDER_CUSTOM:
            return self.url_template
        return None

    def can_build_tile_urls(self) -> bool:
        """Return whether a tile URL can be built with what we know now."""
        try:
            build_tile_url(
                self.provider,
                version=self.version,
                api_key=self.api_key,
                url_template=self.template_for("light"),
                z=0,
                x=0,
                y=0,
                ha_version=self.ha_version,
            )
        except TileUrlError:
            return False
        return True

    async def async_refresh_version(self) -> tuple[str | None, bool]:
        """Refresh the upstream version code, returning (version, changed).

        Data-driven rather than a function-name lookup (design decision D3): a
        provider either has a ``version_meta_url`` to read a TileJSON
        ``version`` from, or it has no version code at all. A failure returns
        the last known good value and never interrupts the map.
        """
        self._last_version_refresh = time.monotonic()
        if (meta_url := self.provider.version_meta_url) is None:
            return None, False

        session = async_get_clientsession(self.hass)
        version = await async_fetch_tilejson_version(
            session, meta_url, timeout=UPSTREAM_TIMEOUT
        )
        if version is None:
            _LOGGER.debug("Version refresh for %s failed", self.provider.id)
            if self.version is None:
                # Never had a version, so tiles cannot be built at all. The
                # style endpoint falls back to osm and the user gets told.
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    ISSUE_VERSION_UNAVAILABLE,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=ISSUE_VERSION_UNAVAILABLE,
                )
            return self.version, False

        changed = version != self.version
        self.version = version
        ir.async_delete_issue(self.hass, DOMAIN, ISSUE_VERSION_UNAVAILABLE)
        if changed:
            # No cache invalidation needed: the version is part of the cache key,
            # so old entries simply stop being addressed (D9, docs/05 section 5).
            _LOGGER.debug("Version code for %s is now %s", self.provider.id, version)
        return version, changed

    @callback
    def request_version_refresh(self) -> None:
        """Schedule a version refresh, throttled.

        Called from the tile view when upstream answers 400, which is how an
        expired version code announces itself (docs/05 section 5). Throttled to
        VERSION_REFRESH_MIN_INTERVAL so a burst of 400s cannot become a burst of
        upstream calls (design decision D7).
        """
        now = time.monotonic()
        if (
            self._last_version_refresh is not None
            and now - self._last_version_refresh
            < VERSION_REFRESH_MIN_INTERVAL.total_seconds()
        ):
            return
        self._last_version_refresh = now
        self.hass.async_create_background_task(
            self.async_refresh_version(), f"{DOMAIN} refresh version"
        )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services.

    Services are registered here, not in ``async_setup_entry``, so automations
    can reference and validate them regardless of whether the entry is loaded
    (docs/02-HA-PLATFORM-2026.md section 4.4). The old ``apply`` / ``restore``
    services are gone: no file is ever patched, so the concept no longer exists.
    """

    def _runtime_or_raise() -> NaverMapRuntimeData:
        for entry in hass.config_entries.async_entries(DOMAIN):
            runtime: NaverMapRuntimeData | None = getattr(entry, "runtime_data", None)
            if runtime is not None:
                return runtime
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="entry_not_loaded"
        )

    async def _async_refresh_version(call: ServiceCall) -> dict[str, Any]:
        """Refresh the upstream version code now."""
        runtime = _runtime_or_raise()
        version, changed = await runtime.async_refresh_version()
        return {"version": version, "changed": changed}

    async def _async_clear_cache(call: ServiceCall) -> dict[str, Any]:
        """Empty the tile cache."""
        runtime = _runtime_or_raise()
        return {"evicted_bytes": runtime.cache.clear()}

    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH_VERSION,
        _async_refresh_version,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAR_CACHE,
        _async_clear_cache,
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: NaverMapConfigEntry) -> bool:
    """Set up a config entry."""
    options: dict[str, Any] = {**entry.data, **entry.options}
    provider_id = options.get(CONF_PROVIDER, DEFAULT_PROVIDER)
    if (provider := get_provider(provider_id)) is None:
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="unknown_provider",
            translation_placeholders={"provider": str(provider_id)},
        )

    cache = TileCache(
        max_bytes=int(options.get(CONF_CACHE_MAX_BYTES, CACHE_MAX_BYTES)),
        background=hass.async_create_background_task,
    )
    runtime = NaverMapRuntimeData(
        hass=hass,
        provider=provider,
        cache=cache,
        api_key=options.get(CONF_API_KEY),
        url_template=options.get(CONF_URL_TEMPLATE),
        attribution=options.get(CONF_ATTRIBUTION),
        dark_variant=bool(options.get(CONF_DARK_VARIANT, DEFAULT_DARK_VARIANT)),
        retina=bool(options.get(CONF_RETINA, DEFAULT_RETINA)),
    )
    entry.runtime_data = runtime

    await _async_register_once(hass)

    # The injected module, added per entry and removed again on unload.
    # docs/03 section 3.6 assumed add_extra_js_url had no counterpart, but
    # homeassistant.components.frontend.remove_extra_js_url does exist in
    # 2026.9.0 (verified in the installed source), and the frontend subscribes
    # to that list, so removal reaches open browsers without a restart. The
    # version query busts the browser cache when the integration updates.
    module_url = f"{FRONTEND_URL_PATH}/{FRONTEND_SCRIPT}?v={INTEGRATION_VERSION}"
    add_extra_js_url(hass, module_url)
    entry.async_on_unload(partial(remove_extra_js_url, hass, module_url))

    # First version fetch. A failure is not fatal: the style endpoint falls back
    # to osm and a repairs issue is raised (docs/03 section 3.2).
    await runtime.async_refresh_version()

    async def _async_scheduled_refresh(_now: datetime) -> None:
        await runtime.async_refresh_version()

    entry.async_on_unload(
        async_track_time_interval(
            hass,
            _async_scheduled_refresh,
            VERSION_REFRESH_INTERVAL,
            cancel_on_shutdown=True,
        )
    )
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_register_once(hass: HomeAssistant) -> None:
    """Register the static path and the views, once per process.

    Neither has a runtime counterpart to unregister with, and registering the
    views again on an entry reload would collide on the aiohttp routes, so both
    are guarded by a process-wide flag (design decision D5,
    docs/03-REDESIGN-SPEC.md section 3.6). The injected module is *not* handled
    here: it can be removed, so it is per entry.
    """
    if hass.data.get(DATA_REGISTERED):
        return
    hass.data[DATA_REGISTERED] = True

    # Serving our own folder read-only, through the modern static path API.
    # The single-path API that was removed in 2025.7 is never used
    # (docs/02-HA-PLATFORM-2026.md section 4.6).
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                FRONTEND_URL_PATH,
                str(Path(__file__).parent / "frontend"),
                # The keyword is cache_headers in 2026.9.0 (verified against
                # homeassistant/components/http/__init__.py); the spec's
                # should_cache does not exist. No cache headers, so an updated
                # module is picked up even without the ?v= query below.
                cache_headers=False,
            )
        ]
    )
    async_register_views(hass)


async def _async_update_listener(
    hass: HomeAssistant, entry: NaverMapConfigEntry
) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: NaverMapConfigEntry) -> bool:
    """Unload a config entry.

    The timer, the update listener, the injected module URL and the cache are
    released. The views and the static path stay until Home Assistant restarts,
    because there is no API to remove them (docs/03-REDESIGN-SPEC.md section
    3.6). This is not hidden: the views answer 503 while no entry is loaded, the
    style endpoint keeps answering with a valid fallback style, and removing the
    entry raises a repairs issue asking for a restart.
    """
    runtime: NaverMapRuntimeData | None = getattr(entry, "runtime_data", None)
    if runtime is not None:
        runtime.cache.clear()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: NaverMapConfigEntry) -> None:
    """Tell the user a restart is needed to fully undo the injection."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_RESTART_REQUIRED,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_RESTART_REQUIRED,
    )
