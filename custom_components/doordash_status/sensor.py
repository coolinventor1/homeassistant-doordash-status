"""Sensor platform for DoorDash Status."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlsplit

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MAX_ATTRIBUTE_ORDERS
from .coordinator import DoorDashDataUpdateCoordinator, DoorDashSnapshot


@dataclass(frozen=True, kw_only=True)
class DoorDashSensorDescription(SensorEntityDescription):
    """Describe a DoorDash-backed sensor."""

    value_fn: Callable[[DoorDashSnapshot], Any]
    attrs_fn: Callable[[DoorDashSnapshot], dict[str, Any]]


def _serialize_order(order: dict[str, Any] | None) -> dict[str, Any] | None:
    """Serialize a normalized order for state attributes."""
    if order is None:
        return None

    return {
        "id": order.get("id"),
        "status": order.get("status"),
        "store_name": order.get("store_name"),
        "eta_at": order["eta_at"].isoformat() if order.get("eta_at") is not None else None,
        "eta_text": order.get("eta_text"),
        "updated_at": order["updated_at"].isoformat() if order.get("updated_at") is not None else None,
        "created_at": order["created_at"].isoformat() if order.get("created_at") is not None else None,
        "total_display": order.get("total_display"),
        "total_amount": order.get("total_amount"),
        "fulfillment_type": order.get("fulfillment_type"),
        "tracking_url": order.get("tracking_url"),
        "help_url": order.get("help_url"),
        "dasher_name": order.get("dasher_name"),
        "items": order.get("items"),
    }


def _serialize_orders(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Serialize a list of orders for attributes."""
    return [
        serialized
        for serialized in (
            _serialize_order(order) for order in orders[:MAX_ATTRIBUTE_ORDERS]
        )
        if serialized is not None
    ]


SENSOR_DESCRIPTIONS: tuple[DoorDashSensorDescription, ...] = (
    DoorDashSensorDescription(
        key="active_orders",
        name="Active orders",
        icon="mdi:truck-delivery-outline",
        value_fn=lambda data: len(data.active_orders),
        attrs_fn=lambda data: {
            "active_orders": _serialize_orders(data.active_orders),
            "all_orders": _serialize_orders(data.orders),
        },
    ),
    DoorDashSensorDescription(
        key="latest_order_status",
        name="Latest order status",
        icon="mdi:progress-clock",
        value_fn=lambda data: data.latest_order.get("status") if data.latest_order else None,
        attrs_fn=lambda data: {
            "latest_order": _serialize_order(data.latest_order),
            "all_orders": _serialize_orders(data.orders),
        },
    ),
    DoorDashSensorDescription(
        key="latest_order_store",
        name="Latest order store",
        icon="mdi:storefront-outline",
        value_fn=lambda data: data.latest_order.get("store_name") if data.latest_order else None,
        attrs_fn=lambda data: {
            "latest_order": _serialize_order(data.latest_order),
        },
    ),
    DoorDashSensorDescription(
        key="latest_order_eta",
        name="Latest order ETA",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: data.latest_order.get("eta_at") if data.latest_order else None,
        attrs_fn=lambda data: {
            "eta_text": data.latest_order.get("eta_text") if data.latest_order else None,
            "latest_order": _serialize_order(data.latest_order),
        },
    ),
    DoorDashSensorDescription(
        key="latest_order_eta_text",
        name="Latest order ETA text",
        icon="mdi:clock-fast",
        value_fn=lambda data: data.latest_order.get("eta_text") if data.latest_order else None,
        attrs_fn=lambda data: {
            "latest_order": _serialize_order(data.latest_order),
        },
    ),
    DoorDashSensorDescription(
        key="latest_order_total",
        name="Latest order total",
        icon="mdi:currency-usd",
        value_fn=lambda data: data.latest_order.get("total_display") if data.latest_order else None,
        attrs_fn=lambda data: {
            "total_amount": data.latest_order.get("total_amount") if data.latest_order else None,
            "latest_order": _serialize_order(data.latest_order),
        },
    ),
    DoorDashSensorDescription(
        key="latest_order_item_count",
        name="Latest order item count",
        icon="mdi:cart-outline",
        value_fn=lambda data: len(data.latest_order.get("items", [])) if data.latest_order else 0,
        attrs_fn=lambda data: {
            "items": data.latest_order.get("items") if data.latest_order else [],
            "latest_order": _serialize_order(data.latest_order),
        },
    ),
    DoorDashSensorDescription(
        key="latest_dasher",
        name="Latest dasher",
        icon="mdi:account-outline",
        value_fn=lambda data: data.latest_order.get("dasher_name") if data.latest_order else None,
        attrs_fn=lambda data: {
            "latest_order": _serialize_order(data.latest_order),
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up DoorDash sensors from a config entry."""
    coordinator: DoorDashDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        DoorDashSensor(coordinator, entry, description) for description in SENSOR_DESCRIPTIONS
    )


class DoorDashSensor(CoordinatorEntity[DoorDashDataUpdateCoordinator], SensorEntity):
    """Generic sensor backed by a DoorDash snapshot."""

    entity_description: DoorDashSensorDescription

    def __init__(
        self,
        coordinator: DoorDashDataUpdateCoordinator,
        entry: ConfigEntry,
        description: DoorDashSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_name = description.name
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

        host = urlsplit(entry.data.get("tracking_url") or entry.data.get("base_url") or "").netloc
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"DoorDash Status ({host or 'DoorDash'})",
            manufacturer="DoorDash",
            model=host or "doordash.com",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=entry.data.get("tracking_url") or entry.data.get("base_url"),
        )

    @property
    def native_value(self) -> Any:
        """Return the current sensor state."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes for the sensor."""
        return self.entity_description.attrs_fn(self.coordinator.data)
