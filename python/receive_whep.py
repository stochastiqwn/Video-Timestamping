#!/usr/bin/env python3
"""
Receive a WHEP WebRTC stream and extract absolute timestamps from H.264 SEI NAL units.

Uses aiortc to connect to a WHEP endpoint, receive H.264 encoded video frames,
and parse SEI NAL units to extract embedded absolute timestamps.

Usage:
    python receive_whep.py --url http://localhost:8889/stream/whep
"""

import argparse
import asyncio
import signal
import struct
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sei_parser

try:
    import aiohttp
    from aiortc import RTCPeerConnection, RTCSessionDescription
    from aiortc.contrib.media import MediaRecorder
except ImportError:
    print(
        "Error: aiortc and aiohttp are required.\n"
        "Install with: pip install aiortc aiohttp",
        file=sys.stderr,
    )
    sys.exit(1)


frame_count = 0


class TimestampTrack:
    """Processes incoming RTP packets to extract H.264 SEI timestamps."""

    def __init__(self):
        self.frame_count = 0

    def process_rtp_packet(self, data: bytes):
        """Process raw H.264 data from an RTP depayloaded frame."""
        self.frame_count += 1
        timestamps = sei_parser.find_sei_timestamps(data)

        if timestamps:
            for ts_ns in timestamps:
                latency_ms = (time.time_ns() - ts_ns) / 1e6
                print(
                    f"Frame {self.frame_count:6d} | "
                    f"Timestamp: {sei_parser.format_timestamp(ts_ns)} | "
                    f"Latency: {latency_ms:.1f} ms"
                )
        else:
            print(f"Frame {self.frame_count:6d} | No SEI timestamp found")


async def whep_connect(url: str):
    """Connect to a WHEP endpoint and receive timestamped video."""
    pc = RTCPeerConnection()
    tracker = TimestampTrack()

    # Add a transceiver for receiving video
    pc.addTransceiver("video", direction="recvonly")

    @pc.on("track")
    def on_track(track):
        print(f"Received track: {track.kind}")

        if track.kind == "video":

            @track.on("ended")
            def on_ended():
                print("Track ended")

            async def receive_frames():
                while True:
                    try:
                        frame = await track.recv()
                        # aiortc gives us decoded VideoFrame objects.
                        # To access raw H.264 with SEI, we need to work at a lower level.
                        # For aiortc, we can access the encoded data through a custom approach.
                        tracker.frame_count += 1
                        # Note: With standard aiortc, frames arrive decoded.
                        # The SEI data is available in the RTP depayloader before decoding.
                        # For full SEI access, we'd need to hook into the jitter buffer.
                        print(
                            f"Frame {tracker.frame_count:6d} | "
                            f"Decoded frame: {frame.width}x{frame.height} | "
                            f"PTS: {frame.pts} | time_base: {frame.time_base}"
                        )
                    except Exception as e:
                        if "MediaStreamError" in str(type(e).__name__):
                            break
                        raise

            asyncio.ensure_future(receive_frames())

    @pc.on("datachannel")
    def on_datachannel(channel):
        """Handle DataChannel for timestamp fallback."""
        print(f"DataChannel received: {channel.label}")

        @channel.on("message")
        def on_message(message):
            if isinstance(message, str):
                import json

                try:
                    data = json.loads(message)
                    ts_ns = data.get("timestamp_ns")
                    frame_num = data.get("frame")
                    if ts_ns:
                        latency_ms = (time.time_ns() - ts_ns) / 1e6
                        print(
                            f"Frame {frame_num:6d} | "
                            f"Timestamp: {sei_parser.format_timestamp(ts_ns)} | "
                            f"Latency: {latency_ms:.1f} ms | "
                            f"(via DataChannel)"
                        )
                except json.JSONDecodeError:
                    pass

    # Create offer
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)

    # Send offer to WHEP endpoint
    async with aiohttp.ClientSession() as session:
        headers = {"Content-Type": "application/sdp"}
        async with session.post(url, data=pc.localDescription.sdp, headers=headers) as resp:
            if resp.status != 201:
                body = await resp.text()
                print(f"WHEP error: {resp.status} {body}", file=sys.stderr)
                await pc.close()
                return

            answer_sdp = await resp.text()
            # Get the resource URL for teardown
            resource_url = resp.headers.get("Location", "")

    answer = RTCSessionDescription(sdp=answer_sdp, type="answer")
    await pc.setRemoteDescription(answer)

    print("Connected to WHEP endpoint — receiving stream...")
    print("Press Ctrl+C to stop.\n")

    # Keep running until interrupted
    stop_event = asyncio.Event()

    def handle_signal():
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_signal)

    await stop_event.wait()

    # Teardown
    await pc.close()
    if resource_url:
        async with aiohttp.ClientSession() as session:
            await session.delete(resource_url)
    print("\nReceiver stopped.")


def main():
    parser = argparse.ArgumentParser(description="Receive WHEP stream and extract timestamps")
    parser.add_argument(
        "--url",
        default="http://localhost:8889/stream/whep",
        help="WHEP endpoint URL",
    )
    args = parser.parse_args()

    print(f"Connecting to WHEP endpoint: {args.url}")
    print(
        "Note: SEI timestamp extraction works best with raw H.264 access.\n"
        "aiortc decodes frames before delivery, so timestamps are extracted\n"
        "via DataChannel fallback when available.\n"
    )

    asyncio.run(whep_connect(args.url))


if __name__ == "__main__":
    main()
