"""Image platform for DoorDash Status."""

from __future__ import annotations

from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DoorDashDataUpdateCoordinator
from .sensor import _device_info, _items, _serialize_order


def _latest_order(coordinator: DoorDashDataUpdateCoordinator) -> dict[str, Any] | None:
    """Return the latest order from the current snapshot."""
    return coordinator.data.latest_order


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up DoorDash image entities from a config entry."""
    coordinator: DoorDashDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    added_slots: set[int] = set()

    def _build_new_item_entities() -> list[DoorDashLatestOrderItemImage]:
        latest_order = _latest_order(coordinator)
        items = _items(latest_order)
        new_entities: list[DoorDashLatestOrderItemImage] = []
        for index in range(len(items)):
            slot = index + 1
            if slot in added_slots:
                continue
            added_slots.add(slot)
            new_entities.append(
                DoorDashLatestOrderItemImage(hass, coordinator, entry, index)
            )
        return new_entities

    entities: list[ImageEntity] = [
        DoorDashLatestOrderStoreImage(hass, coordinator, entry),
        DoorDashLatestOrderDropoffPhotoImage(hass, coordinator, entry),
    ]
    entities.extend(_build_new_item_entities())
    async_add_entities(entities)

    @callback
    def _handle_coordinator_update() -> None:
        new_entities = _build_new_item_entities()
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_handle_coordinator_update))


class DoorDashImageBase(CoordinatorEntity[DoorDashDataUpdateCoordinator], ImageEntity):
    """Base image entity backed by the DoorDash coordinator."""

    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: DoorDashDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the image entity."""
        ImageEntity.__init__(self, hass)
        CoordinatorEntity.__init__(self, coordinator)
        self._attr_device_info = _device_info(entry)

    @property
    def image_last_updated(self):
        """Return when the image should be considered refreshed."""
        latest_order = _latest_order(self.coordinator)
        if latest_order is not None:
            return latest_order.get("updated_at") or latest_order.get("created_at")
        return self.coordinator.last_update_success_time


class DoorDashLatestOrderStoreImage(DoorDashImageBase):
    """Image entity for the latest order's store image."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: DoorDashDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the store image entity."""
        super().__init__(hass, coordinator, entry)
        self._attr_name = "Latest order store image"
        self._attr_unique_id = f"{entry.entry_id}_latest_order_store_image"
        self._attr_icon = "mdi:storefront-outline"

    @property
    def available(self) -> bool:
        """Return whether the latest order currently exposes a store image."""
        latest_order = _latest_order(self.coordinator)
        return bool(latest_order and latest_order.get("store_image_url"))

    @property
    def image_url(self) -> str | None:
        """Return the store image URL."""
        latest_order = _latest_order(self.coordinator)
        if latest_order is None:
            return None
        return latest_order.get("store_image_url")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes for the latest store image."""
        latest_order = _latest_order(self.coordinator)
        return {
            "store_name": latest_order.get("store_name") if latest_order else None,
            "latest_order": _serialize_order(latest_order),
        }


class DoorDashLatestOrderDropoffPhotoImage(DoorDashImageBase):
    """Image entity for the latest order's dropoff photo."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: DoorDashDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the dropoff photo image entity."""
        super().__init__(hass, coordinator, entry)
        self._attr_name = "Latest order dropoff photo"
        self._attr_unique_id = f"{entry.entry_id}_latest_order_dropoff_photo"
        self._attr_icon = "mdi:camera-image"

    @property
    def available(self) -> bool:
        """Return whether the latest order currently exposes a dropoff photo."""
        latest_order = _latest_order(self.coordinator)
        return bool(latest_order and latest_order.get("dropoff_photo_url"))

    @property
    def image_url(self) -> str | None:
        """Return the dropoff photo URL."""
        latest_order = _latest_order(self.coordinator)
        if latest_order is None:
            return None
        return latest_order.get("dropoff_photo_url")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes for the latest dropoff photo."""
        latest_order = _latest_order(self.coordinator)
        return {
            "store_name": latest_order.get("store_name") if latest_order else None,
            "status": latest_order.get("status") if latest_order else None,
            "delivered_at": (
                latest_order.get("delivered_at").isoformat()
                if latest_order and latest_order.get("delivered_at") is not None
                else None
            ),
            "latest_order": _serialize_order(latest_order),
        }


class DoorDashLatestOrderItemImage(DoorDashImageBase):
    """Image entity for one latest-order item slot."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: DoorDashDataUpdateCoordinator,
        entry: ConfigEntry,
        index: int,
    ) -> None:
        """Initialize the item image entity."""
        super().__init__(hass, coordinator, entry)
        self._index = index
        slot = index + 1
        self._attr_name = f"Latest order item {slot} image"
        self._attr_unique_id = f"{entry.entry_id}_latest_order_item_{slot}_image"
        self._attr_icon = "mdi:food-outline"

    def _item(self) -> dict[str, Any] | None:
        """Return the current item occupying this slot."""
        latest_order = _latest_order(self.coordinator)
        items = _items(latest_order)
        if self._index >= len(items):
            return None
        item = items[self._index]
        return item if isinstance(item, dict) else None

    @property
    def available(self) -> bool:
        """Return whether this item slot currently exposes an image."""
        item = self._item()
        return bool(item and item.get("image_url"))

    @property
    def image_url(self) -> str | None:
        """Return the current image URL for this item slot."""
        item = self._item()
        if item is None:
            return None
        return item.get("image_url")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes for the latest item image."""
        item = self._item()
        latest_order = _latest_order(self.coordinator)
        return {
            "item_index": self._index + 1,
            "item_name": item.get("name") if item else None,
            "item_quantity": item.get("quantity") if item else None,
            "item_description": item.get("description") if item else None,
            "unit_price_display": item.get("unit_price_display") if item else None,
            "line_total_display": item.get("line_total_display") if item else None,
            "latest_order": _serialize_order(latest_order),
        }
