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
    """Iterate over NAL units in byte-stream format.

    Yields (nalu_start_offset, nalu_bytes_without_start_code).
    """
    starts = list(find_start_codes(data))
    for idx, (nalu_start, sc_len) in enumerate(starts):
        if idx + 1 < len(starts):
            next_sc_start = starts[idx + 1][0] - starts[idx + 1][1]
            nalu_data = data[nalu_start:next_sc_start]
        else:
            nalu_data = data[nalu_start:]
        yield nalu_start, nalu_data


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


def find_sei_timestamps(data: bytes) -> list[int]:
    """Scan H.264 byte-stream data and return all our embedded timestamps."""
    timestamps = []
    for _offset, nalu in iter_nalus(data):
        if len(nalu) == 0:
            continue
        nal_type = nalu[0] & 0x1F
        if nal_type == NAL_TYPE_SEI:
            ts = parse_sei_timestamp(nalu[1:])
            if ts is not None:
                timestamps.append(ts)
    return timestamps


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
