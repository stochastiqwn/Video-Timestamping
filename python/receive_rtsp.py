#!/usr/bin/env python3
"""
Receive an RTSP stream and extract absolute timestamps from H.264 SEI NAL units.

Connects to an RTSP URL, depayloads H.264, and parses each access unit to find
our embedded absolute timestamps. Optionally decodes and displays the video.

Usage:
    # Just print timestamps:
    python receive_rtsp.py --url rtsp://localhost:8554/stream

    # Decode and display video with timestamp overlay:
    python receive_rtsp.py --url rtsp://localhost:8554/stream --display

    # Use the detimestamper GStreamer element instead of Python parsing:
    python receive_rtsp.py --url rtsp://localhost:8554/stream --use-element
"""

import argparse
import signal
import sys
import os
import time

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstApp", "1.0")
from gi.repository import Gst, GstApp, GLib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sei_parser
import timestamp_plugin


frame_count = 0


def on_new_sample_raw_parse(sink) -> Gst.FlowReturn:
    """Callback for appsink: parse H.264 data directly in Python to extract timestamps."""
    global frame_count
    sample = sink.emit("pull-sample")
    if sample is None:
        return Gst.FlowReturn.ERROR

    buf = sample.get_buffer()
    success, map_info = buf.map(Gst.MapFlags.READ)
    if not success:
        return Gst.FlowReturn.ERROR

    data = bytes(map_info.data)
    buf.unmap(map_info)

    ts_ns = sei_parser.find_sei_timestamp(data)
    frame_count += 1

    if ts_ns is not None:
        latency_ms = (time.time_ns() - ts_ns) / 1e6
        print(
            f"Frame {frame_count:6d} | "
            f"Timestamp: {sei_parser.format_timestamp(ts_ns)} | "
            f"Latency: {latency_ms:.1f} ms"
        )
    else:
        pts = buf.pts
        if pts != Gst.CLOCK_TIME_NONE:
            print(f"Frame {frame_count:6d} | No SEI timestamp | PTS: {pts}")
        else:
            print(f"Frame {frame_count:6d} | No SEI timestamp | No PTS")

    return Gst.FlowReturn.OK


def on_new_sample_element(sink) -> Gst.FlowReturn:
    """Callback for appsink: read timestamp from GstReferenceTimestampMeta (set by detimestamper)."""
    global frame_count
    sample = sink.emit("pull-sample")
    if sample is None:
        return Gst.FlowReturn.ERROR

    buf = sample.get_buffer()
    frame_count += 1

    # Try to read GstReferenceTimestampMeta
    # The detimestamper element attaches this with caps "timestamp/x-unix-ns"
    reference_caps = Gst.Caps.from_string("timestamp/x-unix-ns, source=(string)sei")
    meta = buf.get_reference_timestamp_meta(reference_caps)

    if meta:
        ts_ns = meta.timestamp
        latency_ms = (time.time_ns() - ts_ns) / 1e6
        print(
            f"Frame {frame_count:6d} | "
            f"Timestamp: {sei_parser.format_timestamp(ts_ns)} | "
            f"Latency: {latency_ms:.1f} ms | "
            f"(via detimestamper meta)"
        )
    else:
        print(f"Frame {frame_count:6d} | No timestamp meta found")

    return Gst.FlowReturn.OK


def main():
    parser = argparse.ArgumentParser(description="Receive RTSP stream and extract timestamps")
    parser.add_argument(
        "--url",
        default="rtsp://localhost:8554/stream",
        help="RTSP URL to connect to",
    )
    parser.add_argument(
        "--use-element",
        action="store_true",
        help="Use the detimestamper GStreamer element instead of Python SEI parsing",
    )
    parser.add_argument(
        "--display",
        action="store_true",
        help="Also decode and display the video (requires a display)",
    )
    args = parser.parse_args()

    Gst.init(None)
    timestamp_plugin.load()

    if args.use_element:
        # Pipeline using the detimestamper element
        pipeline_str = (
            f"rtspsrc location={args.url} latency=100 ! "
            f"rtph264depay ! h264parse ! "
            f"video/x-h264,stream-format=byte-stream ! "
            f"detimestamper ! "
            f"appsink name=sink emit-signals=true sync=false"
        )
        callback = on_new_sample_element
    else:
        # Pipeline with raw H.264 to Python for SEI parsing
        pipeline_str = (
            f"rtspsrc location={args.url} latency=100 ! "
            f"rtph264depay ! h264parse ! "
            f"video/x-h264,stream-format=byte-stream ! "
            f"appsink name=sink emit-signals=true sync=false"
        )
        callback = on_new_sample_raw_parse

    print(f"Connecting to: {args.url}")
    print(f"Mode: {'detimestamper element' if args.use_element else 'Python SEI parsing'}")

    pipeline = Gst.parse_launch(pipeline_str)
    sink = pipeline.get_by_name("sink")
    sink.connect("new-sample", callback)

    loop = GLib.MainLoop()
    bus = pipeline.get_bus()
    bus.add_signal_watch()

    def on_message(bus, message):
        t = message.type
        if t == Gst.MessageType.EOS:
            print("\nEnd of stream")
            loop.quit()
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print(f"\nError: {err.message}", file=sys.stderr)
            if debug:
                print(f"Debug: {debug}", file=sys.stderr)
            loop.quit()
        elif t == Gst.MessageType.STATE_CHANGED:
            if message.src == pipeline:
                old, new, pending = message.parse_state_changed()
                if new == Gst.State.PLAYING:
                    print("Receiving stream — waiting for frames...\n")

    bus.connect("message", on_message)

    def signal_handler(sig, frame):
        print("\nStopping...")
        loop.quit()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    pipeline.set_state(Gst.State.PLAYING)

    try:
        loop.run()
    except Exception:
        pass
    finally:
        pipeline.set_state(Gst.State.NULL)
        print("Receiver stopped.")


if __name__ == "__main__":
    main()
