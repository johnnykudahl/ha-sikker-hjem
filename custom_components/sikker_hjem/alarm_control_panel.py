"""Read-only alarm control panel for Sikker Hjem."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import time

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.restore_state import RestoreEntity

from . import SikkerHjemConfigEntry
from .const import (
    CONF_DELAYED_SENSORS,
    CONF_ENTRY_DELAY,
    DEFAULT_ENTRY_DELAY,
    DOMAIN,
)
from .contact_id import resolve_state
from .entity import SikkerHjemEntity
from .receiver import ReceivedMessage

_ATTRS = ("last_event", "last_cid", "sensor", "user", "partition")

# The panel's own "siren started" 1130 arrives ~1.6 s after a 60 s delay.
# Accept it from this many seconds before the configured delay ends ...
CONFIRM_WINDOW = 10
# ... and only fall back to the timer this long after the delay ends, so a
# disarm right at the end of the delay does not raise a false alarm.
FALLBACK_GRACE = 5


def parse_sensor_list(value: str | None) -> frozenset[str]:
    """Parse a comma separated list of sensor names (case-insensitive)."""
    return frozenset(
        name.strip().casefold() for name in (value or "").split(",") if name.strip()
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SikkerHjemConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the alarm panel."""
    async_add_entities([SikkerHjemAlarmPanel(entry)])


class SikkerHjemAlarmPanel(SikkerHjemEntity, AlarmControlPanelEntity, RestoreEntity):
    """Alarm state reported by the panel. Arming/disarming is done on the panel.

    A burglary alarm from a delayed sensor while armed gives `pending` (entry
    delay running) and becomes `triggered` only if the panel is not disarmed
    within the configured entry delay.
    """

    _attr_name = None  # use the device name -> alarm_control_panel.sikker_hjem
    _attr_code_arm_required = False
    _attr_code_format = None
    _attr_supported_features = AlarmControlPanelEntityFeature(0)

    def __init__(self, entry: SikkerHjemConfigEntry) -> None:
        """Initialise the panel."""
        super().__init__(entry, "panel")
        self._entry_delay: int = entry.options.get(CONF_ENTRY_DELAY, DEFAULT_ENTRY_DELAY)
        self._delayed_sensors = parse_sensor_list(
            entry.options.get(CONF_DELAYED_SENSORS)
        )
        self._attr_alarm_state: AlarmControlPanelState | None = None
        self._attr_extra_state_attributes = {}
        self._cancel_entry_timer: Callable[[], None] | None = None
        self._pending_since: float | None = None

    async def _async_restore(self) -> None:
        if (last := await self.async_get_last_state()) is None:
            return
        try:
            state = AlarmControlPanelState(last.state)
        except ValueError:
            return
        if state == AlarmControlPanelState.PENDING:
            # Restarted during an entry delay: the countdown is lost. Assume the
            # panel is still armed rather than raising a false alarm.
            state = AlarmControlPanelState.ARMED_AWAY
        self._attr_alarm_state = state
        self._attr_changed_by = last.attributes.get("changed_by")
        self._attr_extra_state_attributes = {
            k: last.attributes[k] for k in _ATTRS if k in last.attributes
        }

    async def async_will_remove_from_hass(self) -> None:
        """Cancel a running entry-delay timer."""
        self._cancel_timer()
        await super().async_will_remove_from_hass()

    def _is_delayed(self, sensor: str | None) -> bool:
        # Only sensors listed in the options are delayed (e.g. the front door).
        # Everything else - and everything if the list is empty - is instant,
        # which is the standard Contact-ID meaning of a burglary alarm.
        if self._entry_delay <= 0 or sensor is None:
            return False
        return sensor.casefold() in self._delayed_sensors

    def _cancel_timer(self) -> None:
        if self._cancel_entry_timer is not None:
            self._cancel_entry_timer()
            self._cancel_entry_timer = None

    @callback
    def _async_entry_delay_expired(self, _now: datetime) -> None:
        self._cancel_entry_timer = None
        self._pending_since = None
        if self._attr_alarm_state == AlarmControlPanelState.PENDING:
            self._attr_alarm_state = AlarmControlPanelState.TRIGGERED
            self.async_write_ha_state()

    @callback
    def _async_handle_message(self, message: ReceivedMessage) -> None:
        event = message.event
        if event is None:
            return
        sensor = message.frame.info
        current = self._attr_alarm_state.value if self._attr_alarm_state else None
        delay_elapsed = (
            self._pending_since is not None
            and time.monotonic() - self._pending_since
            >= self._entry_delay - CONFIRM_WINDOW
        )
        new = resolve_state(
            event,
            current,
            delayed=self._is_delayed(sensor),
            delay_elapsed=delay_elapsed,
        )
        if new is None:
            return

        state = AlarmControlPanelState(new)
        if state != AlarmControlPanelState.PENDING:
            self._cancel_timer()
            self._pending_since = None
        elif self._cancel_entry_timer is None:
            self._pending_since = time.monotonic()
            # Fallback only: normally the panel's own repeated 1130 triggers.
            self._cancel_entry_timer = async_call_later(
                self.hass,
                self._entry_delay + FALLBACK_GRACE,
                self._async_entry_delay_expired,
            )
        self._attr_alarm_state = state

        if event.is_open_close:
            who = f"User {int(event.zone)}"
            self._attr_changed_by = f"{who} ({sensor})" if sensor else who
        else:
            self._attr_changed_by = sensor or f"Zone {int(event.zone)}"
        self._attr_extra_state_attributes = {
            "last_event": event.description,
            "last_cid": event.cid,
            "sensor": sensor,
            "user": event.zone,
            "partition": event.partition,
        }
        self.async_write_ha_state()

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        """Disarming from Home Assistant is not supported by the DC-09 link."""
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="read_only"
        )
