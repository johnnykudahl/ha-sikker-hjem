"""Protocol tests using frames captured from a real Sikker Hjem S7Pro.

These load the HA-independent modules directly (without importing the
integration's __init__.py), so they run with plain `pytest`.
"""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
import sys
import types

PKG = "sikker_hjem_under_test"
_pkg = types.ModuleType(PKG)
_pkg.__path__ = [
    str(Path(__file__).parent.parent / "custom_components" / "sikker_hjem")
]
sys.modules[PKG] = _pkg

dc09 = importlib.import_module(f"{PKG}.dc09")
cid = importlib.import_module(f"{PKG}.contact_id")
receiver_mod = importlib.import_module(f"{PKG}.receiver")

ARM = b'\n99F60047"ADM-CID"0019L0#1234[#1234|3401 01 001][IKeyboard ]_11:11:14,11-22-2026\r'
DISARM = b'\n76A30047"ADM-CID"0020L0#1234[#1234|1401 01 001][IKeyboard ]_11:11:24,11-22-2026\r'


def _frame(body: str) -> bytes:
    payload = body.encode("utf-8")
    return b"\n" + f"{dc09.crc16_arc(payload):04X}{len(payload):04X}".encode() + payload + b"\r"


def test_captured_frames_validate_and_parse() -> None:
    frame = dc09.parse_frame(ARM)
    assert frame.token == "ADM-CID"
    assert frame.sequence == "0019"
    assert frame.receiver is None
    assert frame.line == "0"
    assert frame.account == "1234"
    assert frame.info == "Keyboard"
    assert frame.timestamp.isoformat() == "2026-11-22T11:11:14+00:00"
    event = cid.parse_contact_id(frame.data)
    assert event.cid == "3401"
    assert (event.partition, event.zone) == ("01", "001")
    assert event.description == "Armed by user"

    event = cid.parse_contact_id(dc09.parse_frame(DISARM).data)
    assert event.cid == "1401"
    assert event.description == "Disarmed by user"


def test_ack_matches_the_one_the_panel_accepted() -> None:
    assert dc09.build_ack(dc09.parse_frame(ARM)) == b'\n85040014"ACK"0019R0L0#1234[]\r'
    assert dc09.build_ack(dc09.parse_frame(DISARM)) == b'\n5A1D0014"ACK"0020R0L0#1234[]\r'


def test_unpadded_crc_from_panel_accepted() -> None:
    # The S7Pro sends CRCs below 0x1000 without leading zeros (0x0EF0 -> "EF0").
    # Find an SOS frame (placeholder sensor name) whose CRC is < 0x1000.
    for seq in range(1, 10000):
        body = f'"ADM-CID"{seq:04d}L0#1234[#1234|1101 01 001][ISOS button]'.encode()
        if dc09.crc16_arc(body) < 0x1000:
            break
    raw = b"\n" + f"{dc09.crc16_arc(body):X}{len(body):04X}".encode() + body + b"\r"
    assert raw[1:8].count(b'"') == 0 and raw.index(b'"') < 9  # short header
    frame = dc09.parse_frame(raw)
    assert frame.sequence == f"{seq:04d}"
    assert frame.info == "SOS button"
    event = cid.parse_contact_id(frame.data)
    assert event.cid == "1101"
    assert event.description == "Personal emergency alarm"
    # A 3-digit CRC that is wrong must still be rejected.
    bad = b"\nABC" + f"{len(body):04X}".encode() + body + b"\r"
    try:
        dc09.parse_frame(bad)
    except dc09.DC09Error:
        pass
    else:
        raise AssertionError("bad short CRC accepted")


def test_corrupt_frames_rejected() -> None:
    for bad in (ARM.replace(b"3401", b"3402"), ARM.replace(b"0047", b"0048"), b"\nGARBAGE\r"):
        try:
            dc09.parse_frame(bad)
        except dc09.DC09Error:
            continue
        raise AssertionError(f"accepted {bad!r}")


def test_non_ascii_sensor_name() -> None:
    raw = _frame('"ADM-CID"0021L0#1234[#1234|1130 01 003][IFordør sensor]_11:12:00,11-22-2026')
    frame = dc09.parse_frame(raw)
    assert frame.info == "Fordør sensor"
    event = cid.parse_contact_id(frame.data)
    assert event.cid == "1130"
    assert event.description == "Burglary alarm"


def test_state_mapping() -> None:
    def ev(c: str) -> object:
        return cid.parse_contact_id(f"#1234|{c} 01 001")

    rs = cid.resolve_state
    assert rs(ev("3401"), "disarmed") == "armed_away"
    assert rs(ev("1401"), "armed_away") == "disarmed"
    assert rs(ev("1401"), "pending") == "disarmed"
    assert rs(ev("3441"), "disarmed") == "armed_home"
    # Non-delayed: burglary triggers immediately.
    assert rs(ev("1130"), "armed_away") == "triggered"
    # Restores (door closed) never change state.
    assert rs(ev("3130"), "triggered") is None
    assert rs(ev("3130"), "pending", delayed=True) is None
    assert rs(ev("3130"), "disarmed") is None
    # Trouble events do not change state.
    assert rs(ev("1301"), "armed_away") is None


def test_entry_delay_sequence_from_real_panel() -> None:
    """Armed -> 1130 door opened -> 3130 door closed -> 1401 tag, 10 s later."""
    def ev(c: str) -> object:
        return cid.parse_contact_id(f"#1234|{c} 01 001")

    rs = cid.resolve_state
    state = "armed_away"
    state = rs(ev("1130"), state, delayed=True) or state
    assert state == "pending"
    assert rs(ev("1130"), state, delayed=True) is None  # no restart of countdown
    state = rs(ev("3130"), state, delayed=True) or state
    assert state == "pending"
    state = rs(ev("1401"), state) or state
    assert state == "disarmed"
    # Delayed sensor while disarmed (e.g. 24h use) or fire/SOS: immediate.
    assert rs(ev("1130"), "disarmed", delayed=True) == "triggered"
    assert rs(ev("1101"), "armed_away", delayed=True) == "triggered"
    assert rs(ev("1110"), "pending", delayed=True) == "triggered"
    assert rs(ev("1130"), "triggered", delayed=True) is None


def test_overnight_s7pro_codes() -> None:
    # Hourly test report (real frame shape, no [I] block).
    frame = dc09.parse_frame(
        _frame('"ADM-CID"0063L0#1234[#1234|1602 01 001]_18:49:23,11-23-2026')
    )
    event = cid.parse_contact_id(frame.data)
    assert event.is_test and event.description == "Periodic test report"
    assert event.connectivity is None and frame.info is None
    assert cid.resolve_state(event, "disarmed") is None

    # RF supervision loss / restore for a named sensor (UTF-8 name).
    for code, connected, desc in (
        ("1381", False, "Loss of RF supervision"),
        ("3381", True, "Loss of RF supervision restore"),
    ):
        frame = dc09.parse_frame(
            _frame(f'"ADM-CID"0068L0#1234[#1234|{code} 01 001][IFordør sensor]')
        )
        event = cid.parse_contact_id(frame.data)
        assert frame.info == "Fordør sensor"
        assert event.connectivity is connected
        assert event.description == desc
        assert not event.is_test
        assert cid.resolve_state(event, "armed_away") is None


def test_receiver_end_to_end() -> None:
    async def run() -> tuple[list, list[bytes]]:
        received: list = []
        rx = receiver_mod.DC09Receiver("127.0.0.1", 0, received.append, account="1234")
        await rx.start()
        replies: list[bytes] = []
        reader, writer = await asyncio.open_connection("127.0.0.1", rx.port)
        for raw in (ARM, ARM, DISARM, _frame('"NULL"0022L0#1234[]'),
                    _frame('"ADM-CID"0023L0#9999[#9999|1130 01 001]'),
                    ARM.replace(b"3401", b"3402")):
            writer.write(raw)
            await writer.drain()
            replies.append(await reader.readuntil(b"\r"))
        writer.close()
        await rx.stop()
        return received, replies

    received, replies = asyncio.run(run())
    assert replies[0] == b'\n85040014"ACK"0019R0L0#1234[]\r'
    assert replies[1] == replies[0]  # retransmission is re-ACKed...
    assert replies[2] == b'\n5A1D0014"ACK"0020R0L0#1234[]\r'
    assert b'"ACK"0022' in replies[3]
    assert b'"DUH"0023' in replies[4]  # wrong account
    assert b'"NAK"0000' in replies[5]  # bad CRC
    # ...but only delivered once; NULL is delivered without an event.
    assert [m.event.cid if m.event else None for m in received] == ["3401", "1401", None]
    data = received[0].as_event_data()
    assert data["cid"] == "3401" and data["sensor"] == "Keyboard" and data["user"] == "001"


def test_siren_sequence_from_real_panel() -> None:
    """Real test 2026-11-24: arm, door open, door closed, siren after ~61 s."""
    def ev(c: str) -> object:
        return cid.parse_contact_id(f"#1234|{c} 01 001")

    rs = cid.resolve_state
    state = rs(ev("3401"), "disarmed")                       # 19:19:10 armed
    assert state == "armed_away"
    state = rs(ev("1130"), state, delayed=True)              # 19:19:14 door opened
    assert state == "pending"
    assert rs(ev("3130"), state, delayed=True) is None       # 19:19:40 door closed
    # 19:20:15 repeat 61.6 s after opening = siren started
    state = rs(ev("1130"), state, delayed=True, delay_elapsed=True)
    assert state == "triggered"
    assert rs(ev("1130"), state, delayed=True, delay_elapsed=True) is None  # 0096/0097
    assert rs(ev("1401"), state) == "disarmed"               # 19:20:23 disarmed
    # A repeat early in the delay (door reopened) must not trigger.
    assert rs(ev("1130"), "pending", delayed=True, delay_elapsed=False) is None


def test_two_digit_crc_from_real_panel() -> None:
    raw = (b'\n26004C"ADM-CID"0095L0#1234[#1234|1130 01 001]'
           b'[IFord\xc3\xb8r sensor]_18:20:39,11-24-2026\r')
    frame = dc09.parse_frame(raw)
    assert frame.sequence == "0095" and frame.info == "Fordør sensor"
