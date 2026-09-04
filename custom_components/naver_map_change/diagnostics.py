"""Diagnostics for the Naver Map Change integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_API_KEY

if TYPE_CHECKING:
    from . import NaverMapConfigEntry, NaverMapRuntimeData

TO_REDACT = {CONF_API_KEY}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NaverMapConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    The API key is redacted; the upstream URL template is reported as the
    provider template, never with a key substituted into it.
    """
    runtime: NaverMapRuntimeData | None = getattr(entry, "runtime_data", None)
    diagnostics: dict[str, Any] = {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
    }
    if runtime is None:
        diagnostics["runtime"] = None
        return diagnostics

    diagnostics["runtime"] = {
        "provider": runtime.provider.id,
        "provider_unverified": runtime.provider.unverified,
        "url_template": runtime.provider.url_template,
        "has_dark_template": runtime.dark_template is not None,
        # Both facts are needed to explain a "still blurry" report: whether the
        # user allows @2x, and whether this provider has one at all (D12).
        "retina_enabled": runtime.retina,
        "has_retina_template": runtime.provider.url_template_retina is not None,
        "version": runtime.version,
        "version_meta_url": runtime.provider.version_meta_url,
        "can_build_tile_urls": runtime.can_build_tile_urls(),
        "tile_size": runtime.provider.tile_size,
        "zoom": {
            "min": runtime.provider.min_zoom,
            "max": runtime.provider.max_zoom,
            "max_native": runtime.provider.max_native_zoom,
        },
        "attribution": runtime.attribution or runtime.provider.attribution,
        "cache": runtime.cache.stats(),
        "ha_version": runtime.ha_version,
    }
    return diagnostics
