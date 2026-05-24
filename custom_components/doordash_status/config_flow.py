"""Config flow for DoorDash Status."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import DoorDashApiClient, DoorDashApiError, DoorDashAuthError, DoorDashConnectionError
from .const import (
    AUTH_MODE_BROWSER_SESSION,
    AUTH_MODE_TRACKING_URL,
    CONF_AUTH_MODE,
    CONF_BASE_URL,
    CONF_BROWSER_COOKIE,
    CONF_SCAN_INTERVAL_MINUTES,
    CONF_TRACKING_URL,
    DEFAULT_BASE_URL,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
)


class InvalidDoorDashUrl(ValueError):
    """Raised when the supplied DoorDash URL is malformed."""


class DoorDashStatusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for DoorDash Status."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the DoorDash config flow."""
        self._base_url = DEFAULT_BASE_URL
        self._tracking_url = ""
        self._browser_cookie = ""

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the options flow handler."""
        return DoorDashStatusOptionsFlow(config_entry)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Choose how DoorDash should be connected."""
        if user_input is not None:
            if user_input[CONF_AUTH_MODE] == AUTH_MODE_TRACKING_URL:
                return await self.async_step_tracking_url()
            return await self.async_step_browser_session()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_AUTH_MODE,
                        default=AUTH_MODE_TRACKING_URL,
                    ): vol.In(
                        {
                            AUTH_MODE_TRACKING_URL: "Tracking link",
                            AUTH_MODE_BROWSER_SESSION: "Browser session cookie",
                        }
                    )
                }
            ),
        )

    async def async_step_tracking_url(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure DoorDash using a pasted tracking URL."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                self._tracking_url = _normalize_url(user_input[CONF_TRACKING_URL])
                info = await _async_validate_tracking_url(self.hass, self._tracking_url)
            except InvalidDoorDashUrl:
                errors[CONF_TRACKING_URL] = "invalid_url"
            except DoorDashConnectionError:
                errors["base"] = "cannot_connect"
            except DoorDashApiError:
                errors["base"] = "cannot_use_tracking_url"
            else:
                await self.async_set_unique_id(f"{info['host']}:{self._tracking_url}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=info["title"],
                    data={CONF_TRACKING_URL: self._tracking_url},
                )

        return self.async_show_form(
            step_id="tracking_url",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_TRACKING_URL,
                        default=(user_input or {}).get(CONF_TRACKING_URL, "https://"),
                    ): str,
                }
            ),
            errors=errors,
        )

    async def async_step_browser_session(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure DoorDash using a copied browser cookie header."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                self._base_url = _normalize_url(user_input[CONF_BASE_URL], path_ok=True)
                self._browser_cookie = _normalize_cookie_header(user_input[CONF_BROWSER_COOKIE])
                info = await _async_validate_browser_session(
                    self.hass,
                    self._base_url,
                    self._browser_cookie,
                )
            except InvalidDoorDashUrl:
                errors[CONF_BASE_URL] = "invalid_url"
            except DoorDashConnectionError:
                errors["base"] = "cannot_connect"
            except DoorDashAuthError:
                errors["base"] = "invalid_auth"
            except DoorDashApiError:
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(f"{info['host']}:browser_session")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=info["title"],
                    data={
                        CONF_BASE_URL: self._base_url,
                        CONF_BROWSER_COOKIE: self._browser_cookie,
                    },
                )

        return self.async_show_form(
            step_id="browser_session",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_BASE_URL,
                        default=(user_input or {}).get(CONF_BASE_URL, DEFAULT_BASE_URL),
                    ): str,
                    vol.Required(CONF_BROWSER_COOKIE): str,
                }
            ),
            errors=errors,
        )


class DoorDashStatusOptionsFlow(config_entries.OptionsFlow):
    """Handle DoorDash Status options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Store the config entry being edited."""
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the options form."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self._config_entry.options
        data = self._config_entry.data

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL_MINUTES,
                        default=options.get(
                            CONF_SCAN_INTERVAL_MINUTES,
                            data.get(CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES),
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=2, max=30)),
                }
            ),
        )


async def _async_validate_tracking_url(hass, tracking_url: str) -> dict[str, Any]:
    """Validate a DoorDash tracking URL."""
    client = DoorDashApiClient(
        session=async_get_clientsession(hass),
        base_url=DEFAULT_BASE_URL,
        tracking_url=tracking_url,
    )
    info = await client.async_validate()
    return {
        "title": "DoorDash Status (Tracking link)",
        "host": info["host"],
    }


async def _async_validate_browser_session(
    hass,
    base_url: str,
    cookie_header: str,
) -> dict[str, Any]:
    """Validate a copied DoorDash browser session."""
    client = DoorDashApiClient(
        session=async_get_clientsession(hass),
        base_url=base_url,
        cookie_header=cookie_header,
    )
    info = await client.async_validate()
    return {
        "title": info["title"],
        "host": info["host"],
    }


def _normalize_url(value: str, *, path_ok: bool = False) -> str:
    """Normalize a user-provided DoorDash URL."""
    candidate = value.strip()
    if "://" not in candidate:
        candidate = f"https://{candidate}"

    parsed = urlsplit(candidate)
    if not parsed.scheme or not parsed.netloc:
        raise InvalidDoorDashUrl

    path = parsed.path.rstrip("/") if path_ok else parsed.path
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))


def _normalize_cookie_header(value: str) -> str:
    """Normalize a pasted Cookie header value."""
    normalized = value.strip()
    if normalized.lower().startswith("cookie:"):
        normalized = normalized.split(":", 1)[1].strip()
    return normalized
