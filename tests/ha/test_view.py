"""The tile proxy and the style endpoint, over real HTTP.

Covers AC5 (no token, no tile) and AC6 (no secret in the style document) as
executed requests rather than as unit-level assertions.
"""

# ruff: noqa: I001 - sys.path has to be set up before these imports.

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.naver_map_change.const import (
    CORE_MAP_TILES_DATA_KEY,
    DOMAIN,
)

from upstream import TILE_BODY, TILEJSON_URL, VERSION, tile_url

TILE_PATH = "/api/map_tiles/naver_map_change/12/3492/1586.png"
STYLE_PATH = "/api/map_tiles/naver_map_change/style/light.json"


@pytest.fixture
async def client(
    hass: HomeAssistant,
    hass_client_no_auth: Any,
    mock_upstream: Any,
    config_entry: MockConfigEntry,
) -> Any:
    """Set the entry up and return an unauthenticated HTTP client."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return await hass_client_no_auth()


def _token(hass: HomeAssistant) -> str:
    """Return a currently valid core map_tiles token."""
    return hass.data[CORE_MAP_TILES_DATA_KEY][-1]


async def test_ac5_tile_without_a_token_is_forbidden(
    hass: HomeAssistant, client: Any
) -> None:
    """AC5: no token, no tile."""
    response = await client.get(TILE_PATH)
    assert response.status == HTTPStatus.FORBIDDEN


async def test_ac5_tile_with_a_valid_token_is_served(
    hass: HomeAssistant, client: Any
) -> None:
    """AC5: a core rotating token is accepted, upstream Content-Type is kept."""
    response = await client.get(f"{TILE_PATH}?token={_token(hass)}")
    assert response.status == HTTPStatus.OK
    assert await response.read() == TILE_BODY
    # The route says .png, the bytes are JPEG - see design decision D11.
    assert response.headers["Content-Type"] == "image/jpeg"
    assert response.headers["Cache-Control"] == "private, max-age=604800"
    # Only whitelisted headers are passed through (design decision D7).
    assert "Set-Cookie" not in response.headers
    assert "ETag" not in response.headers


async def test_expired_token_is_forbidden_not_unauthorized(
    hass: HomeAssistant, client: Any
) -> None:
    """403, so the ban middleware does not count the user's own dashboard."""
    response = await client.get(f"{TILE_PATH}?token=deadbeef")
    assert response.status == HTTPStatus.FORBIDDEN


async def test_out_of_range_coordinates_are_404_without_an_upstream_call(
    hass: HomeAssistant, client: Any, mock_upstream: Any
) -> None:
    """Upstream would answer 200 with a blank tile, so we filter (docs/05 4)."""
    before = len(mock_upstream.mock_calls)
    response = await client.get(
        f"/api/map_tiles/naver_map_change/12/99999/99999.png?token={_token(hass)}"
    )
    assert response.status == HTTPStatus.NOT_FOUND
    assert len(mock_upstream.mock_calls) == before


async def test_zoom_above_the_provider_maximum_is_404(
    hass: HomeAssistant, client: Any
) -> None:
    """Naver declares maxzoom 21, so z22 never reaches upstream."""
    response = await client.get(
        f"/api/map_tiles/naver_map_change/22/0/0.png?token={_token(hass)}"
    )
    assert response.status == HTTPStatus.NOT_FOUND


async def test_non_numeric_coordinates_do_not_match_the_route(
    hass: HomeAssistant, client: Any
) -> None:
    """The route regex mirrors core's (requirement R1)."""
    response = await client.get(
        f"/api/map_tiles/naver_map_change/abc/0/0.png?token={_token(hass)}"
    )
    assert response.status == HTTPStatus.NOT_FOUND


async def test_cached_tile_is_served_without_a_second_upstream_call(
    hass: HomeAssistant, client: Any, mock_upstream: Any
) -> None:
    """The cache is what keeps upstream traffic low (docs/04 section 3)."""
    token = _token(hass)
    assert (await client.get(f"{TILE_PATH}?token={token}")).status == HTTPStatus.OK
    calls = len(mock_upstream.mock_calls)
    assert (await client.get(f"{TILE_PATH}?token={token}")).status == HTTPStatus.OK
    assert len(mock_upstream.mock_calls) == calls


async def test_non_image_200_is_502_and_not_cached(
    hass: HomeAssistant,
    hass_client_no_auth: Any,
    aioclient_mock: Any,
    config_entry: MockConfigEntry,
) -> None:
    """VWorld answers an invalid key with 200 and XML (docs/05 7.2, D7)."""
    import json

    from upstream import TILEJSON_BODY

    aioclient_mock.get(
        TILEJSON_URL,
        text=json.dumps(TILEJSON_BODY),
        headers={"Content-Type": "application/json"},
    )
    aioclient_mock.get(
        tile_url(),
        text="<ExceptionReport>등록되지 않은 인증키입니다.</ExceptionReport>",
        headers={"Content-Type": "text/xml", "Set-Cookie": "JSESSIONID=abc"},
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_client_no_auth()

    response = await client.get(f"{TILE_PATH}?token={_token(hass)}")
    assert response.status == HTTPStatus.BAD_GATEWAY
    assert len(config_entry.runtime_data.cache) == 0


async def test_upstream_400_triggers_a_version_refresh(
    hass: HomeAssistant,
    hass_client_no_auth: Any,
    aioclient_mock: Any,
    config_entry: MockConfigEntry,
) -> None:
    """An expired version code announces itself as 400 (docs/05 5, D7)."""
    import json

    from upstream import NEW_VERSION, TILEJSON_BODY

    aioclient_mock.get(
        TILEJSON_URL,
        text=json.dumps(TILEJSON_BODY),
        headers={"Content-Type": "application/json"},
    )
    aioclient_mock.get(tile_url(), status=400, content=b"")
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_client_no_auth()
    assert config_entry.runtime_data.version == VERSION

    # The throttle window starts at the setup refresh, so allow the next one.
    config_entry.runtime_data._last_version_refresh = None

    aioclient_mock.clear_requests()
    aioclient_mock.get(
        TILEJSON_URL,
        json={**TILEJSON_BODY, "version": NEW_VERSION},
        headers={"Content-Type": "application/json"},
    )
    aioclient_mock.get(tile_url(), status=400, content=b"")

    response = await client.get(f"{TILE_PATH}?token={_token(hass)}")
    assert response.status == HTTPStatus.BAD_GATEWAY
    await hass.async_block_till_done()
    assert config_entry.runtime_data.version == NEW_VERSION


async def test_version_refresh_from_tiles_is_throttled(
    hass: HomeAssistant, client: Any, config_entry: MockConfigEntry
) -> None:
    """A burst of 400s must not become a burst of upstream calls (D7)."""
    runtime = config_entry.runtime_data
    runtime.request_version_refresh()
    first = runtime._last_version_refresh
    runtime.request_version_refresh()
    assert runtime._last_version_refresh == first
    await hass.async_block_till_done()


async def test_tile_without_a_loaded_entry_is_503(
    hass: HomeAssistant, client: Any, config_entry: MockConfigEntry
) -> None:
    """The view outlives its entry, so it says so honestly (design D4)."""
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    response = await client.get(f"{TILE_PATH}?token={_token(hass)}")
    assert response.status == HTTPStatus.SERVICE_UNAVAILABLE


async def test_missing_token_store_is_forbidden(
    hass: HomeAssistant, client: Any
) -> None:
    """Never become an open proxy if core changes (docs/03 6.3)."""
    from homeassistant.helpers import issue_registry as ir

    from custom_components.naver_map_change.const import ISSUE_MISSING_TOKEN_STORE

    token = _token(hass)
    hass.data.pop(CORE_MAP_TILES_DATA_KEY)
    response = await client.get(f"{TILE_PATH}?token={token}")
    assert response.status == HTTPStatus.FORBIDDEN
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_MISSING_TOKEN_STORE)


async def test_ac6_style_document_has_no_secret_and_needs_no_token(
    hass: HomeAssistant, client: Any
) -> None:
    """AC6: the style endpoint is safe to serve unauthenticated."""
    response = await client.get(STYLE_PATH)
    assert response.status == HTTPStatus.OK
    style = await response.json()

    assert style["version"] == 8
    source = style["sources"]["basemap"]
    assert source["tiles"] == ["/api/map_tiles/naver_map_change/{z}/{x}/{y}.png"]
    assert source["attribution"] == "© NAVER"
    assert source["tileSize"] == 256
    assert "sprite" not in style
    assert "glyphs" not in style

    body = await response.text()
    for forbidden in ("pstatic.net", "vworld.kr", "api_key", VERSION):
        assert forbidden not in body


async def test_style_dark_variant(hass: HomeAssistant, client: Any) -> None:
    """Naver has no dark tiles, so dark returns the same tile template."""
    dark = await client.get("/api/map_tiles/naver_map_change/style/dark.json")
    assert dark.status == HTTPStatus.OK
    assert (await dark.json())["sources"]["basemap"]["tiles"] == [
        "/api/map_tiles/naver_map_change/{z}/{x}/{y}.png"
    ]


async def test_unknown_style_variant_is_404(hass: HomeAssistant, client: Any) -> None:
    """Only light and dark exist."""
    response = await client.get("/api/map_tiles/naver_map_change/style/pink.json")
    assert response.status == HTTPStatus.NOT_FOUND


async def test_style_falls_back_to_osm_when_no_version_is_known(
    hass: HomeAssistant,
    hass_client_no_auth: Any,
    aioclient_mock: Any,
    config_entry: MockConfigEntry,
) -> None:
    """Design D10: the style endpoint always answers with a usable style."""
    aioclient_mock.get(TILEJSON_URL, status=500)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_client_no_auth()

    response = await client.get(STYLE_PATH)
    assert response.status == HTTPStatus.OK
    style = await response.json()
    assert style["sources"]["basemap"]["attribution"] == "© OpenStreetMap contributors"
    assert style["sources"]["basemap"]["tiles"] == [
        "/api/map_tiles/naver_map_change/{z}/{x}/{y}.png"
    ]


async def test_style_without_a_loaded_entry_is_still_a_valid_style(
    hass: HomeAssistant, client: Any, config_entry: MockConfigEntry
) -> None:
    """A blank map is never an acceptable failure (docs/03 section 0.3)."""
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    response = await client.get(STYLE_PATH)
    assert response.status == HTTPStatus.OK
    style = await response.json()
    assert style["version"] == 8
    assert style["layers"][0]["type"] == "raster"


async def test_injected_module_is_served(hass: HomeAssistant, client: Any) -> None:
    """The static path serves the frontend module (docs/02 4.8)."""
    response = await client.get("/naver_map_change_frontend/naver-basemap.js")
    assert response.status == HTTPStatus.OK
    body = await response.text()
    assert "__naverMapChangePatched" in body
    assert "/api/map_tiles/naver_map_change/style/light.json" in body
