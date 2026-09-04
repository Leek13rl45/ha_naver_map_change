"""Fixtures for the Home Assistant integration tests.

These require ``pytest-homeassistant-custom-component``; ``tests/conftest.py``
skips this whole directory when it is not installed.
"""

# ruff: noqa: I001 - sys.path has to be set up before these imports.

from __future__ import annotations

import json
import os
import sys
from collections.abc import Generator
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

# The repository root, so "custom_components.naver_map_change" is importable.
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
# This directory, so the shared upstream data module is importable.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from custom_components.naver_map_change.const import (  # noqa: E402
    CONF_PROVIDER,
    DOMAIN,
)
from upstream import (  # noqa: E402
    TILE_BODY,
    TILE_BODY_2X,
    TILEJSON_BODY,
    TILEJSON_URL,
    tile_url,
    tile_url_2x,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: Any,
) -> Generator[None]:
    """Let Home Assistant load the integration from custom_components/."""
    yield


@pytest.fixture
def mock_upstream(aioclient_mock: Any) -> Any:
    """Answer the TileJSON and one tile the way upstream measurably does."""
    aioclient_mock.get(
        TILEJSON_URL,
        text=json.dumps(TILEJSON_BODY),
        headers={"Content-Type": "application/json", "Cache-Control": "max-age=300"},
    )
    aioclient_mock.get(
        tile_url(),
        content=TILE_BODY,
        headers={"Content-Type": "image/png", "Cache-Control": "max-age=31536000"},
    )
    # The measured @2x variant (docs/05 section 3, design decision D12), with a
    # different body so a test can prove which resolution was fetched.
    aioclient_mock.get(
        tile_url_2x(),
        content=TILE_BODY_2X,
        headers={"Content-Type": "image/png", "Cache-Control": "max-age=31536000"},
    )
    return aioclient_mock


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a naver config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="NAVER Map (unofficial)",
        data={CONF_PROVIDER: "naver"},
        entry_id="naver_map_change_test",
    )


@pytest.fixture
def osm_config_entry() -> MockConfigEntry:
    """Return an osm config entry - the provider with no @2x variant."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="OpenStreetMap",
        data={CONF_PROVIDER: "osm"},
        entry_id="naver_map_change_osm_test",
    )
