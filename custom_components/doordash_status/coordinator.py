"""Coordinator for DoorDash Status data refreshes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DoorDashApiClient, DoorDashApiError, DoorDashAuthError
from .const import CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES, DOMAIN

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class DoorDashSnapshot:
    """Normalized DoorDash data for entity consumption."""

    orders: list[dict[str, Any]]
    active_orders: list[dict[str, Any]]
    latest_order: dict[str, Any] | None


class DoorDashDataUpdateCoordinator(DataUpdateCoordinator[DoorDashSnapshot]):
    """Poll DoorDash and keep the latest snapshot in memory."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: DoorDashApiClient,
    ) -> None:
        """Initialize the coordinator."""
        interval_minutes = entry.options.get(
            CONF_SCAN_INTERVAL_MINUTES,
            entry.data.get(CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES),
        )

        super().__init__(
            hass,
            logger=_LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(minutes=interval_minutes),
        )
        self._client = client

    async def _async_update_data(self) -> DoorDashSnapshot:
        """Fetch the latest DoorDash order snapshot."""
        try:
            orders = await self._client.async_get_orders()
        except DoorDashAuthError as err:
            raise UpdateFailed(
                "DoorDash rejected the current session or tracking source."
            ) from err
        except DoorDashApiError as err:
            raise UpdateFailed(str(err)) from err

        active_orders = [order for order in orders if _is_active_status(order.get("status"))]
        latest_order = active_orders[0] if active_orders else (orders[0] if orders else None)

        return DoorDashSnapshot(
            orders=orders,
            active_orders=active_orders,
            latest_order=latest_order,
        )


def _is_active_status(status: str | None) -> bool:
    """Return whether a DoorDash status looks active rather than completed."""
    if status is None:
        return False

    lowered = status.lower()
    return not any(
        keyword in lowered
        for keyword in ("delivered", "complete", "completed", "cancelled", "canceled", "failed")
    )
