"""Binary sensor platform for DoorDash Status."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import is_active_order_status
from .const import DOMAIN
from .coordinator import DoorDashDataUpdateCoordinator, DoorDashSnapshot
from .sensor import _device_info


@dataclass(frozen=True, kw_only=True)
class DoorDashBinarySensorDescription(BinarySensorEntityDescription):
    """Describe a DoorDash-backed binary sensor."""

    value_fn: Callable[[DoorDashSnapshot], bool]
    attrs_fn: Callable[[DoorDashSnapshot], dict[str, Any]]


def _latest_order(snapshot: DoorDashSnapshot) -> dict[str, Any] | None:
    """Return the latest order from the current snapshot."""
    return snapshot.latest_order


BINARY_SENSOR_DESCRIPTIONS: tuple[DoorDashBinarySensorDescription, ...] = (
    DoorDashBinarySensorDescription(
        key="latest_order_tracking_available",
        name="Latest order tracking available",
        icon="mdi:map-marker-path",
        value_fn=lambda data: bool(
            _latest_order(data)
            and (
                _latest_order(data).get("tracking_url")
                or _latest_order(data).get("order_detail_url")
            )
        ),
        attrs_fn=lambda data: {
            "tracking_url": _latest_order(data).get("tracking_url") if _latest_order(data) else None,
            "order_detail_url": _latest_order(data).get("order_detail_url") if _latest_order(data) else None,
            "order_id": _latest_order(data).get("id") if _latest_order(data) else None,
        },
    ),
    DoorDashBinarySensorDescription(
        key="latest_order_has_dasher",
        name="Latest order has dasher",
        icon="mdi:account-check-outline",
        value_fn=lambda data: bool(_latest_order(data) and _latest_order(data).get("dasher_name")),
        attrs_fn=lambda data: {
            "dasher_name": _latest_order(data).get("dasher_name") if _latest_order(data) else None,
            "order_id": _latest_order(data).get("id") if _latest_order(data) else None,
        },
    ),
    DoorDashBinarySensorDescription(
        key="latest_order_has_issue",
        name="Latest order has issue",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda data: bool(
            _latest_order(data)
            and (
                _latest_order(data).get("status") == "Issue"
                or (
                    isinstance(_latest_order(data).get("raw_status"), str)
                    and any(
                        token in _latest_order(data).get("raw_status").casefold()
                        for token in ("issue", "problem", "delay", "support", "unavailable")
                    )
                )
            )
        ),
        attrs_fn=lambda data: {
            "status": _latest_order(data).get("status") if _latest_order(data) else None,
            "raw_status": _latest_order(data).get("raw_status") if _latest_order(data) else None,
            "order_id": _latest_order(data).get("id") if _latest_order(data) else None,
        },
    ),
    DoorDashBinarySensorDescription(
        key="latest_order_is_active",
        name="Latest order is active",
        icon="mdi:truck-fast-outline",
        value_fn=lambda data: bool(
            _latest_order(data) and is_active_order_status(_latest_order(data).get("status"))
        ),
        attrs_fn=lambda data: {
            "status": _latest_order(data).get("status") if _latest_order(data) else None,
            "order_id": _latest_order(data).get("id") if _latest_order(data) else None,
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up DoorDash binary sensors from a config entry."""
    coordinator: DoorDashDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        DoorDashBinarySensor(coordinator, entry, description)
        for description in BINARY_SENSOR_DESCRIPTIONS
    )


class DoorDashBinarySensor(
    CoordinatorEntity[DoorDashDataUpdateCoordinator], BinarySensorEntity
):
    """Generic binary sensor backed by a DoorDash snapshot."""

    entity_description: DoorDashBinarySensorDescription

    def __init__(
        self,
        coordinator: DoorDashDataUpdateCoordinator,
        entry: ConfigEntry,
        description: DoorDashBinarySensorDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_name = description.name
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = _device_info(entry)

    @property
    def is_on(self) -> bool:
        """Return whether the binary sensor is currently on."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes for the binary sensor."""
        return self.entity_description.attrs_fn(self.coordinator.data)
