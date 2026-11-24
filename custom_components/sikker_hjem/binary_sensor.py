"""Per-sensor RF connectivity for Sikker Hjem.

The panel reports Contact-ID 1381 when it stops hearing a wireless sensor's
supervision signal and 3381 when it hears it again. The sensor is identified by
the name in the [I...] block (e.g. "Fordør sensor"). A connectivity entity is
created the first time a sensor reports, and recreated from the entity registry
on later starts.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import STATE_OFF, STATE_ON, EntityCategory, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import SikkerHjemConfigEntry
from .const import signal_message
from .entity import SikkerHjemEntity
from .receiver import ReceivedMessage

_KEY_PREFIX = "connectivity_"


def _sensor_name(message: ReceivedMessage) -> str:
    """Return the reporting sensor's name, falling back to its zone number."""
    if message.frame.info:
        return message.frame.info
    return f"Zone {int(message.event.zone)}" if message.event else "Unknown"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SikkerHjemConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up connectivity sensors (restored ones now, new ones on demand)."""
    known: set[str] = set()
    unique_prefix = f"{entry.entry_id}_{_KEY_PREFIX}"

    restored: list[SikkerHjemConnectivitySensor] = []
    registry = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg_entry.domain != Platform.BINARY_SENSOR:
            continue
        if not reg_entry.unique_id.startswith(unique_prefix):
            continue
        name = reg_entry.unique_id.removeprefix(unique_prefix)
        known.add(name)
        restored.append(SikkerHjemConnectivitySensor(entry, name, None))
    async_add_entities(restored)

    @callback
    def _async_discover(message: ReceivedMessage) -> None:
        if message.event is None or message.event.connectivity is None:
            return
        name = _sensor_name(message)
        if name in known:
            return  # the existing entity handles it
        known.add(name)
        async_add_entities(
            [SikkerHjemConnectivitySensor(entry, name, message.event.connectivity)]
        )

    entry.async_on_unload(
        async_dispatcher_connect(hass, signal_message(entry.entry_id), _async_discover)
    )


class SikkerHjemConnectivitySensor(SikkerHjemEntity, BinarySensorEntity, RestoreEntity):
    """On = the panel hears the wireless sensor, off = supervision lost."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "connectivity"

    def __init__(
        self, entry: SikkerHjemConfigEntry, sensor_name: str, initial: bool | None
    ) -> None:
        """Initialise the entity."""
        super().__init__(entry, f"{_KEY_PREFIX}{sensor_name}")
        self._sensor_name = sensor_name
        self._attr_translation_placeholders = {"sensor": sensor_name}
        self._attr_is_on = initial

    async def _async_restore(self) -> None:
        if self._attr_is_on is not None:
            return  # created from a live message
        if (last := await self.async_get_last_state()) is not None:
            if last.state in (STATE_ON, STATE_OFF):
                self._attr_is_on = last.state == STATE_ON

    @callback
    def _async_handle_message(self, message: ReceivedMessage) -> None:
        event = message.event
        if event is None or event.connectivity is None:
            return
        if _sensor_name(message) != self._sensor_name:
            return
        self._attr_is_on = event.connectivity
        self.async_write_ha_state()

