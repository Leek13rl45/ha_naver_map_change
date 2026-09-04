"""Tests for providers.py - pure logic, no Home Assistant needed.

Runs under both ``pytest`` and ``python -m unittest``: every test is a
unittest.TestCase method, and the async ones are wrapped in ``asyncio.run`` so
no pytest-asyncio plugin is required.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _bootstrap import const, providers  # noqa: E402

NAVER = providers.PROVIDERS["naver"]
OSM = providers.PROVIDERS["osm"]
VWORLD = providers.PROVIDERS["vworld"]
CUSTOM = providers.PROVIDERS["custom"]

# The document upstream actually returned, verbatim from
# docs/05-UPSTREAM-FINDINGS.md section 1.
TILEJSON_SAMPLE = json.dumps(
    {
        "tilejson": "2.1.0",
        "name": "",
        "attribution": "",
        "scheme": "xyz",
        "minzoom": 0,
        "maxzoom": 21,
        "version": "1787907321",
        "format": "jpg",
        "tiles": [
            "https://map.pstatic.net/nrb/styles/basic/1787907321/{z}/{x}/{y}.jpg"
        ],
    }
)


class FakeResponse:
    """Minimal async context manager standing in for an aiohttp response."""

    def __init__(self, status: int = 200, payload: bytes = b"") -> None:
        self.status = status
        self._payload = payload

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def read(self) -> bytes:
        return self._payload


class FakeSession:
    """Minimal stand-in for aiohttp.ClientSession."""

    def __init__(self, response: object = None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs: object) -> object:
        self.calls.append((url, dict(kwargs)))
        if self._error is not None:
            raise self._error
        return self._response


class TestProviderRegistry(unittest.TestCase):
    """The registry itself."""

    def test_expected_providers_registered(self) -> None:
        self.assertEqual(
            set(providers.PROVIDERS), {"naver", "osm", "vworld", "custom"}
        )

    def test_naver_constants_match_measurements(self) -> None:
        # docs/05-UPSTREAM-FINDINGS.md sections 3 and 8.
        # .png, not .jpg (design decision D13), and with the mt layer selector
        # (design decision D14). Both measured, neither guessed.
        self.assertEqual(
            NAVER.url_template,
            "https://map.pstatic.net/nrb/styles/basic/{version}/{z}/{x}/{y}"
            ".png?mt=bg.ol.ts.ar.lko",
        )
        self.assertEqual(
            NAVER.version_meta_url, "https://map.pstatic.net/nrb/styles/basic.json"
        )
        self.assertEqual(dict(NAVER.headers), {})
        self.assertEqual(NAVER.attribution, "© NAVER")
        self.assertEqual(NAVER.tile_size, 256)
        self.assertEqual(NAVER.min_zoom, 0)
        self.assertEqual(NAVER.max_zoom, 20)
        self.assertEqual(NAVER.max_native_zoom, 21)
        self.assertIsNone(NAVER.url_template_dark)

    def test_osm_constants_match_measurements(self) -> None:
        self.assertEqual(
            OSM.url_template, "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        )
        self.assertEqual(OSM.attribution, "© OpenStreetMap contributors")
        self.assertEqual(
            (OSM.tile_size, OSM.min_zoom, OSM.max_zoom, OSM.max_native_zoom),
            (256, 1, 20, 19),
        )

    def test_vworld_is_marked_unverified(self) -> None:
        # No valid key was available, so the definition must announce itself as
        # unverified rather than pass as measured (docs/05 section 7.2).
        self.assertTrue(VWORLD.unverified)
        self.assertTrue(VWORLD.needs_api_key)

    def test_get_provider(self) -> None:
        self.assertIs(providers.get_provider("naver"), NAVER)
        self.assertIsNone(providers.get_provider("nope"))
        self.assertIsNone(providers.get_provider(None))
        self.assertIsNone(providers.get_provider(""))


class TestBuildTileUrl(unittest.TestCase):
    """URL substitution (design decision D2)."""

    def test_naver_url(self) -> None:
        self.assertEqual(
            providers.build_tile_url(
                NAVER, version="1787907321", z=12, x=3492, y=1586
            ),
            "https://map.pstatic.net/nrb/styles/basic/1787907321/12/3492/1586"
            ".png?mt=bg.ol.ts.ar.lko",
        )

    def test_missing_version_is_an_error(self) -> None:
        with self.assertRaises(providers.TileUrlError):
            providers.build_tile_url(NAVER, version=None, z=12, x=3492, y=1586)

    def test_missing_api_key_is_an_error(self) -> None:
        with self.assertRaises(providers.TileUrlError):
            providers.build_tile_url(VWORLD, api_key=None, z=12, x=3492, y=1586)

    def test_vworld_uses_y_then_x(self) -> None:
        # Coordinate order per docs/04 section 4.1; still unverified upstream.
        self.assertEqual(
            providers.build_tile_url(VWORLD, api_key="KEY", z=12, x=3492, y=1586),
            "https://api.vworld.kr/req/wmts/1.0.0/KEY/Base/12/1586/3492.png",
        )

    def test_custom_template(self) -> None:
        self.assertEqual(
            providers.build_tile_url(
                CUSTOM,
                url_template="https://example.org/{z}/{x}/{y}@2x.png?k={api_key}",
                api_key="SECRET",
                z=3,
                x=1,
                y=2,
            ),
            "https://example.org/3/1/2@2x.png?k=SECRET",
        )

    def test_stray_braces_raise_our_error_not_a_format_error(self) -> None:
        """A user template is never treated as a format string (D2).

        ``"...{oops}".format(z=1)`` would raise KeyError; we raise TileUrlError
        so the caller has one failure mode to handle.
        """
        with self.assertRaises(providers.TileUrlError):
            providers.build_tile_url(
                CUSTOM,
                url_template="https://example.org/{z}/{x}/{y}.png?s={oops}",
                z=1,
                x=1,
                y=1,
            )

    def test_doubled_braces_do_not_crash(self) -> None:
        with self.assertRaises(providers.TileUrlError):
            providers.build_tile_url(
                CUSTOM,
                url_template="https://example.org/{z}/{x}/{y}.png?s={{a}}",
                z=1,
                x=1,
                y=1,
            )

    def test_ha_version_placeholder(self) -> None:
        self.assertEqual(
            providers.build_tile_url(
                CUSTOM,
                url_template="https://example.org/{ha_version}/{z}/{x}/{y}.png",
                ha_version="2026.9.0",
                z=1,
                x=2,
                y=3,
            ),
            "https://example.org/2026.9.0/1/2/3.png",
        )

    def test_dark_template_used_for_dark_variant(self) -> None:
        provider = providers.TileProvider(
            id="two",
            name="Two",
            url_template="https://example.org/light/{z}/{x}/{y}.png",
            url_template_dark="https://example.org/dark/{z}/{x}/{y}.png",
        )
        self.assertIn(
            "/dark/",
            providers.build_tile_url(
                provider, variant=const.VARIANT_DARK, z=1, x=1, y=1
            ),
        )
        self.assertIn(
            "/light/",
            providers.build_tile_url(
                provider, variant=const.VARIANT_LIGHT, z=1, x=1, y=1
            ),
        )

    def test_dark_variant_falls_back_to_light_when_absent(self) -> None:
        # Naver has no dark family (docs/05 F8).
        self.assertIn(
            "/basic/",
            providers.build_tile_url(
                NAVER, version="1", variant=const.VARIANT_DARK, z=1, x=1, y=1
            ),
        )


class TestNaverFormatAndLayerSelector(unittest.TestCase):
    """The .png format (D13) and the mt layer selector (D14)."""

    def test_both_templates_are_png_not_jpg(self) -> None:
        """D13 reverses the v2.0.1 .jpg choice.

        A map tile is mostly glyphs and hairlines, which is what JPEG block
        artefacts destroy; the 1.x implementation used .png for that reason.
        """
        for template in (NAVER.url_template, NAVER.url_template_retina):
            self.assertIn(".png", template)
            self.assertNotIn(".jpg", template)

    def test_both_templates_carry_the_measured_layer_selector(self) -> None:
        """D14: the mt value is the measured one, never assembled from guesses.

        Unknown mt components are answered with HTTP 400 upstream, so the
        constant is the only safe source for this string.
        """
        self.assertEqual(const.NAVER_MAP_TYPES, "bg.ol.ts.ar.lko")
        self.assertEqual(const.NAVER_MAP_TYPES_QUERY, "mt")
        expected = f"?{const.NAVER_MAP_TYPES_QUERY}={const.NAVER_MAP_TYPES}"
        for template in (NAVER.url_template, NAVER.url_template_retina):
            self.assertTrue(template.endswith(expected), template)

    def test_the_layer_selector_is_not_hardcoded_twice(self) -> None:
        """The 1x and 2x templates must not be able to drift apart."""
        self.assertEqual(
            NAVER.url_template.partition("?")[2],
            NAVER.url_template_retina.partition("?")[2],
        )

    def test_the_selector_is_the_only_query_parameter(self) -> None:
        for template in (NAVER.url_template, NAVER.url_template_retina):
            self.assertEqual(template.count("?"), 1, template)
            self.assertEqual(template.count("&"), 0, template)


class TestQueryStringTemplates(unittest.TestCase):
    """A template with a query string must survive substitution intact.

    ``?mt=bg.ol.ts.ar.lko`` (design decision D14) put a query string into the
    upstream URL for the first time, so the placeholder scan and the coordinate
    substitution are asserted against it explicitly.
    """

    def test_coordinates_are_substituted_and_the_query_is_preserved(self) -> None:
        url = providers.build_tile_url(
            NAVER, version="1787907321", z=17, x=111925, y=50801
        )
        self.assertEqual(
            url,
            "https://map.pstatic.net/nrb/styles/basic/1787907321/17/111925/50801"
            ".png?mt=bg.ol.ts.ar.lko",
        )
        # No brace survived, and the query is byte-for-byte what was measured.
        self.assertNotIn("{", url)
        self.assertNotIn("}", url)
        self.assertTrue(url.endswith("?mt=bg.ol.ts.ar.lko"))

    def test_the_at2x_marker_lands_before_the_query_not_after(self) -> None:
        """``...@2x.png?mt=`` - a query after @2x would 404 upstream."""
        url = providers.build_tile_url(
            NAVER, version="1", scale=2, z=1, x=2, y=3
        )
        self.assertEqual(
            url,
            "https://map.pstatic.net/nrb/styles/basic/1/1/2/3@2x.png"
            "?mt=bg.ol.ts.ar.lko",
        )
        self.assertLess(url.index("@2x"), url.index("?"))

    def test_a_query_string_does_not_trip_the_placeholder_check(self) -> None:
        """The D2 leftover-placeholder scan must not false-positive on a query."""
        for template in (NAVER.url_template, NAVER.url_template_retina):
            # No exception is the assertion.
            providers.build_tile_url(
                NAVER, url_template=template, version="1", z=1, x=1, y=1
            )

    def test_a_custom_template_with_its_own_query_still_works(self) -> None:
        self.assertEqual(
            providers.build_tile_url(
                CUSTOM,
                url_template="https://example.org/{z}/{x}/{y}.png?a=1&b=2",
                z=1,
                x=2,
                y=3,
            ),
            "https://example.org/1/2/3.png?a=1&b=2",
        )

    def test_a_placeholder_inside_a_query_is_still_an_error(self) -> None:
        with self.assertRaises(providers.TileUrlError):
            providers.build_tile_url(
                CUSTOM,
                url_template="https://example.org/{z}/{x}/{y}.png?mt={oops}",
                z=1,
                x=1,
                y=1,
            )


class TestRetinaTemplates(unittest.TestCase):
    """The @2x provider fields (design decision D12)."""

    def test_naver_retina_template_matches_the_measurement(self) -> None:
        # Measured 512x512, docs/05-UPSTREAM-FINDINGS.md section 3.
        self.assertEqual(
            NAVER.url_template_retina,
            "https://map.pstatic.net/nrb/styles/basic/{version}/{z}/{x}/{y}"
            "@2x.png?mt=bg.ol.ts.ar.lko",
        )
        # No dark family exists at all, so no dark @2x either (F8).
        self.assertIsNone(NAVER.url_template_dark_retina)

    def test_no_retina_template_is_guessed_for_the_other_providers(self) -> None:
        """Hard constraint 6: osm/vworld/custom get None, never a guess.

        osm because core 2026.9 states "OSM serves no @2x variant" (docs/02
        3.2), vworld because its 1x template itself is unverified (docs/05 7.2),
        custom because a user template cannot be assumed to accept @2x.
        """
        for provider in (OSM, VWORLD, CUSTOM):
            self.assertIsNone(provider.url_template_retina, provider.id)
            self.assertIsNone(provider.url_template_dark_retina, provider.id)
            self.assertFalse(providers.supports_retina(provider), provider.id)

    def test_supports_retina(self) -> None:
        self.assertTrue(providers.supports_retina(NAVER, const.VARIANT_LIGHT))
        # Naver's dark variant reuses the light tiles, so the light @2x
        # template is its correct high-DPI answer too.
        self.assertTrue(providers.supports_retina(NAVER, const.VARIANT_DARK))
        self.assertFalse(providers.supports_retina(OSM, const.VARIANT_LIGHT))

    def test_a_provider_with_its_own_dark_family_needs_a_dark_retina_template(
        self,
    ) -> None:
        """A dark 1x family without a dark @2x must not borrow the light @2x."""
        provider = providers.TileProvider(
            id="two",
            name="Two",
            url_template="https://example.org/l/{z}/{x}/{y}.png",
            url_template_dark="https://example.org/d/{z}/{x}/{y}.png",
            url_template_retina="https://example.org/l/{z}/{x}/{y}@2x.png",
        )
        self.assertTrue(providers.supports_retina(provider, const.VARIANT_LIGHT))
        self.assertFalse(providers.supports_retina(provider, const.VARIANT_DARK))


class TestBuildTileUrlScale(unittest.TestCase):
    """``scale`` on build_tile_url (design decision D12)."""

    def test_scale_two_uses_the_at2x_template(self) -> None:
        self.assertEqual(
            providers.build_tile_url(
                NAVER, version="1787907321", scale=2, z=12, x=3492, y=1586
            ),
            "https://map.pstatic.net/nrb/styles/basic/1787907321/12/3492/1586"
            "@2x.png?mt=bg.ol.ts.ar.lko",
        )

    def test_scale_one_is_the_default_and_has_no_at2x(self) -> None:
        for kwargs in ({}, {"scale": 1}):
            url = providers.build_tile_url(
                NAVER, version="1787907321", z=12, x=3492, y=1586, **kwargs
            )
            self.assertNotIn("@2x", url)

    def test_scale_two_without_a_retina_template_falls_back_silently(self) -> None:
        """No exception, no 404 - just the 1x URL (hard constraint 3)."""
        url = providers.build_tile_url(OSM, scale=2, z=12, x=3492, y=1586)
        self.assertEqual(url, "https://tile.openstreetmap.org/12/3492/1586.png")

    def test_scale_two_on_a_dark_variant_without_dark_at2x_falls_back_to_dark(
        self,
    ) -> None:
        provider = providers.TileProvider(
            id="two",
            name="Two",
            url_template="https://example.org/l/{z}/{x}/{y}.png",
            url_template_dark="https://example.org/d/{z}/{x}/{y}.png",
            url_template_retina="https://example.org/l/{z}/{x}/{y}@2x.png",
        )
        url = providers.build_tile_url(
            provider, variant=const.VARIANT_DARK, scale=2, z=1, x=1, y=1
        )
        self.assertEqual(url, "https://example.org/d/1/1/1.png")

    def test_scale_two_uses_the_dark_at2x_template_when_there_is_one(self) -> None:
        provider = providers.TileProvider(
            id="two",
            name="Two",
            url_template="https://example.org/l/{z}/{x}/{y}.png",
            url_template_dark="https://example.org/d/{z}/{x}/{y}.png",
            url_template_retina="https://example.org/l/{z}/{x}/{y}@2x.png",
            url_template_dark_retina="https://example.org/d/{z}/{x}/{y}@2x.png",
        )
        self.assertEqual(
            providers.build_tile_url(
                provider, variant=const.VARIANT_DARK, scale=2, z=1, x=1, y=1
            ),
            "https://example.org/d/1/1/1@2x.png",
        )

    def test_an_explicit_template_still_wins(self) -> None:
        """A caller-supplied template is honoured verbatim at any scale."""
        self.assertEqual(
            providers.build_tile_url(
                CUSTOM,
                url_template="https://example.org/{z}/{x}/{y}.png",
                scale=2,
                z=1,
                x=2,
                y=3,
            ),
            "https://example.org/1/2/3.png",
        )

    def test_unexpected_scale_values_behave_as_one(self) -> None:
        for scale in (0, 1, 3, -2):
            self.assertNotIn(
                "@2x",
                providers.build_tile_url(
                    NAVER, version="1", scale=scale, z=1, x=1, y=1
                ),
                scale,
            )


class TestValidateTileCoords(unittest.TestCase):
    """Coordinate validation (design decision D8)."""

    def test_valid(self) -> None:
        self.assertEqual(
            providers.validate_tile_coords(NAVER, "12", "3492", "1586"),
            (12, 3492, 1586),
        )

    def test_too_many_digits_rejected_before_int(self) -> None:
        long_digits = "9" * (const.MAX_COORDINATE_DIGITS + 1)
        self.assertIsNone(
            providers.validate_tile_coords(NAVER, "12", long_digits, "1586")
        )

    def test_non_numeric_rejected(self) -> None:
        for parts in (
            ("a", "1", "1"),
            ("1", "b", "1"),
            ("1", "1", "c"),
            ("", "1", "1"),
        ):
            self.assertIsNone(providers.validate_tile_coords(NAVER, *parts))

    def test_negative_rejected(self) -> None:
        self.assertIsNone(providers.validate_tile_coords(NAVER, "-1", "0", "0"))
        self.assertIsNone(providers.validate_tile_coords(NAVER, "5", "-1", "0"))
        self.assertIsNone(providers.validate_tile_coords(NAVER, "5", "0", "-1"))

    def test_zoom_bounds_follow_the_provider(self) -> None:
        # Naver: TileJSON maxzoom 21 (docs/05 section 4).
        self.assertIsNotNone(providers.validate_tile_coords(NAVER, "21", "0", "0"))
        self.assertIsNone(providers.validate_tile_coords(NAVER, "22", "0", "0"))
        self.assertIsNotNone(providers.validate_tile_coords(NAVER, "0", "0", "0"))
        # OSM: native max 19, min 1.
        self.assertIsNotNone(providers.validate_tile_coords(OSM, "19", "0", "0"))
        self.assertIsNone(providers.validate_tile_coords(OSM, "20", "0", "0"))
        self.assertIsNone(providers.validate_tile_coords(OSM, "0", "0", "0"))

    def test_out_of_pyramid_rejected(self) -> None:
        """Upstream answers 200 with a blank tile here (docs/05 section 4)."""
        self.assertIsNone(providers.validate_tile_coords(NAVER, "12", "99999", "99999"))
        self.assertIsNone(providers.validate_tile_coords(NAVER, "12", "4096", "0"))
        self.assertIsNotNone(
            providers.validate_tile_coords(NAVER, "12", "4095", "4095")
        )


class TestBuildStyle(unittest.TestCase):
    """Style document generation."""

    def test_shape(self) -> None:
        style = providers.build_style(
            NAVER,
            variant=const.VARIANT_LIGHT,
            tile_url_template=const.TILE_URL_TEMPLATE,
        )
        self.assertEqual(style["version"], 8)
        source = style["sources"]["basemap"]
        self.assertEqual(source["type"], "raster")
        self.assertEqual(
            source["tiles"], ["/api/map_tiles/naver_map_change/{z}/{x}/{y}.png"]
        )
        self.assertEqual(source["tileSize"], 256)
        self.assertEqual(source["minzoom"], 0)
        # Clamped to what the tile route serves.
        self.assertEqual(source["maxzoom"], 20)
        self.assertEqual(source["attribution"], "© NAVER")
        self.assertEqual(
            style["layers"], [{"id": "basemap", "type": "raster", "source": "basemap"}]
        )

    def test_no_sprite_or_glyphs(self) -> None:
        # A raster style needs neither and core's loadStyle() passes a missing
        # sprite straight through (docs/02 section 3.2).
        style = providers.build_style(
            NAVER, tile_url_template=const.TILE_URL_TEMPLATE
        )
        self.assertNotIn("sprite", style)
        self.assertNotIn("glyphs", style)

    def test_attribution_override(self) -> None:
        style = providers.build_style(
            CUSTOM, tile_url_template=const.TILE_URL_TEMPLATE, attribution="© Me"
        )
        self.assertEqual(style["sources"]["basemap"]["attribution"], "© Me")

    def test_attribution_never_taken_from_upstream_empty_string(self) -> None:
        # Upstream sends "" (docs/05 section 1); ours is always set.
        style = providers.build_style(
            NAVER, tile_url_template=const.TILE_URL_TEMPLATE, attribution=None
        )
        self.assertEqual(style["sources"]["basemap"]["attribution"], "© NAVER")

    def test_ac6_no_secret_or_upstream_domain_in_style(self) -> None:
        """AC6: the style document leaks neither API key nor upstream host.

        This is what makes the unauthenticated style endpoint safe.
        """
        for provider in providers.PROVIDERS.values():
            for variant in const.VARIANTS:
                style = providers.build_style(
                    provider,
                    variant=variant,
                    tile_url_template=providers.build_proxy_tile_template(
                        provider, variant=variant
                    ),
                    attribution=None,
                )
                serialized = json.dumps(style, ensure_ascii=False)
                for forbidden in (
                    "pstatic.net",
                    "vworld.kr",
                    "api_key",
                    "SECRET_KEY_VALUE",
                    "tile.openstreetmap.org",
                    # The mt layer selector is an upstream implementation
                    # detail and must not reach the browser either (D14).
                    "mt=",
                    const.NAVER_MAP_TYPES,
                ):
                    self.assertNotIn(
                        forbidden,
                        serialized,
                        f"{provider.id}/{variant} style leaks {forbidden}",
                    )
                for tile_url in style["sources"]["basemap"]["tiles"]:
                    self.assertTrue(
                        tile_url.startswith("/api/map_tiles/naver_map_change/"),
                        tile_url,
                    )


class TestBuildStyleScale(unittest.TestCase):
    """``scale`` on build_style, and the tileSize invariant (D12)."""

    def _style(self, provider: object, **kwargs: object) -> dict:
        variant = kwargs.pop("variant", const.VARIANT_LIGHT)
        return providers.build_style(
            provider,  # type: ignore[arg-type]
            variant=variant,  # type: ignore[arg-type]
            tile_url_template=providers.build_proxy_tile_template(
                provider,  # type: ignore[arg-type]
                variant=variant,  # type: ignore[arg-type]
            ),
            **kwargs,  # type: ignore[arg-type]
        )

    def test_scale_two_appends_the_scale_query(self) -> None:
        style = self._style(NAVER, scale=2)
        self.assertEqual(
            style["sources"]["basemap"]["tiles"],
            ["/api/map_tiles/naver_map_change/{z}/{x}/{y}.png?scale=2"],
        )

    def test_scale_one_appends_nothing(self) -> None:
        for kwargs in ({}, {"scale": 1}):
            style = self._style(NAVER, **kwargs)
            self.assertEqual(
                style["sources"]["basemap"]["tiles"],
                ["/api/map_tiles/naver_map_change/{z}/{x}/{y}.png"],
            )

    def test_scale_two_is_ignored_without_a_retina_template(self) -> None:
        """osm has no @2x, so its style must not ask the route for one."""
        style = self._style(OSM, scale=2)
        self.assertEqual(
            style["sources"]["basemap"]["tiles"],
            ["/api/map_tiles/naver_map_change/{z}/{x}/{y}.png"],
        )

    def test_scale_joins_an_existing_variant_query_with_an_ampersand(self) -> None:
        provider = providers.TileProvider(
            id="two",
            name="Two",
            url_template="https://example.org/l/{z}/{x}/{y}.png",
            url_template_dark="https://example.org/d/{z}/{x}/{y}.png",
            url_template_retina="https://example.org/l/{z}/{x}/{y}@2x.png",
            url_template_dark_retina="https://example.org/d/{z}/{x}/{y}@2x.png",
        )
        style = self._style(provider, variant=const.VARIANT_DARK, scale=2)
        self.assertEqual(
            style["sources"]["basemap"]["tiles"],
            ["/api/map_tiles/naver_map_change/{z}/{x}/{y}.png?variant=dark&scale=2"],
        )

    def test_tile_size_is_always_the_provider_tile_size(self) -> None:
        """★ The single most important assertion of design decision D12.

        In a MapLibre raster source ``tileSize`` is the logical size the tile
        covers, not the pixel size of the image that arrives. A 512px image
        declared as ``tileSize: 256`` is precisely how you request double pixel
        density. ``tileSize: 512`` would shift the zoom pyramid one level and
        put the basemap at the wrong scale and position, so the @2x switch must
        never touch it.
        """
        for provider in providers.PROVIDERS.values():
            for variant in const.VARIANTS:
                for scale in (1, 2):
                    style = self._style(provider, variant=variant, scale=scale)
                    self.assertEqual(
                        style["sources"]["basemap"]["tileSize"],
                        provider.tile_size,
                        f"{provider.id}/{variant}/scale={scale}",
                    )
                    self.assertEqual(
                        style["sources"]["basemap"]["tileSize"],
                        256,
                        f"{provider.id}/{variant}/scale={scale}",
                    )

    def test_zoom_range_is_unchanged_by_scale(self) -> None:
        """The pyramid is geometry; @2x only changes the bytes."""
        one = self._style(NAVER, scale=1)["sources"]["basemap"]
        two = self._style(NAVER, scale=2)["sources"]["basemap"]
        self.assertEqual((one["minzoom"], one["maxzoom"]), (two["minzoom"], two["maxzoom"]))

    def test_ac6_still_holds_at_scale_two(self) -> None:
        """AC6 regression guard: no secret, no upstream host, at either scale."""
        for provider in providers.PROVIDERS.values():
            for variant in const.VARIANTS:
                for scale in (1, 2):
                    style = self._style(
                        provider, variant=variant, scale=scale, attribution=None
                    )
                    serialized = json.dumps(style, ensure_ascii=False)
                    for forbidden in (
                        "pstatic.net",
                        "vworld.kr",
                        "api_key",
                        "SECRET_KEY_VALUE",
                        "tile.openstreetmap.org",
                        "@2x",
                        ".jpg",
                        # The mt layer selector stays server side (D14).
                        "mt=",
                        const.NAVER_MAP_TYPES,
                    ):
                        self.assertNotIn(
                            forbidden,
                            serialized,
                            f"{provider.id}/{variant}/scale={scale} leaks {forbidden}",
                        )
                    for tile_url in style["sources"]["basemap"]["tiles"]:
                        self.assertTrue(
                            tile_url.startswith("/api/map_tiles/naver_map_change/"),
                            tile_url,
                        )


class TestProxyTileTemplate(unittest.TestCase):
    """The tile template embedded in the style document."""

    def test_light(self) -> None:
        self.assertEqual(
            providers.build_proxy_tile_template(NAVER, variant=const.VARIANT_LIGHT),
            "/api/map_tiles/naver_map_change/{z}/{x}/{y}.png",
        )

    def test_dark_without_dark_tiles_is_identical(self) -> None:
        self.assertEqual(
            providers.build_proxy_tile_template(NAVER, variant=const.VARIANT_DARK),
            providers.build_proxy_tile_template(NAVER, variant=const.VARIANT_LIGHT),
        )

    def test_dark_with_dark_tiles_carries_a_query(self) -> None:
        provider = providers.TileProvider(
            id="two",
            name="Two",
            url_template="https://example.org/l/{z}/{x}/{y}.png",
            url_template_dark="https://example.org/d/{z}/{x}/{y}.png",
        )
        self.assertEqual(
            providers.build_proxy_tile_template(provider, variant=const.VARIANT_DARK),
            "/api/map_tiles/naver_map_change/{z}/{x}/{y}.png?variant=dark",
        )


class TestResolveHeaders(unittest.TestCase):
    """Header resolution."""

    def test_naver_sends_no_headers(self) -> None:
        # Measured: 200 with zero headers (docs/05 F2).
        self.assertEqual(resolve_or(NAVER, "2026.9.0"), {})

    def test_osm_user_agent_is_substituted(self) -> None:
        self.assertEqual(
            resolve_or(OSM, "2026.9.0"),
            {"User-Agent": "HomeAssistant/2026.9.0 (+naver_map_change)"},
        )

    def test_placeholder_header_dropped_without_a_version(self) -> None:
        self.assertEqual(resolve_or(OSM, None), {})


def resolve_or(provider: object, ha_version: str | None) -> dict:
    """Small helper so the assertions above read as one line."""
    return providers.resolve_headers(provider, ha_version=ha_version)  # type: ignore[arg-type]


class TestTileJsonParsing(unittest.TestCase):
    """Version-code parsing (design decision D3)."""

    def test_measured_document(self) -> None:
        self.assertEqual(
            providers.parse_tilejson_version(TILEJSON_SAMPLE), "1787907321"
        )

    def test_bytes_payload(self) -> None:
        self.assertEqual(
            providers.parse_tilejson_version(TILEJSON_SAMPLE.encode()), "1787907321"
        )

    def test_integer_version_is_stringified(self) -> None:
        self.assertEqual(
            providers.parse_tilejson_version('{"version": 1787907321}'), "1787907321"
        )

    def test_rejects_garbage(self) -> None:
        for payload in (
            "not json",
            "[]",
            '"a string"',
            "{}",
            '{"version": null}',
            '{"version": ""}',
            '{"version": {"a": 1}}',
            # No path traversal into the tile URL.
            '{"version": "../../etc/passwd"}',
            '{"version": "1/2"}',
        ):
            self.assertIsNone(providers.parse_tilejson_version(payload), payload)


class TestAsyncFetchTileJsonVersion(unittest.TestCase):
    """The one coroutine in providers.py, exercised with a fake session."""

    def test_success(self) -> None:
        session = FakeSession(FakeResponse(200, TILEJSON_SAMPLE.encode()))
        result = asyncio.run(
            providers.async_fetch_tilejson_version(
                session,  # type: ignore[arg-type]
                "https://example.org/basic.json",
                timeout=object(),
            )
        )
        self.assertEqual(result, "1787907321")
        self.assertEqual(session.calls[0][0], "https://example.org/basic.json")
        self.assertIn("timeout", session.calls[0][1])

    def test_non_200_returns_none(self) -> None:
        session = FakeSession(FakeResponse(500, b"{}"))
        self.assertIsNone(
            asyncio.run(
                providers.async_fetch_tilejson_version(
                    session,  # type: ignore[arg-type]
                    "https://example.org/basic.json",
                )
            )
        )

    def test_transport_error_returns_none(self) -> None:
        session = FakeSession(error=OSError("boom"))
        self.assertIsNone(
            asyncio.run(
                providers.async_fetch_tilejson_version(
                    session,  # type: ignore[arg-type]
                    "https://example.org/basic.json",
                )
            )
        )

    def test_no_timeout_argument_when_none(self) -> None:
        session = FakeSession(FakeResponse(200, TILEJSON_SAMPLE.encode()))
        asyncio.run(
            providers.async_fetch_tilejson_version(
                session,  # type: ignore[arg-type]
                "https://example.org/basic.json",
            )
        )
        self.assertNotIn("timeout", session.calls[0][1])


if __name__ == "__main__":
    unittest.main()
