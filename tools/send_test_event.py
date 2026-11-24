#!/usr/bin/env python3
"""Send a simulated Sikker Hjem DC-09 / ADM-CID event to Home Assistant.

Lets you test automations without triggering the real alarm, e.g.:

    python3 send_test_event.py 192.168.1.10 --cid 1130 --sensor "Fordør sensor"
    python3 send_test_event.py 192.168.1.10 --cid 3130 --sensor "Fordør sensor"
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import random
import socket


def crc16_arc(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("host", help="Home Assistant IP address")
    parser.add_argument("--port", type=int, default=5555)
    parser.add_argument("--account", default="1234")
    parser.add_argument("--cid", default="1130", help="qualifier+code, e.g. 3401, 1401, 1130")
    parser.add_argument("--partition", default="01")
    parser.add_argument("--zone", default="001", help="zone or user number")
    parser.add_argument("--sensor", default="Test sensor")
    parser.add_argument("--seq", type=int, default=random.randint(1, 9999))
    args = parser.parse_args()

    now = datetime.now(UTC)
    body = (
        f'"ADM-CID"{args.seq:04d}L0#{args.account}'
        f"[#{args.account}|{args.cid} {args.partition} {args.zone}]"
        f"[I{args.sensor}]{now:_%H:%M:%S,%m-%d-%Y}"
    ).encode("utf-8")
    frame = b"\n" + f"{crc16_arc(body):04X}{len(body):04X}".encode() + body + b"\r"

    with socket.create_connection((args.host, args.port), timeout=5) as sock:
        print(f"Sent:  {frame!r}")
        sock.sendall(frame)
        print(f"Reply: {sock.recv(1024)!r}")


if __name__ == "__main__":
    main()
