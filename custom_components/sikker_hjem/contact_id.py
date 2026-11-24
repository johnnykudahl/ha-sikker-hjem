"""Ademco Contact-ID event parsing, descriptions and alarm state mapping.

No Home Assistant imports so it can be unit tested on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

QUALIFIER_NEW = 1  # new event / opening (disarm)
QUALIFIER_RESTORE = 3  # restore / closing (arm)
QUALIFIER_STATUS = 6  # previously reported, still present

# State strings match homeassistant AlarmControlPanelState values.
STATE_DISARMED = "disarmed"
STATE_ARMED_AWAY = "armed_away"
STATE_ARMED_HOME = "armed_home"
STATE_TRIGGERED = "triggered"
STATE_PENDING = "pending"  # entry delay running

_CID_RE = re.compile(
    r"^\s*(?:#(?P<account>[0-9A-Fa-f]+))?\s*\|\s*"
    r"(?P<qualifier>[136])(?P<code>\d{3})\s+"
    r"(?P<partition>[0-9A-Fa-f]{2})\s+(?P<zone>[0-9A-Fa-f]{3})\s*$"
)

# Open/close codes: code -> (text when armed, text when disarmed)
OPEN_CLOSE_CODES: dict[str, tuple[str, str]] = {
    "400": ("Armed", "Disarmed"),
    "401": ("Armed by user", "Disarmed by user"),
    "403": ("Armed automatically", "Disarmed automatically"),
    "407": ("Armed remotely", "Disarmed remotely"),
    "408": ("Quick armed", "Disarmed"),
    "409": ("Armed by keyswitch", "Disarmed by keyswitch"),
    "441": ("Armed home (stay)", "Disarmed"),
    "442": ("Armed home (keyswitch)", "Disarmed by keyswitch"),
    "456": ("Partially armed", "Disarmed"),
}
ARM_HOME_CODES = frozenset({"441", "442", "456"})
TEST_CODES = frozenset({"602", "603"})
# Burglary codes that may come from an entry/exit (delayed) zone.
ENTRY_DELAY_CODES = frozenset({"130", "131", "132", "134"})
SUPERVISION_CODES = frozenset({"381"})  # loss of RF supervision (sensor offline)

# Common standard Contact-ID event codes (not exhaustive).
CID_NAMES: dict[str, str] = {
    "100": "Medical",
    "101": "Personal emergency",
    "110": "Fire",
    "120": "Panic",
    "121": "Duress",
    "122": "Silent panic",
    "123": "Audible panic",
    "130": "Burglary",
    "131": "Perimeter burglary",
    "132": "Interior burglary",
    "133": "24-hour burglary",
    "134": "Entry/exit burglary",
    "137": "Tamper",
    "139": "Verified burglary",
    "140": "General",
    "144": "Sensor tamper",
    "145": "Expansion module tamper",
    "150": "24-hour non-burglary",
    "151": "Gas detected",
    "154": "Water leak",
    "158": "High temperature",
    "159": "Low temperature",
    "162": "Carbon monoxide",
    "300": "System trouble",
    "301": "AC power loss",
    "302": "Low system battery",
    "305": "System reset",
    "311": "Battery missing",
    "321": "Siren trouble",
    "333": "Expansion module failure",
    "344": "RF jamming",
    "350": "Communication trouble",
    "351": "Telco line fault",
    "354": "Failure to communicate",
    "380": "Sensor trouble",
    "381": "Loss of RF supervision",
    "383": "Sensor tamper",
    "384": "RF sensor low battery",
    "406": "Cancel",
    "459": "Recent closing",
    "570": "Zone bypass",
    "602": "Periodic test report",
    "603": "Periodic RF test",
    "627": "Program mode entered",
    "628": "Program mode exited",
}


def is_alarm_code(code: str) -> bool:
    """Return True for the 1xx alarm range (medical, fire, panic, burglary...)."""
    return 100 <= int(code) <= 169


def describe(qualifier: int, code: str) -> str:
    """Return a human readable description of a Contact-ID event."""
    if code in OPEN_CLOSE_CODES:
        armed, disarmed = OPEN_CLOSE_CODES[code]
        return armed if qualifier == QUALIFIER_RESTORE else disarmed

    name = CID_NAMES.get(code, f"Contact-ID event {code}")
    if is_alarm_code(code):
        name = f"{name} alarm"
    if qualifier == QUALIFIER_RESTORE:
        return f"{name} restore"
    if qualifier == QUALIFIER_STATUS:
        return f"{name} (status)"
    return name


@dataclass(frozen=True, slots=True)
class ContactIdEvent:
    """A decoded Contact-ID event."""

    qualifier: int
    code: str
    partition: str
    zone: str  # user number for open/close events, zone number otherwise

    @property
    def cid(self) -> str:
        """Return qualifier + code, e.g. '3401' (the form used in automations)."""
        return f"{self.qualifier}{self.code}"

    @property
    def description(self) -> str:
        """Return a human readable description."""
        return describe(self.qualifier, self.code)

    @property
    def is_open_close(self) -> bool:
        """Return True for arm/disarm events."""
        return self.code in OPEN_CLOSE_CODES

    @property
    def is_test(self) -> bool:
        """Return True for periodic test / supervision reports (602, 603)."""
        return self.code in TEST_CODES

    @property
    def connectivity(self) -> bool | None:
        """Return False on RF supervision loss, True on restore, else None."""
        if self.code not in SUPERVISION_CODES:
            return None
        if self.qualifier == QUALIFIER_RESTORE:
            return True
        if self.qualifier in (QUALIFIER_NEW, QUALIFIER_STATUS):
            return False
        return None


def parse_contact_id(data: str) -> ContactIdEvent | None:
    """Parse the ADM-CID data block, e.g. '#1234|3401 01 001'."""
    match = _CID_RE.match(data)
    if match is None:
        return None
    return ContactIdEvent(
        qualifier=int(match["qualifier"]),
        code=match["code"],
        partition=match["partition"],
        zone=match["zone"],
    )


def resolve_state(
    event: ContactIdEvent,
    current: str | None,
    delayed: bool = False,
    delay_elapsed: bool = False,
) -> str | None:
    """Return the new alarm panel state for an event, or None for no change.

    The S7Pro sends a burglary alarm (1130) the moment a delayed door opens,
    i.e. when the entry delay *starts*, not when the siren sounds. With
    `delayed=True` such an event while armed gives `pending`; the caller turns
    it into `triggered` if no disarm arrives before the entry delay expires.

    When the entry delay expires and the siren starts, the S7Pro sends 1130
    again (seen 61.6 s after the door opened, then twice more within 1.5 s).
    A delayed burglary event while `pending` with `delay_elapsed=True` (the
    entry delay has nearly run out) therefore gives `triggered`.

    Alarm restores (3130) only mean the sensor returned to normal (e.g. the
    door was closed), so they never change the state: a triggered panel stays
    triggered until it is disarmed.
    """
    if event.is_open_close:
        if event.qualifier == QUALIFIER_RESTORE:
            return STATE_ARMED_HOME if event.code in ARM_HOME_CODES else STATE_ARMED_AWAY
        if event.qualifier == QUALIFIER_NEW:
            return STATE_DISARMED
        return None

    if is_alarm_code(event.code) and event.qualifier == QUALIFIER_NEW:
        if current == STATE_TRIGGERED:
            return None
        if delayed and event.code in ENTRY_DELAY_CODES:
            if current == STATE_PENDING:
                # Repeat at the end of the delay = siren started. Earlier
                # repeats (e.g. door reopened) keep the countdown running.
                return STATE_TRIGGERED if delay_elapsed else None
            if current in (STATE_ARMED_AWAY, STATE_ARMED_HOME):
                return STATE_PENDING
        return STATE_TRIGGERED
    return None
