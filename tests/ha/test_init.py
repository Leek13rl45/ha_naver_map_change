"""Setup, services and unload, against a real Home Assistant instance."""

# ruff: noqa: I001 - sys.path has to be set up before these imports.

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.naver_map_change.const import (
    DATA_REGISTERED,
    DOMAIN,
    FRONTEND_SCRIPT,
    SERVICE_CLEAR_CACHE,
    SERVICE_REFRESH_VERSION,
)

from upstream import NEW_VERSION, TILEJSON_BODY, TILEJSON_URL, VERSION


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_entry(
    hass: HomeAssistant, mock_upstream: Any, config_entry: MockConfigEntry
) -> None:
    """The entry loads, fetches a version code and registers everything once."""
    await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    runtime = config_entry.runtime_data
    assert runtime.provider.id == "naver"
    assert runtime.version == VERSION
    assert runtime.can_build_tile_urls()
    assert hass.data[DATA_REGISTERED] is True

    # The injected module is announced to the frontend.
    from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL

    assert any(
        FRONTEND_SCRIPT in url for url in hass.data[DATA_EXTRA_MODULE_URL].urls
    )


async def test_services_are_registered_without_an_entry(hass: HomeAssistant) -> None:
    """Services exist as soon as the component is set up (docs/02 4.4)."""
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, DOMAIN, {})
    assert hass.services.has_service(DOMAIN, SERVICE_REFRESH_VERSION)
    assert hass.services.has_service(DOMAIN, SERVICE_CLEAR_CACHE)

    # And they refuse to run while no entry is loaded.
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, SERVICE_REFRESH_VERSION, blocking=True, return_response=True
        )


async def test_refresh_version_service(
    hass: HomeAssistant, mock_upstream: Any, config_entry: MockConfigEntry
) -> None:
    """refresh_version reports the version and whether it changed."""
    await _setup(hass, config_entry)

    response = await hass.services.async_call(
        DOMAIN, SERVICE_REFRESH_VERSION, blocking=True, return_response=True
    )
    assert response == {"version": VERSION, "changed": False}

    # Upstream rotates the code; the service reports the change.
    mock_upstream.clear_requests()
    mock_upstream.get(
        TILEJSON_URL,
        json={**TILEJSON_BODY, "version": NEW_VERSION},
        headers={"Content-Type": "application/json"},
    )
    response = await hass.services.async_call(
        DOMAIN, SERVICE_REFRESH_VERSION, blocking=True, return_response=True
    )
    assert response == {"version": NEW_VERSION, "changed": True}
    assert config_entry.runtime_data.version == NEW_VERSION


async def test_clear_cache_service(
    hass: HomeAssistant, mock_upstream: Any, config_entry: MockConfigEntry
) -> None:
    """clear_cache reports the freed bytes."""
    await _setup(hass, config_entry)
    cache = config_entry.runtime_data.cache

    from custom_components.naver_map_change.cache import TileAsset

    cache.store(
        ("naver", VERSION, "light", 12, 3492, 1586),
        TileAsset(body=b"x" * 500, content_type="image/jpeg"),
    )
    freed = cache.total_bytes
    assert freed > 500

    response = await hass.services.async_call(
        DOMAIN, SERVICE_CLEAR_CACHE, blocking=True, return_response=True
    )
    assert response == {"evicted_bytes": freed}
    assert len(cache) == 0


async def test_version_fetch_failure_is_not_fatal(
    hass: HomeAssistant, aioclient_mock: Any, config_entry: MockConfigEntry
) -> None:
    """A failed version fetch loads the entry anyway and raises an issue."""
    from homeassistant.helpers import issue_registry as ir

    from custom_components.naver_map_change.const import ISSUE_VERSION_UNAVAILABLE

    aioclient_mock.get(TILEJSON_URL, status=500)
    await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    assert config_entry.runtime_data.version is None
    assert not config_entry.runtime_data.can_build_tile_urls()
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_VERSION_UNAVAILABLE)


async def test_unload_keeps_the_process_registration(
    hass: HomeAssistant, mock_upstream: Any, config_entry: MockConfigEntry
) -> None:
    """Unloading clears the cache; views stay until a restart (docs/03 3.6)."""
    await _setup(hass, config_entry)
    cache = config_entry.runtime_data.cache

    from custom_components.naver_map_change.cache import TileAsset

    cache.store(
        ("naver", VERSION, "light", 1, 0, 0),
        TileAsset(body=b"x" * 10, content_type="image/jpeg"),
    )

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.NOT_LOADED
    assert len(cache) == 0
    # The views and the static path stay: there is no API to undo those.
    assert hass.data[DATA_REGISTERED] is True
    # The injected module does have a removal API, so it goes away.
    from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL

    assert not any(
        FRONTEND_SCRIPT in url for url in hass.data[DATA_EXTRA_MODULE_URL].urls
    )


async def test_reload_does_not_register_twice(
    hass: HomeAssistant, mock_upstream: Any, config_entry: MockConfigEntry
) -> None:
    """A reload must not collide on the aiohttp routes (design D5)."""
    await _setup(hass, config_entry)
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED


async def test_unknown_provider_fails_setup(hass: HomeAssistant) -> None:
    """A stored provider that no longer exists is an error, not a crash."""
    entry = MockConfigEntry(domain=DOMAIN, data={"provider": "does_not_exist"})
    entry.add_to_hass(entry.hass if hasattr(entry, "hass") else hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_remove_entry_asks_for_a_restart(
    hass: HomeAssistant, mock_upstream: Any, config_entry: MockConfigEntry
) -> None:
    """Removing the entry raises a repairs issue (docs/03 3.6)."""
    from homeassistant.helpers import issue_registry as ir

    from custom_components.naver_map_change.const import ISSUE_RESTART_REQUIRED

    await _setup(hass, config_entry)
    assert await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_RESTART_REQUIRED)


async def test_diagnostics_redacts_the_api_key(hass: HomeAssistant) -> None:
    """The API key never appears in diagnostics."""
    from custom_components.naver_map_change.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    entry = MockConfigEntry(
        domain=DOMAIN, data={"provider": "vworld", "api_key": "SECRET_KEY_VALUE"}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert "SECRET_KEY_VALUE" not in str(diagnostics)
    assert diagnostics["runtime"]["provider"] == "vworld"
    assert diagnostics["runtime"]["provider_unverified"] is True
