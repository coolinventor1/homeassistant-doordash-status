"""DoorDash Status custom integration."""

from __future__ import annotations

import asyncio
from contextlib import suppress

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
    PLATFORMS,
)
from .coordinator import DoorDashDataUpdateCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up DoorDash Status from a config entry."""
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
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
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
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry after an options update."""
    await hass.config_entries.async_reload(entry.entry_id)
