"""SIA DC-09 transport layer helpers.

Frame layout (as sent by the Sikker Hjem S7Pro):

    <LF><CRC:4 hex><LEN:4 hex>"<TOKEN>"<SEQ:4>[R<rcv>]L<line>#<acct>[<data>][<ext>]..._HH:MM:SS,MM-DD-YYYY<CR>

The CRC is CRC-16/ARC and, together with the length, covers everything from the
first quote up to (not including) the trailing CR.

This module has no Home Assistant imports so it can be unit tested on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import re

FRAME_START = b"\n"
FRAME_END = b"\r"

_HEX4 = re.compile(rb"[0-9A-Fa-f]{4}")
_CRC_RE = re.compile(rb"[0-9A-Fa-f]{1,4}")
_HEADER_RE = re.compile(
    r'^"(?P<encrypted>\*?)(?P<token>[A-Z0-9-]+)"'
    r"(?P<sequence>\d{4})"
    r"(?:R(?P<receiver>[0-9A-Fa-f]{1,6}))?"
    r"L(?P<line>[0-9A-Fa-f]{1,6})"
    r"#(?P<account>[0-9A-Fa-f]{3,16})"
    r"(?P<rest>.*)$",
    re.DOTALL,
)
_BLOCK_RE = re.compile(r"\[([^\]]*)\]")
_TIMESTAMP_RE = re.compile(r"^_(\d{2}):(\d{2}):(\d{2}),(\d{2})-(\d{2})-(\d{4})$")


class DC09Error(ValueError):
    """Raised when a frame fails CRC/length checks or has no valid header."""


def crc16_arc(data: bytes) -> int:
    """Return the CRC-16/ARC checksum (poly 0xA001 reflected, init 0)."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def _decode(body: bytes) -> str:
    """Decode frame text; sensor names may contain non-ASCII (e.g. 'Fordør')."""
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("latin-1")


@dataclass(frozen=True, slots=True)
class DC09Frame:
    """A validated DC-09 frame."""

    token: str
    sequence: str
    receiver: str | None
    line: str
    account: str
    encrypted: bool
    data: str | None
    extended: dict[str, str] = field(default_factory=dict)
    timestamp: datetime | None = None

    @property
    def info(self) -> str | None:
        """Return the free-text info block ([I...]), e.g. the sensor name."""
        value = self.extended.get("I", "").strip()
        return value or None


def parse_frame(raw: bytes) -> DC09Frame:
    """Validate and parse one raw DC-09 frame (with or without LF/CR)."""
    frame = raw.strip(b"\r\n\x00")
    if len(frame) < 9:
        raise DC09Error("frame too short")

    # The header is everything before the first quote: CRC then 4-digit length.
    # The S7Pro does not zero-pad the CRC (0x0EF0 is sent as "EF0"), so the CRC
    # field is 1-4 hex digits and the length is always the last 4 characters.
    quote = frame.find(b'"')
    if quote < 5 or quote > 8:
        raise DC09Error("invalid CRC/length header")
    crc_field, length_field, body = frame[: quote - 4], frame[quote - 4 : quote], frame[quote:]
    if not _CRC_RE.fullmatch(crc_field) or not _HEX4.fullmatch(length_field):
        raise DC09Error("invalid CRC/length header")
    if int(length_field, 16) != len(body):
        raise DC09Error(
            f"length mismatch (header {int(length_field, 16)}, actual {len(body)})"
        )
    if int(crc_field, 16) != crc16_arc(body):
        raise DC09Error("CRC mismatch")

    match = _HEADER_RE.match(_decode(body))
    if match is None:
        raise DC09Error("unrecognised message header")

    encrypted = bool(match["encrypted"])
    rest = match["rest"]
    blocks: list[str] = []
    timestamp: datetime | None = None

    if not encrypted:
        pos = 0
        while (block := _BLOCK_RE.match(rest, pos)) is not None:
            blocks.append(block.group(1))
            pos = block.end()
        if (ts := _TIMESTAMP_RE.match(rest[pos:].strip())) is not None:
            hour, minute, second, month, day, year = map(int, ts.groups())
            try:
                # DC-09 timestamps are UTC.
                timestamp = datetime(year, month, day, hour, minute, second, tzinfo=UTC)
            except ValueError:
                timestamp = None

    return DC09Frame(
        token=match["token"],
        sequence=match["sequence"],
        receiver=match["receiver"],
        line=match["line"],
        account=match["account"],
        encrypted=encrypted,
        data=blocks[0] if blocks else None,
        extended={b[0]: b[1:] for b in blocks[1:] if b},
        timestamp=timestamp,
    )


def _wrap(body: str) -> bytes:
    payload = body.encode("ascii")
    header = f"{crc16_arc(payload):04X}{len(payload):04X}".encode("ascii")
    return FRAME_START + header + payload + FRAME_END


def _reply(token: str, frame: DC09Frame) -> bytes:
    return _wrap(
        f'"{token}"{frame.sequence}R{frame.receiver or "0"}L{frame.line}#{frame.account}[]'
    )


def build_ack(frame: DC09Frame) -> bytes:
    """Positive acknowledgement - the panel stops retransmitting."""
    return _reply("ACK", frame)


def build_duh(frame: DC09Frame) -> bytes:
    """'Unable to process' - frame was intact but its content is not supported."""
    return _reply("DUH", frame)


def build_nak(now: datetime) -> bytes:
    """Negative acknowledgement for a corrupt frame (panel may retransmit)."""
    return _wrap(f'"NAK"0000R0L0A0[]{now.astimezone(UTC):_%H:%M:%S,%m-%d-%Y}')
