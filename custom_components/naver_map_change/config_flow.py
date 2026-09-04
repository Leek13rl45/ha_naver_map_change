"""Config and options flow for the Naver Map Change integration."""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

import voluptuous as vol
from aiohttp import ClientError
from awesomeversion import AwesomeVersion
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CACHE_MAX_BYTES,
    CONF_API_KEY,
    CONF_ATTRIBUTION,
    CONF_CACHE_MAX_BYTES,
    CONF_DARK_VARIANT,
    CONF_PROVIDER,
    CONF_RETINA,
    CONF_URL_TEMPLATE,
    DEFAULT_DARK_VARIANT,
    DEFAULT_PROVIDER,
    DEFAULT_RETINA,
    DOMAIN,
    MIN_HA_VERSION,
    TEST_TILE_X,
    TEST_TILE_Y,
    TEST_TILE_Z,
)
from .providers import (
    PROVIDERS,
    TileProvider,
    TileUrlError,
    async_fetch_tilejson_version,
    build_tile_url,
    get_provider,
    resolve_headers,
)
from .view import UPSTREAM_TIMEOUT

_LOGGER = logging.getLogger(__name__)

_PROVIDER_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=[
            selector.SelectOptionDict(value=provider.id, label=provider.name)
            for provider in PROVIDERS.values()
        ],
        mode=selector.SelectSelectorMode.DROPDOWN,
    )
)


def _ha_version_supported() -> bool:
    """Return whether this Home Assistant is new enough.

    Below 2026.9.0 the map is built differently and this design does not apply
    (docs/02-HA-PLATFORM-2026.md section 3).

    AwesomeVersion is imported at module level on purpose: an import inside a
    coroutine is one of the blocking calls Home Assistant detects (docs/02
    section 4.7). The try only guards the comparison, which can raise on an
    unexpected version string.
    """
    try:
        return AwesomeVersion(HA_VERSION) >= AwesomeVersion(MIN_HA_VERSION)
    except Exception:  # noqa: BLE001 - a parsing quirk must not block setup
        _LOGGER.debug("Could not compare Home Assistant version %s", HA_VERSION)
        return True


async def _async_test_provider(
    hass: Any,
    provider: TileProvider,
    *,
    api_key: str | None,
    url_template: str | None,
) -> str | None:
    """Fetch one real tile, returning None on success or an error key.

    A status-only check is not enough: VWorld answers an unregistered key with
    HTTP 200 and an OWS ExceptionReport XML body (docs/05 section 7.2), so the
    Content-Type has to be an image. This only proves the *server* can reach
    upstream; because upstream CORS is not our concern once we proxy, that is
    all this test claims (docs/05 section 6).
    """
    session = async_get_clientsession(hass)

    version: str | None = None
    if provider.version_meta_url is not None:
        version = await async_fetch_tilejson_version(
            session, provider.version_meta_url, timeout=UPSTREAM_TIMEOUT
        )
        if version is None:
            return "version_unavailable"

    try:
        url = build_tile_url(
            provider,
            version=version,
            api_key=api_key,
            url_template=url_template,
            z=TEST_TILE_Z,
            x=TEST_TILE_X,
            y=TEST_TILE_Y,
            ha_version=HA_VERSION,
        )
    except TileUrlError as err:
        _LOGGER.debug("Test URL could not be built: %s", err)
        return "invalid_url_template"

    try:
        async with session.get(
            url,
            headers=resolve_headers(provider, ha_version=HA_VERSION),
            timeout=UPSTREAM_TIMEOUT,
        ) as response:
            if response.status >= HTTPStatus.BAD_REQUEST:
                _LOGGER.debug("Test tile %s returned %s", url, response.status)
                return "cannot_connect"
            media_type = (
                response.headers.get("Content-Type", "").partition(";")[0].strip()
            )
            if not media_type.lower().startswith("image/"):
                _LOGGER.debug("Test tile %s answered %s", url, media_type)
                return "not_an_image"
    except (ClientError, TimeoutError) as err:
        _LOGGER.debug("Test tile %s failed: %s", url, err)
        return "cannot_connect"
    return None


class NaverMapChangeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._provider_id: str = DEFAULT_PROVIDER

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a provider."""
        if not _ha_version_supported():
            return self.async_abort(reason="unsupported_ha_version")

        errors: dict[str, str] = {}
        if user_input is not None:
            self._provider_id = user_input[CONF_PROVIDER]
            provider = get_provider(self._provider_id)
            if provider is None:
                return self.async_abort(reason="unknown_provider")
            if provider.needs_api_key or provider.needs_url_template:
                return await self.async_step_provider()
            # Nothing more to ask, so the connection test belongs here: an
            # entry whose provider cannot serve tiles would silently fall back
            # to the OpenStreetMap style (design decision D10), which is not
            # what the user asked for. Surfaced as a retryable error, never as
            # an abort, because a version fetch can fail transiently.
            if (
                error := await _async_test_provider(
                    self.hass, provider, api_key=None, url_template=None
                )
            ) is None:
                return await self._async_finish(provider, {})
            errors["base"] = error

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_PROVIDER, default=self._provider_id
                    ): _PROVIDER_SELECTOR
                }
            ),
            errors=errors,
        )

    async def async_step_provider(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the credentials or template the provider needs."""
        provider = get_provider(self._provider_id)
        if provider is None:
            return self.async_abort(reason="unknown_provider")

        errors: dict[str, str] = {}
        if user_input is not None:
            if (
                error := await _async_test_provider(
                    self.hass,
                    provider,
                    api_key=user_input.get(CONF_API_KEY),
                    url_template=user_input.get(CONF_URL_TEMPLATE),
                )
            ) is None:
                return await self._async_finish(provider, user_input)
            errors["base"] = error

        fields: dict[Any, Any] = {}
        if provider.needs_api_key:
            fields[vol.Required(CONF_API_KEY)] = selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            )
        if provider.needs_url_template:
            fields[vol.Required(CONF_URL_TEMPLATE)] = selector.TextSelector()
            fields[vol.Optional(CONF_ATTRIBUTION, default="")] = selector.TextSelector()

        return self.async_show_form(
            step_id="provider",
            data_schema=vol.Schema(fields),
            errors=errors,
            description_placeholders={"provider": provider.name},
        )

    async def _async_finish(
        self, provider: TileProvider, user_input: dict[str, Any]
    ) -> ConfigFlowResult:
        """Create the entry."""
        return self.async_create_entry(
            title=provider.name,
            data={CONF_PROVIDER: provider.id, **user_input},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> OptionsFlow:
        """Return the options flow."""
        return NaverMapChangeOptionsFlow()


class NaverMapChangeOptionsFlow(OptionsFlow):
    """Handle the options flow.

    Changing anything reloads the entry (see ``_async_update_listener``), which
    rebuilds the cache and makes the style endpoint answer with the new
    provider.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the options."""
        current: dict[str, Any] = {
            **self.config_entry.data,
            **self.config_entry.options,
        }
        errors: dict[str, str] = {}

        if user_input is not None:
            provider = get_provider(user_input[CONF_PROVIDER])
            if provider is None:
                errors["base"] = "unknown_provider"
            elif (
                error := await _async_test_provider(
                    self.hass,
                    provider,
                    api_key=user_input.get(CONF_API_KEY) or None,
                    url_template=user_input.get(CONF_URL_TEMPLATE) or None,
                )
            ) is not None:
                errors["base"] = error
            else:
                return self.async_create_entry(data=user_input)
            current = {**current, **user_input}

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_PROVIDER,
                    default=current.get(CONF_PROVIDER, DEFAULT_PROVIDER),
                ): _PROVIDER_SELECTOR,
                vol.Optional(
                    CONF_API_KEY, default=current.get(CONF_API_KEY, "")
                ): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
                vol.Optional(
                    CONF_URL_TEMPLATE, default=current.get(CONF_URL_TEMPLATE, "")
                ): selector.TextSelector(),
                vol.Optional(
                    CONF_ATTRIBUTION, default=current.get(CONF_ATTRIBUTION, "")
                ): selector.TextSelector(),
                vol.Optional(
                    CONF_DARK_VARIANT,
                    default=current.get(CONF_DARK_VARIANT, DEFAULT_DARK_VARIANT),
                ): selector.BooleanSelector(),
                # On by default (design decision D12). Offered as a switch
                # because @2x costs roughly 3.2x the bytes, which matters on a
                # metered connection even though it is what fixes the blur on a
                # Retina display.
                vol.Optional(
                    CONF_RETINA,
                    default=current.get(CONF_RETINA, DEFAULT_RETINA),
                ): selector.BooleanSelector(),
                vol.Optional(
                    CONF_CACHE_MAX_BYTES,
                    default=int(current.get(CONF_CACHE_MAX_BYTES, CACHE_MAX_BYTES)),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1 * 1024 * 1024,
                        max=256 * 1024 * 1024,
                        step=1024 * 1024,
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="B",
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="init", data_schema=schema, errors=errors
        )
