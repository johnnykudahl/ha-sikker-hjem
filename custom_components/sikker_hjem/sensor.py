"""Sensors for the Sikker Hjem integration."""

from __future__ import annotations

from homeassistant.components.sensor import RestoreSensor, SensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SikkerHjemConfigEntry
from .entity import SikkerHjemEntity
from .receiver import ReceivedMessage

_EVENT_ATTRS = (
    "cid",
    "code",
    "qualifier",
    "sensor",
    "partition",
    "user",
    "zone",
    "account",
    "sequence",
    "panel_time",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SikkerHjemConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors."""
    async_add_entities(
        [SikkerHjemLastEventSensor(entry), SikkerHjemLastMessageSensor(entry)]
    )


class SikkerHjemLastEventSensor(SikkerHjemEntity, RestoreSensor):
    """Description of the most recent Contact-ID event (test reports excluded)."""

    _attr_translation_key = "last_event"

    def __init__(self, entry: SikkerHjemConfigEntry) -> None:
        """Initialise the sensor."""
        super().__init__(entry, "last_event")
        self._attr_extra_state_attributes = {}

    async def _async_restore(self) -> None:
        if (data := await self.async_get_last_sensor_data()) is not None:
            self._attr_native_value = data.native_value
        if (state := await self.async_get_last_state()) is not None:
            self._attr_extra_state_attributes = {
                k: state.attributes[k] for k in _EVENT_ATTRS if k in state.attributes
            }

    @callback
    def _async_handle_message(self, message: ReceivedMessage) -> None:
        # Hourly test reports would otherwise hide the last real event.
        if message.event is None or message.event.is_test:
            return
        data = message.as_event_data()
        self._attr_native_value = data.pop("description")
        self._attr_extra_state_attributes = data
        self.async_write_ha_state()


class SikkerHjemLastMessageSensor(SikkerHjemEntity, RestoreSensor):
    """Time of the last message (event or supervision) from the panel."""

    _attr_translation_key = "last_message"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: SikkerHjemConfigEntry) -> None:
        """Initialise the sensor."""
        super().__init__(entry, "last_message")

    async def _async_restore(self) -> None:
        if (data := await self.async_get_last_sensor_data()) is not None:
            self._attr_native_value = data.native_value

    @callback
    def _async_handle_message(self, message: ReceivedMessage) -> None:
        self._attr_native_value = message.received_at
        self.async_write_ha_state()
