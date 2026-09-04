"""Config and options flow (AC1)."""

# ruff: noqa: I001 - sys.path has to be set up before these imports.

from __future__ import annotations

import json
from typing import Any

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.naver_map_change.const import (
    CONF_API_KEY,
    CONF_PROVIDER,
    CONF_URL_TEMPLATE,
    DOMAIN,
)

from upstream import TILEJSON_BODY, TILEJSON_URL, tile_url


async def test_user_flow_naver(
    hass: HomeAssistant, mock_upstream: Any
) -> None:
    """The default provider needs no further input."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PROVIDER: "naver"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_PROVIDER: "naver"}


async def test_single_instance(hass: HomeAssistant, mock_upstream: Any) -> None:
    """single_config_entry blocks a second entry."""
    MockConfigEntry(domain=DOMAIN, data={CONF_PROVIDER: "naver"}).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_vworld_rejects_an_unregistered_key(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """A 200 with an XML body is a failure, not a success (docs/05 7.2)."""
    aioclient_mock.get(
        "https://api.vworld.kr/req/wmts/1.0.0/BADKEY/Base/12/1586/3492.png",
        text="<ExceptionReport>등록되지 않은 인증키입니다.</ExceptionReport>",
        headers={"Content-Type": "text/xml"},
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PROVIDER: "vworld"}
    )
    assert result["step_id"] == "provider"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "BADKEY"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "not_an_image"}


async def test_vworld_accepts_a_working_key(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """A real image is a pass, and the key is stored server side only."""
    aioclient_mock.get(
        "https://api.vworld.kr/req/wmts/1.0.0/GOODKEY/Base/12/1586/3492.png",
        content=b"\x89PNG\r\n",
        headers={"Content-Type": "image/png"},
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PROVIDER: "vworld"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "GOODKEY"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_PROVIDER: "vworld", CONF_API_KEY: "GOODKEY"}


async def test_custom_template_is_validated(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """A template without the placeholders is rejected."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PROVIDER: "custom"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_URL_TEMPLATE: "https://example.org/{z}/{x}/{y}.png?k={api_key}"},
    )
    assert result["errors"] == {"base": "invalid_url_template"}


async def test_naver_version_failure_is_a_retryable_error(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Without a version code there is no tile URL, so the flow says so."""
    aioclient_mock.get(TILEJSON_URL, status=500)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PROVIDER: "naver"}
    )
    # naver needs neither key nor template, so the connection test runs on the
    # first step and its failure has to surface there - as an error the user can
    # retry, not as an abort.
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "version_unavailable"}


async def test_options_flow_switches_provider(
    hass: HomeAssistant, mock_upstream: Any, config_entry: MockConfigEntry
) -> None:
    """AC8's control group is reachable from the UI."""
    aioclient_mock = mock_upstream
    aioclient_mock.get(
        "https://tile.openstreetmap.org/12/3492/1586.png",
        content=b"\x89PNG\r\n",
        headers={"Content-Type": "image/png"},
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_PROVIDER: "osm",
            CONF_API_KEY: "",
            CONF_URL_TEMPLATE: "",
            "attribution": "",
            "dark_variant": True,
            "cache_max_bytes": 32 * 1024 * 1024,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert config_entry.runtime_data.provider.id == "osm"


async def test_unsupported_ha_version_aborts(
    hass: HomeAssistant, monkeypatch: Any
) -> None:
    """Below 2026.9.0 this design does not apply (docs/02 section 3)."""
    from custom_components.naver_map_change import config_flow

    monkeypatch.setattr(config_flow, "_ha_version_supported", lambda: False)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unsupported_ha_version"


def test_tilejson_fixture_matches_the_measurement() -> None:
    """Guard against the fixture drifting from docs/05 section 1."""
    assert json.loads(json.dumps(TILEJSON_BODY))["version"] == "1787907321"
    assert tile_url().endswith("/12/3492/1586.jpg")
