#!/usr/bin/env python3
"""
Publish a timestamped H.264 video stream to an RTSP server (e.g., mediamtx).

The pipeline captures video (from test source, file, or camera), encodes it as
H.264, injects absolute timestamps via the `timestamper` GStreamer element,
and pushes the stream to an RTSP server using rtspclientsink.

Usage:
    # Publish test video to mediamtx:
    python publish_rtsp.py --url rtsp://localhost:8554/stream

    # Publish from a video file:
    python publish_rtsp.py --url rtsp://localhost:8554/stream --source filesrc location=video.mp4 ! decodebin

    # Publish from a camera:
    python publish_rtsp.py --url rtsp://localhost:8554/stream --source v4l2src device=/dev/video0
"""

import argparse
import signal
import sys
import os

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib

# Ensure our plugin is loadable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import timestamp_plugin


def main():
    parser = argparse.ArgumentParser(description="Publish timestamped H.264 to RTSP")
    parser.add_argument(
        "--url",
        default="rtsp://localhost:8554/stream",
        help="RTSP server URL to publish to (default: rtsp://localhost:8554/stream)",
    )
    parser.add_argument(
        "--source",
        default="videotestsrc is-live=true pattern=ball",
        help="GStreamer source element description",
    )
    parser.add_argument(
        "--width", type=int, default=1280, help="Video width (default: 1280)"
    )
    parser.add_argument(
        "--height", type=int, default=720, help="Video height (default: 720)"
    )
    parser.add_argument(
        "--fps", type=int, default=30, help="Framerate (default: 30)"
    )
    parser.add_argument(
        "--bitrate", type=int, default=2000, help="Encoding bitrate in kbps (default: 2000)"
    )
    args = parser.parse_args()

    Gst.init(None)
    timestamp_plugin.load()

    pipeline_str = (
        f"{args.source} ! "
        f"videoconvert ! videoscale ! "
        f"video/x-raw,format=I420,width={args.width},height={args.height},"
        f"framerate={args.fps}/1 ! "
        f"x264enc tune=zerolatency bitrate={args.bitrate} speed-preset=ultrafast "
        f"key-int-max={args.fps * 2} ! "
        f"video/x-h264,stream-format=byte-stream,profile=baseline ! "
        f"timestamper ! "
        f"h264parse ! "
        f"rtspclientsink location={args.url} protocols=tcp latency=0"
    )

    print(f"Pipeline: {pipeline_str}")
    print(f"Publishing to: {args.url}")

    pipeline = Gst.parse_launch(pipeline_str)
    loop = GLib.MainLoop()

    # Handle bus messages
    bus = pipeline.get_bus()
    bus.add_signal_watch()

    def on_message(bus, message):
        t = message.type
        if t == Gst.MessageType.EOS:
            print("End of stream")
            loop.quit()
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print(f"Error: {err.message}", file=sys.stderr)
            if debug:
                print(f"Debug: {debug}", file=sys.stderr)
            loop.quit()
        elif t == Gst.MessageType.STATE_CHANGED:
            if message.src == pipeline:
                old, new, pending = message.parse_state_changed()
                if new == Gst.State.PLAYING:
                    print("Pipeline is PLAYING — streaming timestamped video")

    bus.connect("message", on_message)

    # Handle Ctrl+C
    def signal_handler(sig, frame):
        print("\nStopping...")
        pipeline.send_event(Gst.Event.new_eos())

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    pipeline.set_state(Gst.State.PLAYING)

    try:
        loop.run()
    except Exception:
        pass
    finally:
        pipeline.set_state(Gst.State.NULL)
        print("Pipeline stopped.")


if __name__ == "__main__":
    main()
