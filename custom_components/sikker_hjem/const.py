"""Constants for the Sikker Hjem integration."""

from __future__ import annotations

DOMAIN = "sikker_hjem"

CONF_ACCOUNT = "account"
DEFAULT_PORT = 5555

# Options
CONF_ENTRY_DELAY = "entry_delay"
DEFAULT_ENTRY_DELAY = 60  # seconds; match the panel's entry delay, 0 = off
CONF_DELAYED_SENSORS = "delayed_sensors"  # comma separated names; empty = none (all instant)
LISTEN_HOST: str | None = None  # all interfaces (IPv4 and IPv6)

EVENT_TYPE = f"{DOMAIN}_event"

MANUFACTURER = "Sikker Hjem"
MODEL = "S7Pro"


def signal_message(entry_id: str) -> str:
    """Return the dispatcher signal for messages of one config entry."""
    return f"{DOMAIN}_message_{entry_id}"
