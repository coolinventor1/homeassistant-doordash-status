"""Sensor platform for DoorDash Status."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any, Callable
from urllib.parse import urlsplit

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MAX_ATTRIBUTE_ORDERS
from .coordinator import DoorDashDataUpdateCoordinator, DoorDashSnapshot


@dataclass(frozen=True, kw_only=True)
class DoorDashSensorDescription(SensorEntityDescription):
    """Describe a DoorDash-backed sensor."""

    value_fn: Callable[[DoorDashSnapshot], Any]
    attrs_fn: Callable[[DoorDashSnapshot], dict[str, Any]]


def _serialize_datetime(value: Any) -> str | None:
    """Serialize a datetime to ISO format for state attributes."""
    if value is None:
        return None
    return value.isoformat()


def _serialize_order(order: dict[str, Any] | None) -> dict[str, Any] | None:
    """Serialize a normalized order for state attributes."""
    if order is None:
        return None

    return {
        "id": order.get("id"),
        "order_detail_url": order.get("order_detail_url"),
        "raw_status": order.get("raw_status"),
        "status": order.get("status"),
        "status_step_text": order.get("status_step_text"),
        "status_message": order.get("status_message"),
        "milestone_text": order.get("milestone_text"),
        "milestone_message": order.get("milestone_message"),
        "store_name": order.get("store_name"),
        "store_image_url": order.get("store_image_url"),
        "eta_at": _serialize_datetime(order.get("eta_at")),
        "eta_text": order.get("eta_text"),
        "delivered_at": _serialize_datetime(order.get("delivered_at")),
        "updated_at": _serialize_datetime(order.get("updated_at")),
        "created_at": _serialize_datetime(order.get("created_at")),
        "payment_method": order.get("payment_method"),
        "payment_time": _serialize_datetime(order.get("payment_time")),
        "payment_amount_display": order.get("payment_amount_display"),
        "payment_amount": order.get("payment_amount"),
        "total_display": order.get("total_display"),
        "total_amount": order.get("total_amount"),
        "subtotal_display": order.get("subtotal_display"),
        "subtotal_amount": order.get("subtotal_amount"),
        "tip_display": order.get("tip_display"),
        "tip_amount": order.get("tip_amount"),
        "tax_display": order.get("tax_display"),
        "tax_amount": order.get("tax_amount"),
        "fees_display": order.get("fees_display"),
        "fees_amount": order.get("fees_amount"),
        "delivery_fee_display": order.get("delivery_fee_display"),
        "delivery_fee_amount": order.get("delivery_fee_amount"),
        "delivery_fee_original_display": order.get("delivery_fee_original_display"),
        "delivery_fee_original_amount": order.get("delivery_fee_original_amount"),
        "service_fee_display": order.get("service_fee_display"),
        "service_fee_amount": order.get("service_fee_amount"),
        "service_fee_original_display": order.get("service_fee_original_display"),
        "service_fee_original_amount": order.get("service_fee_original_amount"),
        "express_fee_display": order.get("express_fee_display"),
        "express_fee_amount": order.get("express_fee_amount"),
        "small_order_fee_display": order.get("small_order_fee_display"),
        "small_order_fee_amount": order.get("small_order_fee_amount"),
        "regulatory_response_fee_display": order.get(
            "regulatory_response_fee_display"
        ),
        "regulatory_response_fee_amount": order.get(
            "regulatory_response_fee_amount"
        ),
        "fulfillment_type": order.get("fulfillment_type"),
        "tracking_url": order.get("tracking_url"),
        "help_url": order.get("help_url"),
        "dasher_name": order.get("dasher_name"),
        "delivery_address": order.get("delivery_address"),
        "delivery_address_lines": order.get("delivery_address_lines"),
        "delivery_instructions": order.get("delivery_instructions"),
        "dropoff_photo_url": order.get("dropoff_photo_url"),
        "items": order.get("items"),
        "item_count": order.get("item_count"),
        "confidence": order.get("confidence"),
        "source_order_id": order.get("source_order_id"),
        "status_candidates": order.get("status_candidates"),
        "money_candidates": order.get("money_candidates"),
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


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    """Build shared device metadata for DoorDash entities."""
    host = urlsplit(entry.data.get("tracking_url") or entry.data.get("base_url") or "").netloc
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"DoorDash Status ({host or 'DoorDash'})",
        manufacturer="DoorDash",
        model=host or "doordash.com",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=entry.data.get("tracking_url") or entry.data.get("base_url"),
    )


def _money_value(order: dict[str, Any] | None, prefix: str) -> str | None:
    """Return a stable string value for a money-like order field."""
    if order is None:
        return None

    display = order.get(f"{prefix}_display")
    if isinstance(display, str) and display.strip():
        return display.strip()

    amount = order.get(f"{prefix}_amount")
    if isinstance(amount, (int, float)):
        return f"${amount:.2f}"

    return None


def _uuid_value(order: dict[str, Any] | None) -> str | None:
    """Return the latest order UUID when one is available."""
    if order is None:
        return None
    source_order_id = order.get("source_order_id")
    return source_order_id if isinstance(source_order_id, str) and source_order_id.strip() else None


def _item_count_value(order: dict[str, Any] | None) -> int:
    """Return the best available item count for the latest order."""
    if order is None:
        return 0

    count = order.get("item_count")
    if isinstance(count, int) and count >= 0:
        return count

    items = order.get("items") or []
    if isinstance(items, list):
        return len(items)
    return 0


def _items(order: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return the order items as a list."""
    if order is None:
        return []
    items = order.get("items")
    return items if isinstance(items, list) else []


def _item_names(order: dict[str, Any] | None) -> list[str]:
    """Return the clean item names for an order."""
    names: list[str] = []
    for item in _items(order):
        name = item.get("name") if isinstance(item, dict) else None
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    return names


def _format_item_label(item: dict[str, Any], *, include_quantity: bool = True) -> str | None:
    """Return a compact human-readable label for a single order item."""
    name = item.get("name") if isinstance(item, dict) else None
    if not isinstance(name, str) or not name.strip():
        return None

    try:
        quantity = int(item.get("quantity", 1)) if isinstance(item, dict) else 1
    except (TypeError, ValueError):
        quantity = 1

    clean_name = name.strip()
    if include_quantity and quantity > 1:
        return f"{quantity}x {clean_name}"
    return clean_name


def _truncate_label_list(labels: list[str], *, fallback_count: int) -> str | None:
    """Join labels into a sensor-safe state string."""
    if not labels:
        return None

    joined = ", ".join(labels)
    if len(joined) <= 250:
        return joined

    preview = ", ".join(labels[:3])
    remaining = len(labels) - 3
    if remaining > 0:
        compact = f"{preview} +{remaining} more"
        if len(compact) <= 250:
            return compact

    return f"{fallback_count} items" if fallback_count else f"{len(labels)} items"


def _format_items_value(order: dict[str, Any] | None) -> str | None:
    """Return a stable state value for the latest items sensor."""
    labels = [
        label
        for label in (_format_item_label(item) for item in _items(order))
        if label is not None
    ]
    return _truncate_label_list(labels, fallback_count=_item_count_value(order))


def _format_item_names_value(order: dict[str, Any] | None) -> str | None:
    """Return a state value containing just the item names."""
    return _truncate_label_list(_item_names(order), fallback_count=_item_count_value(order))


def _first_item_value(order: dict[str, Any] | None) -> str | None:
    """Return the first item name for the order."""
    names = _item_names(order)
    return names[0] if names else None


def _dropoff_photo_state(order: dict[str, Any] | None) -> str | None:
    """Return a compact sensor state for the latest dropoff photo."""
    if order is None:
        return None
    if order.get("dropoff_photo_url"):
        return "Captured"
    return None


def _minutes_until_eta(order: dict[str, Any] | None) -> int | None:
    """Return the number of minutes until ETA, rounded up."""
    if order is None:
        return None

    eta_at = order.get("eta_at")
    if eta_at is None:
        return None

    delta_seconds = (eta_at - dt_util.utcnow()).total_seconds()
    if delta_seconds <= 0:
        return 0
    return int(ceil(delta_seconds / 60))


def _order_age_minutes(order: dict[str, Any] | None) -> int | None:
    """Return the age of the order in whole minutes."""
    if order is None:
        return None

    created_at = order.get("created_at")
    if created_at is None:
        return None

    age_seconds = (dt_util.utcnow() - created_at).total_seconds()
    if age_seconds <= 0:
        return 0
    return int(age_seconds // 60)


def _delivered_at_value(order: dict[str, Any] | None) -> Any:
    """Return a conservative delivered timestamp for the latest order."""
    if order is None or order.get("status") != "Delivered":
        return None

    delivered_at = order.get("delivered_at")
    if delivered_at is not None:
        return delivered_at

    if order.get("raw_status") and order.get("updated_at") is not None:
        return order.get("updated_at")

    return None


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
            "raw_status": data.latest_order.get("raw_status") if data.latest_order else None,
            "latest_order": _serialize_order(data.latest_order),
            "all_orders": _serialize_orders(data.orders),
        },
    ),
    DoorDashSensorDescription(
        key="latest_order_raw_status",
        name="Latest order raw status",
        icon="mdi:code-json",
        value_fn=lambda data: data.latest_order.get("raw_status") if data.latest_order else None,
        attrs_fn=lambda data: {
            "normalized_status": data.latest_order.get("status") if data.latest_order else None,
            "latest_order": _serialize_order(data.latest_order),
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
        key="latest_order_uuid",
        name="Latest order UUID",
        icon="mdi:identifier",
        value_fn=lambda data: _uuid_value(data.latest_order),
        attrs_fn=lambda data: {
            "order_detail_url": data.latest_order.get("order_detail_url") if data.latest_order else None,
            "latest_order": _serialize_order(data.latest_order),
        },
    ),
    DoorDashSensorDescription(
        key="latest_order_placed_at",
        name="Latest order placed at",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: data.latest_order.get("created_at") if data.latest_order else None,
        attrs_fn=lambda data: {
            "latest_order": _serialize_order(data.latest_order),
        },
    ),
    DoorDashSensorDescription(
        key="latest_order_updated_at",
        name="Latest order updated at",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: data.latest_order.get("updated_at") if data.latest_order else None,
        attrs_fn=lambda data: {
            "latest_order": _serialize_order(data.latest_order),
        },
    ),
    DoorDashSensorDescription(
        key="latest_order_delivered_at",
        name="Latest order delivered at",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: _delivered_at_value(data.latest_order),
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
        key="latest_order_fulfillment_type",
        name="Latest order fulfillment type",
        icon="mdi:shopping-outline",
        value_fn=lambda data: data.latest_order.get("fulfillment_type") if data.latest_order else None,
        attrs_fn=lambda data: {
            "latest_order": _serialize_order(data.latest_order),
        },
    ),
    DoorDashSensorDescription(
        key="latest_order_total",
        name="Latest order total",
        icon="mdi:currency-usd",
        value_fn=lambda data: _money_value(data.latest_order, "total"),
        attrs_fn=lambda data: {
            "total_amount": data.latest_order.get("total_amount") if data.latest_order else None,
            "latest_order": _serialize_order(data.latest_order),
        },
    ),
    DoorDashSensorDescription(
        key="latest_order_subtotal",
        name="Latest order subtotal",
        icon="mdi:cash-multiple",
        value_fn=lambda data: _money_value(data.latest_order, "subtotal"),
        attrs_fn=lambda data: {
            "subtotal_amount": data.latest_order.get("subtotal_amount") if data.latest_order else None,
            "latest_order": _serialize_order(data.latest_order),
        },
    ),
    DoorDashSensorDescription(
        key="latest_order_tip",
        name="Latest order tip",
        icon="mdi:hand-coin-outline",
        value_fn=lambda data: _money_value(data.latest_order, "tip"),
        attrs_fn=lambda data: {
            "tip_amount": data.latest_order.get("tip_amount") if data.latest_order else None,
            "latest_order": _serialize_order(data.latest_order),
        },
    ),
    DoorDashSensorDescription(
        key="latest_order_tax",
        name="Latest order tax",
        icon="mdi:receipt-text-outline",
        value_fn=lambda data: _money_value(data.latest_order, "tax"),
        attrs_fn=lambda data: {
            "tax_amount": data.latest_order.get("tax_amount") if data.latest_order else None,
            "latest_order": _serialize_order(data.latest_order),
        },
    ),
    DoorDashSensorDescription(
        key="latest_order_fees",
        name="Latest order fees",
        icon="mdi:cash-minus",
        value_fn=lambda data: _money_value(data.latest_order, "fees"),
        attrs_fn=lambda data: {
            "fees_amount": data.latest_order.get("fees_amount") if data.latest_order else None,
            "latest_order": _serialize_order(data.latest_order),
        },
    ),
    DoorDashSensorDescription(
        key="latest_order_item_count",
        name="Latest order item count",
        icon="mdi:cart-outline",
        value_fn=lambda data: _item_count_value(data.latest_order),
        attrs_fn=lambda data: {
            "items": _items(data.latest_order),
            "latest_order": _serialize_order(data.latest_order),
        },
    ),
    DoorDashSensorDescription(
        key="latest_order_items",
        name="Latest order items",
        icon="mdi:food-outline",
        value_fn=lambda data: _format_items_value(data.latest_order),
        attrs_fn=lambda data: {
            "items": _items(data.latest_order),
            "item_labels": [
                label
                for label in (
                    _format_item_label(item) for item in _items(data.latest_order)
                )
                if label
            ],
            "latest_order": _serialize_order(data.latest_order),
        },
    ),
    DoorDashSensorDescription(
        key="latest_order_item_names",
        name="Latest order item names",
        icon="mdi:format-list-bulleted",
        value_fn=lambda data: _format_item_names_value(data.latest_order),
        attrs_fn=lambda data: {
            "item_names": _item_names(data.latest_order),
            "latest_order": _serialize_order(data.latest_order),
        },
    ),
    DoorDashSensorDescription(
        key="latest_order_first_item",
        name="Latest order first item",
        icon="mdi:food-turkey",
        value_fn=lambda data: _first_item_value(data.latest_order),
        attrs_fn=lambda data: {
            "items": _items(data.latest_order),
            "latest_order": _serialize_order(data.latest_order),
        },
    ),
    DoorDashSensorDescription(
        key="latest_order_dropoff_photo",
        name="Latest order dropoff photo status",
        icon="mdi:camera-image",
        value_fn=lambda data: _dropoff_photo_state(data.latest_order),
        attrs_fn=lambda data: {
            "dropoff_photo_url": (
                data.latest_order.get("dropoff_photo_url")
                if data.latest_order
                else None
            ),
            "latest_order": _serialize_order(data.latest_order),
        },
    ),
    DoorDashSensorDescription(
        key="minutes_until_eta",
        name="Minutes until ETA",
        icon="mdi:timer-outline",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _minutes_until_eta(data.latest_order),
        attrs_fn=lambda data: {
            "eta_at": _serialize_datetime(data.latest_order.get("eta_at")) if data.latest_order else None,
            "latest_order": _serialize_order(data.latest_order),
        },
    ),
    DoorDashSensorDescription(
        key="order_age_minutes",
        name="Order age minutes",
        icon="mdi:timeline-clock-outline",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _order_age_minutes(data.latest_order),
        attrs_fn=lambda data: {
            "created_at": _serialize_datetime(data.latest_order.get("created_at")) if data.latest_order else None,
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
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> Any:
        """Return the current sensor state."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes for the sensor."""
        return self.entity_description.attrs_fn(self.coordinator.data)
