"""DoorDash Status custom integration."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import DoorDashApiClient
from .const import (
    CONF_BASE_URL,
    CONF_BROWSER_COOKIE,
    CONF_RENDERED_HELPER_URL,
    CONF_TRACKING_URL,
    DOMAIN,
    FRONTEND_CARD_URL,
    FRONTEND_STATIC_PATH,
    PLATFORMS,
)
from .coordinator import DoorDashDataUpdateCoordinator

DATA_ENTRY_IDS = "__entry_ids__"
DATA_FRONTEND_REGISTERED = "__frontend_registered__"
DATA_STATIC_REGISTERED = "__static_registered__"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up shared static resources for DoorDash Status."""
    domain_data = hass.data.setdefault(DOMAIN, {})

    if not domain_data.get(DATA_STATIC_REGISTERED):
        static_dir = Path(__file__).parent / "static"
        await hass.http.async_register_static_paths(
            [StaticPathConfig(FRONTEND_STATIC_PATH, str(static_dir), True)]
        )
        domain_data[DATA_STATIC_REGISTERED] = True

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up DoorDash Status from a config entry."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    entry_ids: set[str] = domain_data.setdefault(DATA_ENTRY_IDS, set())
    entry_ids.add(entry.entry_id)

    if not domain_data.get(DATA_FRONTEND_REGISTERED):
        frontend.add_extra_js_url(hass, FRONTEND_CARD_URL)
        domain_data[DATA_FRONTEND_REGISTERED] = True

    options = entry.options
    client = DoorDashApiClient(
        session=async_get_clientsession(hass),
        base_url=options.get(CONF_BASE_URL, entry.data.get(CONF_BASE_URL, "https://www.doordash.com")),
        cookie_header=options.get(CONF_BROWSER_COOKIE, entry.data.get(CONF_BROWSER_COOKIE)),
        rendered_helper_url=options.get(
            CONF_RENDERED_HELPER_URL,
            entry.data.get(CONF_RENDERED_HELPER_URL),
        ),
        tracking_url=options.get(CONF_TRACKING_URL, entry.data.get(CONF_TRACKING_URL)),
    )

    coordinator = DoorDashDataUpdateCoordinator(hass, entry, client)
    domain_data[entry.entry_id] = coordinator
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _async_initial_refresh() -> None:
        """Fetch the first DoorDash snapshot without blocking HA startup."""
        with suppress(asyncio.CancelledError):
            await coordinator.async_refresh()

    # Use a plain asyncio task so Home Assistant startup does not wait on
    # DoorDash's first background refresh.
    initial_refresh_task = asyncio.create_task(
        _async_initial_refresh(),
        name=f"{DOMAIN}_{entry.entry_id}_initial_refresh",
    )
    entry.async_on_unload(initial_refresh_task.cancel)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        domain_data = hass.data.get(DOMAIN, {})
        domain_data.pop(entry.entry_id, None)

        entry_ids = domain_data.get(DATA_ENTRY_IDS)
        if isinstance(entry_ids, set):
            entry_ids.discard(entry.entry_id)
            if not entry_ids and domain_data.get(DATA_FRONTEND_REGISTERED):
                frontend.remove_extra_js_url(hass, FRONTEND_CARD_URL)
                domain_data[DATA_FRONTEND_REGISTERED] = False

        if not domain_data:
            hass.data.pop(DOMAIN, None)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry after an options update."""
    await hass.config_entries.async_reload(entry.entry_id)
