"""The Sikker Hjem alarm integration (local SIA DC-09 / Contact-ID receiver)."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PORT, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import CONF_ACCOUNT, DOMAIN, EVENT_TYPE, LISTEN_HOST, signal_message
from .receiver import DC09Receiver, ReceivedMessage

PLATFORMS: list[Platform] = [
    Platform.ALARM_CONTROL_PANEL,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
]

type SikkerHjemConfigEntry = ConfigEntry[DC09Receiver]


async def async_setup_entry(hass: HomeAssistant, entry: SikkerHjemConfigEntry) -> bool:
    """Start the DC-09 receiver and set up entities."""
    signal = signal_message(entry.entry_id)
    port: int = entry.data[CONF_PORT]

    @callback
    def _async_on_message(message: ReceivedMessage) -> None:
        if message.event is not None:
            hass.bus.async_fire(EVENT_TYPE, message.as_event_data())
        async_dispatcher_send(hass, signal, message)

    receiver = DC09Receiver(
        LISTEN_HOST,
        port,
        _async_on_message,
        account=entry.data.get(CONF_ACCOUNT) or None,
    )
    try:
        await receiver.start()
    except OSError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_listen",
            translation_placeholders={"port": str(port), "error": str(err)},
        ) from err

    entry.async_on_unload(receiver.stop)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    entry.runtime_data = receiver
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: SikkerHjemConfigEntry
) -> None:
    """Reload so the panel picks up new entry-delay options."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: SikkerHjemConfigEntry) -> bool:
    """Unload a config entry (the receiver is stopped via async_on_unload)."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
