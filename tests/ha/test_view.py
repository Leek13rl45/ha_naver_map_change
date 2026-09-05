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

from upstream import (
    TILE_BODY,
    TILE_BODY_2X,
    TILE_BODY_DARK,
    TILE_BODY_DARK_2X,
    TILEJSON_URL,
    VERSION,
    tile_url,
    tile_url_2x,
    tile_url_dark,
    tile_url_dark_2x,
)

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
    # Upstream's Content-Type is passed through verbatim, whatever it is
    # (design decision D11, amended by D13: naver now serves image/png).
    assert response.headers["Content-Type"] == "image/png"
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
    for forbidden in (
        "pstatic.net",
        "vworld.kr",
        "api_key",
        VERSION,
        # Upstream implementation details, all server side only: the format
        # (D13), the layer selector (D14) and the @2x marker (D12).
        ".jpg",
        "mt=",
        "bg.ol.ts.ar.lko",
        "@2x",
    ):
        assert forbidden not in body


async def test_style_dark_variant(hass: HomeAssistant, client: Any) -> None:
    """Design decision D15, correcting finding F8.

    Naver does have a dark family (the dbasic style), so the dark style
    document must now point the route at it with ?variant=dark. Before D15 this
    test asserted the *light* template, because url_template_dark was None.
    """
    dark = await client.get("/api/map_tiles/naver_map_change/style/dark.json")
    assert dark.status == HTTPStatus.OK
    style = await dark.json()
    assert style["sources"]["basemap"]["tiles"] == [
        "/api/map_tiles/naver_map_change/{z}/{x}/{y}.png?variant=dark"
    ]
    # Still a plain raster source at the same geometry - only the bytes differ.
    assert style["sources"]["basemap"]["tileSize"] == 256
    assert style["sources"]["basemap"]["attribution"] == "© NAVER"


async def test_style_dark_at_dpr_two_combines_both_queries(
    hass: HomeAssistant, client: Any
) -> None:
    """?variant=dark and ?scale=2 have to coexist on one route.

    They join with "&" because the route is a single aiohttp registration and
    core's withMapTilesToken() appends its token onto whatever query is already
    there (docs/02 section 3.3).
    """
    dark = await client.get("/api/map_tiles/naver_map_change/style/dark.json?dpr=2")
    assert dark.status == HTTPStatus.OK
    style = await dark.json()
    assert style["sources"]["basemap"]["tiles"] == [
        "/api/map_tiles/naver_map_change/{z}/{x}/{y}.png?variant=dark&scale=2"
    ]
    # The tileSize invariant holds for the dark family too (D12).
    assert style["sources"]["basemap"]["tileSize"] == 256


async def test_style_dark_with_the_dark_option_off_uses_light_tiles(
    hass: HomeAssistant,
    hass_client_no_auth: Any,
    mock_upstream: Any,
    config_entry: MockConfigEntry,
) -> None:
    """CONF_DARK_VARIANT keeps its veto now that naver has real dark tiles."""
    from custom_components.naver_map_change.const import CONF_DARK_VARIANT

    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_DARK_VARIANT: False}
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_client_no_auth()

    # The runtime refuses the dark template, so the tile route serves light.
    response = await client.get(
        f"{TILE_PATH}?variant=dark&token={_token(hass)}"
    )
    assert response.status == HTTPStatus.OK
    assert await response.read() == TILE_BODY


async def test_dark_tile_route_fetches_the_dbasic_family(
    hass: HomeAssistant, client: Any, mock_upstream: Any
) -> None:
    """D15: ?variant=dark reaches upstream as the dbasic style."""
    response = await client.get(f"{TILE_PATH}?variant=dark&token={_token(hass)}")
    assert response.status == HTTPStatus.OK
    assert await response.read() == TILE_BODY_DARK
    requested = [str(call[1]) for call in mock_upstream.mock_calls]
    assert tile_url_dark() in requested
    assert tile_url() not in requested


async def test_dark_and_scale_two_select_the_dark_retina_template(
    hass: HomeAssistant, client: Any, mock_upstream: Any
) -> None:
    """The two query parameters combine rather than one overriding the other.

    This is the D12 x D15 intersection: dark picks the family, scale picks the
    resolution, and the result must be the dbasic @2x template - not the light
    @2x one and not the dark 1x one.
    """
    response = await client.get(
        f"{TILE_PATH}?variant=dark&scale=2&token={_token(hass)}"
    )
    assert response.status == HTTPStatus.OK
    assert await response.read() == TILE_BODY_DARK_2X
    requested = [str(call[1]) for call in mock_upstream.mock_calls]
    assert tile_url_dark_2x() in requested
    for other in (tile_url(), tile_url_2x(), tile_url_dark()):
        assert other not in requested


async def test_all_four_variants_are_cached_separately(
    hass: HomeAssistant, client: Any, config_entry: MockConfigEntry
) -> None:
    """variant and scale are both in the cache key, so none of the four mix."""
    token = _token(hass)
    expected = {
        "": TILE_BODY,
        "?scale=2": TILE_BODY_2X,
        "?variant=dark": TILE_BODY_DARK,
        "?variant=dark&scale=2": TILE_BODY_DARK_2X,
    }
    for query, body in expected.items():
        joiner = "&" if query else "?"
        response = await client.get(f"{TILE_PATH}{query}{joiner}token={token}")
        assert await response.read() == body, query
    assert len(config_entry.runtime_data.cache) == 4

    # A second pass must hit each entry's own body, never a neighbour's.
    for query, body in expected.items():
        joiner = "&" if query else "?"
        response = await client.get(f"{TILE_PATH}{query}{joiner}token={token}")
        assert await response.read() == body, query
    assert len(config_entry.runtime_data.cache) == 4


async def test_ac6_dark_style_leaks_no_upstream_detail(
    hass: HomeAssistant, client: Any
) -> None:
    """AC6 regression: the dark style name must not reach the browser either."""
    response = await client.get(
        "/api/map_tiles/naver_map_change/style/dark.json?dpr=2"
    )
    body = await response.text()
    for forbidden in (
        "dbasic",
        "pstatic.net",
        "vworld.kr",
        "api_key",
        VERSION,
        "mt=",
        "bg.ol.ts.ar.lko",
        "@2x",
        ".jpg",
    ):
        assert forbidden not in body


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


TILES = "sources", "basemap", "tiles"


def _tiles(style: dict) -> list[str]:
    """Return the tile templates of a style document."""
    return style["sources"]["basemap"]["tiles"]


async def test_style_at_dpr_two_asks_for_scale_two_and_keeps_tile_size_256(
    hass: HomeAssistant, client: Any
) -> None:
    """★ Design decision D12, the whole point of the change.

    ``tileSize`` stays 256 while the tile URL carries ``scale=2``: in MapLibre
    that pair means "a 512px image covering a 256px logical tile", which is how
    a raster basemap renders sharply on a Retina display. Bumping tileSize to
    512 instead would shift the zoom pyramid and misplace the map.
    """
    response = await client.get(f"{STYLE_PATH}?dpr=2")
    assert response.status == HTTPStatus.OK
    style = await response.json()
    assert _tiles(style) == [
        "/api/map_tiles/naver_map_change/{z}/{x}/{y}.png?scale=2"
    ]
    assert style["sources"]["basemap"]["tileSize"] == 256


async def test_style_at_dpr_one_asks_for_no_scale(
    hass: HomeAssistant, client: Any
) -> None:
    """A 1x display gets the cheap tiles and an unchanged template."""
    response = await client.get(f"{STYLE_PATH}?dpr=1")
    assert response.status == HTTPStatus.OK
    assert _tiles(await response.json()) == [
        "/api/map_tiles/naver_map_change/{z}/{x}/{y}.png"
    ]


async def test_style_without_a_dpr_parameter_asks_for_no_scale(
    hass: HomeAssistant, client: Any
) -> None:
    """An older cached copy of the injected module sends no dpr at all."""
    assert _tiles(await (await client.get(STYLE_PATH)).json()) == [
        "/api/map_tiles/naver_map_change/{z}/{x}/{y}.png"
    ]


async def test_unparseable_dpr_is_treated_as_one_and_never_fails(
    hass: HomeAssistant, client: Any
) -> None:
    """The style endpoint is not allowed to fail (design decision D10)."""
    for raw in ("abc", "", "nan", "inf", "-2", "0", "2,0", "2px", "[]"):
        response = await client.get(f"{STYLE_PATH}?dpr={raw}")
        assert response.status == HTTPStatus.OK, raw
        assert _tiles(await response.json()) == [
            "/api/map_tiles/naver_map_change/{z}/{x}/{y}.png"
        ], raw


async def test_fractional_dpr_is_decided_on_the_server(
    hass: HomeAssistant, client: Any
) -> None:
    """The module forwards 1.5/2.625 verbatim; the threshold lives here."""
    below = await client.get(f"{STYLE_PATH}?dpr=1.5")
    assert _tiles(await below.json()) == [
        "/api/map_tiles/naver_map_change/{z}/{x}/{y}.png"
    ]
    above = await client.get(f"{STYLE_PATH}?dpr=2.625")
    assert _tiles(await above.json()) == [
        "/api/map_tiles/naver_map_change/{z}/{x}/{y}.png?scale=2"
    ]


async def test_style_at_dpr_two_with_retina_disabled_asks_for_no_scale(
    hass: HomeAssistant,
    hass_client_no_auth: Any,
    mock_upstream: Any,
    config_entry: MockConfigEntry,
) -> None:
    """The option is a veto: off means 1x tiles even on a Retina display."""
    from custom_components.naver_map_change.const import CONF_RETINA

    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, options={CONF_RETINA: False})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_client_no_auth()

    response = await client.get(f"{STYLE_PATH}?dpr=2")
    assert response.status == HTTPStatus.OK
    style = await response.json()
    assert _tiles(style) == ["/api/map_tiles/naver_map_change/{z}/{x}/{y}.png"]
    assert style["sources"]["basemap"]["tileSize"] == 256


async def test_style_at_dpr_two_on_osm_asks_for_no_scale(
    hass: HomeAssistant,
    hass_client_no_auth: Any,
    aioclient_mock: Any,
    osm_config_entry: MockConfigEntry,
) -> None:
    """osm has no measured @2x variant, so nothing is requested (docs/02 3.2)."""
    aioclient_mock.get(
        "https://tile.openstreetmap.org/12/3492/1586.png",
        content=TILE_BODY,
        headers={"Content-Type": "image/png"},
    )
    osm_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(osm_config_entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_client_no_auth()

    response = await client.get(f"{STYLE_PATH}?dpr=2")
    assert response.status == HTTPStatus.OK
    style = await response.json()
    assert _tiles(style) == ["/api/map_tiles/naver_map_change/{z}/{x}/{y}.png"]
    assert style["sources"]["basemap"]["attribution"] == "© OpenStreetMap contributors"


async def test_ac6_style_at_dpr_two_still_leaks_nothing(
    hass: HomeAssistant, client: Any
) -> None:
    """AC6 regression guard: the @2x switch must not leak upstream detail."""
    response = await client.get(f"{STYLE_PATH}?dpr=2")
    body = await response.text()
    for forbidden in (
        "pstatic.net",
        "vworld.kr",
        "api_key",
        VERSION,
        "@2x",
        ".jpg",
        "mt=",
        "bg.ol.ts.ar.lko",
    ):
        assert forbidden not in body


async def test_tile_route_at_scale_two_fetches_the_at2x_upstream_url(
    hass: HomeAssistant, client: Any, mock_upstream: Any
) -> None:
    """``?scale=2`` reaches upstream as the @2x path (docs/05 section 3)."""
    response = await client.get(f"{TILE_PATH}?scale=2&token={_token(hass)}")
    assert response.status == HTTPStatus.OK
    assert await response.read() == TILE_BODY_2X
    assert response.headers["Content-Type"] == "image/png"
    requested = [str(call[1]) for call in mock_upstream.mock_calls]
    assert tile_url_2x() in requested
    assert tile_url() not in requested


async def test_scale_one_and_scale_two_are_cached_separately(
    hass: HomeAssistant, client: Any, config_entry: MockConfigEntry
) -> None:
    """The cache key carries the scale (D12), so the bodies cannot mix."""
    token = _token(hass)
    one = await client.get(f"{TILE_PATH}?token={token}")
    two = await client.get(f"{TILE_PATH}?scale=2&token={token}")
    assert await one.read() == TILE_BODY
    assert await two.read() == TILE_BODY_2X
    assert len(config_entry.runtime_data.cache) == 2

    # And a repeat of each is served from its own entry, not the other's.
    assert await (await client.get(f"{TILE_PATH}?token={token}")).read() == TILE_BODY
    assert (
        await (await client.get(f"{TILE_PATH}?scale=2&token={token}")).read()
        == TILE_BODY_2X
    )


async def test_only_the_literal_string_two_means_retina(
    hass: HomeAssistant, client: Any, mock_upstream: Any
) -> None:
    """Strict parsing, and no exception path: everything else is scale 1."""
    token = _token(hass)
    for raw in ("1", "2.0", "02", " 2", "abc", "", "3", "-2", "true"):
        response = await client.get(f"{TILE_PATH}?scale={raw}&token={token}")
        assert response.status == HTTPStatus.OK, raw
        assert await response.read() == TILE_BODY, raw


async def test_scale_two_on_a_provider_without_at2x_falls_back_not_404(
    hass: HomeAssistant,
    hass_client_no_auth: Any,
    aioclient_mock: Any,
    osm_config_entry: MockConfigEntry,
) -> None:
    """Hard constraint 3: an unsupported scale degrades, it does not fail."""
    aioclient_mock.get(
        "https://tile.openstreetmap.org/12/3492/1586.png",
        content=TILE_BODY,
        headers={"Content-Type": "image/png"},
    )
    osm_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(osm_config_entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_client_no_auth()

    response = await client.get(f"{TILE_PATH}?scale=2&token={_token(hass)}")
    assert response.status == HTTPStatus.OK
    assert await response.read() == TILE_BODY
    requested = [str(call[1]) for call in aioclient_mock.mock_calls]
    assert "https://tile.openstreetmap.org/12/3492/1586.png" in requested
    assert not any("@2x" in url for url in requested)


async def test_scale_and_variant_and_token_coexist_on_one_route(
    hass: HomeAssistant, client: Any
) -> None:
    """Why a query parameter and not a path segment (docs/02 section 3.3).

    withMapTilesToken() appends its token onto the existing query string, so
    ``?variant=dark&scale=2&token=...`` all have to survive together.
    """
    response = await client.get(
        f"{TILE_PATH}?variant=dark&scale=2&token={_token(hass)}"
    )
    assert response.status == HTTPStatus.OK
    # Since D15 corrected F8, this is the *dark* @2x body, not the light one.
    assert await response.read() == TILE_BODY_DARK_2X


async def test_injected_module_is_served(hass: HomeAssistant, client: Any) -> None:
    """The static path serves the frontend module (docs/02 4.8)."""
    response = await client.get("/naver_map_change_frontend/naver-basemap.js")
    assert response.status == HTTPStatus.OK
    body = await response.text()
    assert "__naverMapChangePatched" in body
    assert "/api/map_tiles/naver_map_change/style/light.json" in body
    # Design decision D12: the module forwards devicePixelRatio, which is the
    # one client-side fact the server cannot know for itself.
    assert "devicePixelRatio" in body
    assert "?dpr=" in body
