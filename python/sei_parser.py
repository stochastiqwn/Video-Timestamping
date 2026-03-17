"""
H.264 SEI timestamp parser — pure Python implementation.

Parses H.264 byte-stream (Annex B) data to find SEI User Data Unregistered
NAL units containing absolute timestamps (UTC nanoseconds since Unix epoch).

This module is used by both the RTSP and WHEP Python receivers, and can also
be used standalone for testing or integration with other tools.
"""

import struct
from datetime import datetime, timezone
from typing import Iterator

# Must match the UUID in the Rust plugin (sei.rs)
TIMESTAMP_UUID = bytes(
    [0xA1, 0xB2, 0xC3, 0xD4, 0xE5, 0xF6, 0x47, 0x89,
     0xAB, 0xCD, 0xEF, 0x01, 0x23, 0x45, 0x67, 0x89]
)

NAL_TYPE_SEI = 6


def find_start_codes(data: bytes) -> Iterator[tuple[int, int]]:
    """Yield (offset_after_start_code, start_code_length) for each start code in data."""
    i = 0
    n = len(data)
    while i < n - 2:
        if data[i] == 0 and data[i + 1] == 0:
            if data[i + 2] == 1:
                yield (i + 3, 3)
                i += 3
                continue
            if i + 3 < n and data[i + 2] == 0 and data[i + 3] == 1:
                yield (i + 4, 4)
                i += 4
                continue
        i += 1


def iter_nalus(data: bytes) -> Iterator[tuple[int, bytes]]:
    """Iterate over NAL units in byte-stream format (lazy).

    Yields (nalu_start_offset, nalu_bytes_without_start_code).
    Only scans ahead to the next start code on each iteration,
    so callers that break early avoid scanning the rest of the buffer.
    """
    sc_iter = find_start_codes(data)
    prev = next(sc_iter, None)
    if prev is None:
        return

    for nalu_start, sc_len in sc_iter:
        prev_start, _ = prev
        # End of previous NAL is at the start of this start code
        nalu_end = nalu_start - sc_len
        yield prev_start, data[prev_start:nalu_end]
        prev = (nalu_start, sc_len)

    # Last NAL unit extends to end of data
    prev_start, _ = prev
    yield prev_start, data[prev_start:]


def parse_sei_timestamp(sei_payload: bytes) -> int | None:
    """Parse an SEI NAL unit payload (after the NAL header byte).

    Returns the timestamp in nanoseconds if our UUID is found, else None.
    """
    offset = 0
    n = len(sei_payload)

    while offset < n:
        # Parse payload type
        payload_type = 0
        while offset < n and sei_payload[offset] == 0xFF:
            payload_type += 255
            offset += 1
        if offset >= n:
            break
        payload_type += sei_payload[offset]
        offset += 1

        # Parse payload size
        payload_size = 0
        while offset < n and sei_payload[offset] == 0xFF:
            payload_size += 255
            offset += 1
        if offset >= n:
            break
        payload_size += sei_payload[offset]
        offset += 1

        payload_end = offset + payload_size
        if payload_end > n:
            break

        # Check for user_data_unregistered with our UUID
        if payload_type == 5 and payload_size == 24:
            uuid_slice = sei_payload[offset:offset + 16]
            if uuid_slice == TIMESTAMP_UUID:
                ts_bytes = sei_payload[offset + 16:offset + 24]
                (timestamp_ns,) = struct.unpack(">Q", ts_bytes)
                return timestamp_ns

        offset = payload_end

    return None


def find_sei_timestamp(data: bytes) -> int | None:
    """Scan H.264 byte-stream data and return the first embedded timestamp.

    Terminates early once a timestamp is found, or when a VCL NAL unit
    (types 1-5) is reached, since SEI always precedes VCL in an access unit.
    """
    for _offset, nalu in iter_nalus(data):
        if len(nalu) == 0:
            continue
        nal_type = nalu[0] & 0x1F
        # VCL NAL types 1-5: no more SEI can follow, stop scanning
        if 1 <= nal_type <= 5:
            return None
        if nal_type == NAL_TYPE_SEI:
            ts = parse_sei_timestamp(nalu[1:])
            if ts is not None:
                return ts
    return None


def find_sei_timestamps(data: bytes) -> list[int]:
    """Scan H.264 byte-stream data and return all our embedded timestamps.

    For most use cases, prefer find_sei_timestamp() which returns only the
    first timestamp and terminates early.
    """
    ts = find_sei_timestamp(data)
    return [ts] if ts is not None else []


def timestamp_to_datetime(timestamp_ns: int) -> datetime:
    """Convert a nanosecond Unix timestamp to a timezone-aware datetime."""
    return datetime.fromtimestamp(timestamp_ns / 1e9, tz=timezone.utc)


def format_timestamp(timestamp_ns: int) -> str:
    """Format a nanosecond Unix timestamp as a human-readable UTC string."""
    dt = timestamp_to_datetime(timestamp_ns)
    # Include microseconds
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f UTC")


if __name__ == "__main__":
    # Quick self-test: create a minimal SEI NAL and parse it
    import time

    ts = int(time.time() * 1e9)
    # Build a test SEI NAL unit
    nalu = bytearray()
    nalu.extend(b"\x00\x00\x00\x01")  # start code
    nalu.append(0x06)  # NAL type SEI
    nalu.append(0x05)  # payload type: user_data_unregistered
    nalu.append(0x18)  # payload size: 24
    nalu.extend(TIMESTAMP_UUID)
    nalu.extend(struct.pack(">Q", ts))
    nalu.append(0x80)  # RBSP trailing bits

    found = find_sei_timestamps(bytes(nalu))
    assert len(found) == 1
    assert found[0] == ts
    print(f"Self-test passed. Timestamp: {format_timestamp(ts)}")
