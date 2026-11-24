"""Base entity for the Sikker Hjem integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, MANUFACTURER, MODEL, signal_message
from .receiver import ReceivedMessage


class SikkerHjemEntity(Entity):
    """Push-updated entity fed by the DC-09 receiver."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, key: str) -> None:
        """Initialise the entity."""
        self._entry_id = entry.entry_id
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Sikker Hjem",
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    async def async_added_to_hass(self) -> None:
        """Restore state, then subscribe to receiver messages."""
        await super().async_added_to_hass()
        await self._async_restore()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_message(self._entry_id), self._async_handle_message
            )
        )

    async def _async_restore(self) -> None:
        """Restore previous state (override in subclasses)."""

    @callback
    def _async_handle_message(self, message: ReceivedMessage) -> None:
        """Handle a message from the panel."""
        raise NotImplementedError
