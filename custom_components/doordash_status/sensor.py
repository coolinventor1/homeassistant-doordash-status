"""Sensor platform for DoorDash Status."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
import re
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
    """Serialize a normalized order for lightweight state attributes."""
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
        "store_name": order.get("store_name"),
        "eta_at": _serialize_datetime(order.get("eta_at")),
        "eta_text": order.get("eta_text"),
        "delivered_at": _serialize_datetime(order.get("delivered_at")),
        "updated_at": _serialize_datetime(order.get("updated_at")),
        "created_at": _serialize_datetime(order.get("created_at")),
        "total_display": order.get("total_display"),
        "subtotal_display": order.get("subtotal_display"),
        "tip_display": order.get("tip_display"),
        "tax_display": order.get("tax_display"),
        "fees_display": order.get("fees_display"),
        "fulfillment_type": order.get("fulfillment_type"),
        "tracking_url": order.get("tracking_url"),
        "help_url": order.get("help_url"),
        "dasher_name": order.get("dasher_name"),
        "delivery_address": order.get("delivery_address"),
        "delivery_instructions": order.get("delivery_instructions"),
        "payment_method": order.get("payment_method"),
        "has_dropoff_photo": bool(order.get("dropoff_photo_url")),
        "item_count": order.get("item_count"),
        "source_order_id": order.get("source_order_id"),
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


def _string_field(order: dict[str, Any] | None, key: str) -> str | None:
    """Return a clean string field from the latest order."""
    if order is None:
        return None
    value = order.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _card_last4_value(order: dict[str, Any] | None) -> str | None:
    """Return the last 4 digits of the saved payment method when available."""
    payment_method = _string_field(order, "payment_method")
    if payment_method is None:
        return None

    match = re.search(r"(\d{4})(?!.*\d)", payment_method)
    if match is None:
        return None
    return match.group(1)


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


def _serialize_items(order: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return a lightweight serialized item list for state attributes."""
    serialized: list[dict[str, Any]] = []
    for item in _items(order):
        if not isinstance(item, dict):
            continue
        serialized.append(
            {
                "name": item.get("name"),
                "quantity": item.get("quantity"),
                "description": item.get("description"),
                "unit_price_display": item.get("unit_price_display"),
                "line_total_display": item.get("line_total_display"),
            }
        )
    return serialized


def _serialize_item_previews(
    order: dict[str, Any] | None,
    *,
    limit: int = 6,
) -> list[dict[str, Any]]:
    """Return lightweight item preview data for a custom dashboard card."""
    previews: list[dict[str, Any]] = []
    for item in _items(order):
        if not isinstance(item, dict):
            continue
        image_url = item.get("image_url")
        name = item.get("name")
        if not isinstance(image_url, str) or not image_url.strip():
            continue
        previews.append(
            {
                "name": name.strip() if isinstance(name, str) and name.strip() else None,
                "image_url": image_url.strip(),
                "quantity": item.get("quantity"),
            }
        )
        if len(previews) >= limit:
            break
    return previews


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
        key="latest_order_summary",
        name="Latest order summary",
        icon="mdi:credit-card-outline",
        value_fn=lambda data: data.latest_order.get("store_name") if data.latest_order else None,
        attrs_fn=lambda data: {
            "store_name": data.latest_order.get("store_name") if data.latest_order else None,
            "store_image_url": data.latest_order.get("store_image_url") if data.latest_order else None,
            "total_display": data.latest_order.get("total_display") if data.latest_order else None,
            "status": data.latest_order.get("status") if data.latest_order else None,
            "raw_status": data.latest_order.get("raw_status") if data.latest_order else None,
            "item_count": _item_count_value(data.latest_order),
            "items_preview": _serialize_item_previews(data.latest_order),
            "order_detail_url": data.latest_order.get("order_detail_url") if data.latest_order else None,
            "tracking_url": data.latest_order.get("tracking_url") if data.latest_order else None,
            "updated_at": _serialize_datetime(data.latest_order.get("updated_at")) if data.latest_order else None,
            "created_at": _serialize_datetime(data.latest_order.get("created_at")) if data.latest_order else None,
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
        key="latest_order_address",
        name="Latest order address",
        icon="mdi:map-marker-outline",
        value_fn=lambda data: _string_field(data.latest_order, "delivery_address"),
        attrs_fn=lambda data: {
            "delivery_address_lines": (
                data.latest_order.get("delivery_address_lines")
                if data.latest_order
                else None
            ),
            "delivery_instructions": (
                data.latest_order.get("delivery_instructions")
                if data.latest_order
                else None
            ),
            "latest_order": _serialize_order(data.latest_order),
        },
    ),
    DoorDashSensorDescription(
        key="latest_order_left_at",
        name="Latest order left at",
        icon="mdi:door-open",
        value_fn=lambda data: _string_field(data.latest_order, "delivery_instructions"),
        attrs_fn=lambda data: {
            "delivery_address": (
                data.latest_order.get("delivery_address")
                if data.latest_order
                else None
            ),
            "has_dropoff_photo": bool(
                data.latest_order and data.latest_order.get("dropoff_photo_url")
            ),
            "latest_order": _serialize_order(data.latest_order),
        },
    ),
    DoorDashSensorDescription(
        key="latest_order_card_last_4",
        name="Latest order card last 4",
        icon="mdi:credit-card-outline",
        value_fn=lambda data: _card_last4_value(data.latest_order),
        attrs_fn=lambda data: {
            "payment_method": (
                data.latest_order.get("payment_method")
                if data.latest_order
                else None
            ),
            "payment_time": _serialize_datetime(
                data.latest_order.get("payment_time")
            )
            if data.latest_order
            else None,
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
            "items": _serialize_items(data.latest_order),
            "latest_order": _serialize_order(data.latest_order),
        },
    ),
    DoorDashSensorDescription(
        key="latest_order_items",
        name="Latest order items",
        icon="mdi:food-outline",
        value_fn=lambda data: _format_items_value(data.latest_order),
        attrs_fn=lambda data: {
            "items": _serialize_items(data.latest_order),
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
            "items": _serialize_items(data.latest_order),
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
