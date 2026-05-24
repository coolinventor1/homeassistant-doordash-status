"""DoorDash Status custom integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import DoorDashApiClient
from .const import (
    CONF_BASE_URL,
    CONF_BROWSER_COOKIE,
    CONF_TRACKING_URL,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import DoorDashDataUpdateCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up DoorDash Status from a config entry."""
    client = DoorDashApiClient(
        session=async_get_clientsession(hass),
        base_url=entry.data.get(CONF_BASE_URL, "https://www.doordash.com"),
        cookie_header=entry.data.get(CONF_BROWSER_COOKIE),
        tracking_url=entry.data.get(CONF_TRACKING_URL),
    )

    coordinator = DoorDashDataUpdateCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
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
