"""Generate golden binary fixtures matching the Dhan WebSocket protocol.

Each fixture is a complete feed-response packet (header + records).
Fixtures are byte-identical to what Dhan would send on the wire
for known security_ids under normal market conditions.
"""

import struct
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent

_LONG_MAX = 2**63 - 1
_INT_MAX = 2**31 - 1


def _write_fixture(name: str, data: bytes):
    path = FIXTURE_DIR / name
    path.write_bytes(data)
    print(f"  wrote {len(data)} bytes -> {name}")


def _build_packet(record_type: int, records_block: bytes) -> bytes:
    header = struct.pack("<II", record_type, len(records_block))
    return header + records_block


def generate_ticker():
    records = b""
    for sid in [1, 2, 3]:
        records += struct.pack("<IqiiI", sid, 152350_00, 1250000, 125, 1234567890)
    data = _build_packet(1, records)
    _write_fixture("golden_ticker.bin", data)


def generate_quote():
    records = b""
    for sid in [101, 102]:
        records += struct.pack(
            "<IqIIqqIIii",
            sid,
            152350_00, 1234567890, 5000,
            152300_00, 100,
            152400_00, 200,
            1250000, 125,
        )
    data = _build_packet(2, records)
    _write_fixture("golden_quote.bin", data)


def generate_full():
    FULL_FORMAT = "<IqIIqqIIqqqqqiiIIi" + "qi" * 10
    records = b""
    for sid in [201, 202]:
        values = [
            sid,  # security_id
            152350_00, 1234567890, 5000,  # ltp, ltt, ltv
            152300_00, 100,  # bid, bid_qty
            152400_00, 200,  # ask, ask_qty
            152000_00, 152500_00, 151500_00, 152200_00,  # open, high, low, close
            1250000, 125,  # oi, chg
            5000, 3000,  # tot_bid_qty, tot_ask_qty
            0, 0,  # pad, pad
        ]
        for i in range(10):
            values.append(152300_00 - i * 100)
            values.append(50 - i * 5)
        records += struct.pack(FULL_FORMAT, *values)
    data = _build_packet(3, records)
    _write_fixture("golden_full.bin", data)


def generate_malformed_truncated():
    data = b"\x01\x00\x00\x00"  # record_type=1, but no length/body
    _write_fixture("malformed_truncated.bin", data)


def generate_malformed_unknown_type():
    records = struct.pack("<IqiiI", 999, 100_00, 1000, 10, 1234567890)
    data = _build_packet(255, records)  # record_type=255 (invalid)
    _write_fixture("malformed_unknown_type.bin", data)


def generate_empty():
    """Valid header but zero records."""
    data = _build_packet(2, b"")
    _write_fixture("golden_empty_quote.bin", data)


def generate_full_200_odd():
    """A packet with FULL_200_DEPTH type code (4) but truncated depth."""
    FULL_FORMAT = "<IqIIqqIIqqqqqiiIIi" + "qi" * 10
    base = struct.pack(FULL_FORMAT, *([
        301, 152350_00, 1234567890, 5000,
        152300_00, 100, 152400_00, 200,
        152000_00, 152500_00, 151500_00, 152200_00,
        1250000, 125, 5000, 3000, 0, 0,
    ] + [152300_00 - i * 100 for i in range(10)] + [50 - i * 5 for i in range(10)]))
    data = _build_packet(4, base)
    _write_fixture("golden_full_200_odd.bin", data)


if __name__ == "__main__":
    print("Generating golden fixtures...")
    generate_ticker()
    generate_quote()
    generate_full()
    generate_empty()
    generate_malformed_truncated()
    generate_malformed_unknown_type()
    generate_full_200_odd()
    print("Done.")
