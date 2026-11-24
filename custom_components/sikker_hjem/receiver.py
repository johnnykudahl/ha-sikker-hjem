"""Asyncio TCP receiver ("monitoring station") for SIA DC-09 / ADM-CID.

No Home Assistant imports: messages are handed to a plain callback that runs
in the event loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
import time
from typing import Any

from .contact_id import ContactIdEvent, parse_contact_id
from .dc09 import (
    FRAME_END,
    DC09Error,
    DC09Frame,
    build_ack,
    build_duh,
    build_nak,
    parse_frame,
)

_LOGGER = logging.getLogger(__name__)

IDLE_TIMEOUT = 30  # seconds a panel connection may stay idle
MAX_FRAME_SIZE = 2048
DUPLICATE_WINDOW = 120  # seconds to remember (account, sequence, data)


@dataclass(frozen=True, slots=True)
class ReceivedMessage:
    """A message accepted from the panel."""

    frame: DC09Frame
    event: ContactIdEvent | None  # None for NULL (supervision) messages
    peer: str
    received_at: datetime

    def as_event_data(self) -> dict[str, Any]:
        """Return the payload used for the `sikker_hjem_event` bus event."""
        event = self.event
        return {
            "cid": event.cid if event else None,
            "code": event.code if event else None,
            "qualifier": event.qualifier if event else None,
            "description": event.description if event else None,
            "sensor": self.frame.info,
            "partition": event.partition if event else None,
            "user": event.zone if event else None,
            "zone": event.zone if event else None,
            "account": self.frame.account,
            "sequence": self.frame.sequence,
            "panel_time": (
                self.frame.timestamp.isoformat() if self.frame.timestamp else None
            ),
        }


class DC09Receiver:
    """Listen for DC-09 frames and acknowledge them."""

    def __init__(
        self,
        host: str | None,
        port: int,
        on_message: Callable[[ReceivedMessage], None],
        account: str | None = None,
    ) -> None:
        """Initialise. `account` (optional) restricts accepted account numbers."""
        self._host = host
        self._port = port
        self._on_message = on_message
        self._account = account.upper() if account else None
        self._server: asyncio.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._recent: dict[tuple[str, str, str], float] = {}

    @property
    def port(self) -> int:
        """Return the bound port (useful when started with port 0)."""
        if self._server and self._server.sockets:
            return self._server.sockets[0].getsockname()[1]
        return self._port

    async def start(self) -> None:
        """Start listening. Raises OSError if the port cannot be bound."""
        self._server = await asyncio.start_server(
            self._handle_client, self._host, self._port, limit=MAX_FRAME_SIZE
        )
        _LOGGER.debug("Listening for DC-09 messages on port %s", self.port)

    async def stop(self) -> None:
        """Stop listening and close open panel connections."""
        if self._server is None:
            return
        self._server.close()
        for writer in list(self._writers):
            writer.close()
        with suppress(OSError):
            await self._server.wait_closed()
        self._server = None

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peername = writer.get_extra_info("peername")
        peer = f"{peername[0]}:{peername[1]}" if peername else "unknown"
        self._writers.add(writer)
        _LOGGER.debug("Connection from %s", peer)
        try:
            while True:
                try:
                    raw = await asyncio.wait_for(
                        reader.readuntil(FRAME_END), IDLE_TIMEOUT
                    )
                except (asyncio.IncompleteReadError, TimeoutError):
                    break
                except asyncio.LimitOverrunError:
                    _LOGGER.warning("Oversized frame from %s, closing", peer)
                    break
                reply = self.handle_frame(raw, peer)
                if reply:
                    _LOGGER.debug("Reply to %s: %r", peer, reply)
                    writer.write(reply)
                    await writer.drain()
        except ConnectionError:
            pass
        finally:
            self._writers.discard(writer)
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()

    def handle_frame(self, raw: bytes, peer: str) -> bytes | None:
        """Process one raw frame and return the reply to send."""
        _LOGGER.debug("Frame from %s: %r", peer, raw)
        try:
            frame = parse_frame(raw)
        except DC09Error as err:
            _LOGGER.warning("Rejected frame from %s: %s", peer, err)
            return build_nak(datetime.now(UTC))

        if frame.encrypted:
            _LOGGER.warning(
                "Encrypted DC-09 frame from %s is not supported; "
                "disable encryption for this receiver in the panel",
                peer,
            )
            return build_duh(frame)

        if self._account and frame.account.upper() != self._account:
            _LOGGER.warning(
                "Ignoring frame from %s for unexpected account %s",
                peer,
                frame.account,
            )
            return build_duh(frame)

        if frame.token == "NULL":  # supervision / link test
            self._deliver(frame, None, peer)
            return build_ack(frame)

        if frame.token != "ADM-CID":
            _LOGGER.warning("Unsupported DC-09 format %s from %s", frame.token, peer)
            return build_duh(frame)

        event = parse_contact_id(frame.data) if frame.data is not None else None
        if event is None:
            _LOGGER.warning("Could not parse Contact-ID data %r", frame.data)
            return build_duh(frame)

        if self._is_duplicate(frame):
            _LOGGER.debug("Duplicate of sequence %s, re-acknowledging", frame.sequence)
            return build_ack(frame)

        self._deliver(frame, event, peer)
        return build_ack(frame)

    def _is_duplicate(self, frame: DC09Frame) -> bool:
        now = time.monotonic()
        self._recent = {
            k: t for k, t in self._recent.items() if now - t < DUPLICATE_WINDOW
        }
        key = (frame.account, frame.sequence, frame.data or "")
        if key in self._recent:
            return True
        self._recent[key] = now
        return False

    def _deliver(
        self, frame: DC09Frame, event: ContactIdEvent | None, peer: str
    ) -> None:
        message = ReceivedMessage(frame, event, peer, datetime.now(UTC))
        try:
            self._on_message(message)
        except Exception:  # Never let a consumer bug block the panel's ACK
            _LOGGER.exception("Error handling message from %s", peer)
