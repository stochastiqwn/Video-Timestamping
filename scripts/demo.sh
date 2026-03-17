#!/usr/bin/env bash
# End-to-end demo: publish timestamped video and receive it.
# Run with: bash scripts/demo.sh
#
# This script starts mediamtx, publishes a timestamped video stream,
# and runs a Python receiver to verify timestamp extraction.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# Determine plugin path (prefer release, fall back to debug)
if [ -f "target/release/libgstrstimestamp.so" ]; then
    PLUGIN_PATH="target/release"
elif [ -f "target/debug/libgstrstimestamp.so" ]; then
    PLUGIN_PATH="target/debug"
else
    echo "Error: Plugin not built. Run 'cargo build' or 'cargo build --release' first."
    exit 1
fi

export GST_PLUGIN_PATH="$PROJECT_DIR/$PLUGIN_PATH"
echo "Using plugin from: $GST_PLUGIN_PATH"

# Verify plugin is loadable
if gst-inspect-1.0 --plugin rstimestamp &>/dev/null; then
    echo "Plugin loaded successfully:"
    gst-inspect-1.0 --plugin rstimestamp 2>/dev/null | head -10
else
    echo "Warning: Could not inspect plugin. Continuing anyway..."
fi

echo
echo "============================================"
echo "  Video Timestamping End-to-End Demo"
echo "============================================"
echo
echo "This demo will:"
echo "  1. Start mediamtx (RTSP + WebRTC server)"
echo "  2. Publish a timestamped video stream"
echo "  3. Run a Python RTSP receiver for 10 seconds"
echo

# Check if mediamtx is available
MEDIAMTX_BIN=""
if [ -f "$PROJECT_DIR/bin/mediamtx" ]; then
    MEDIAMTX_BIN="$PROJECT_DIR/bin/mediamtx"
elif command -v mediamtx &>/dev/null; then
    MEDIAMTX_BIN="mediamtx"
fi

# Cleanup function
PIDS=()
cleanup() {
    echo
    echo "Cleaning up..."
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
    done
    echo "Done."
}
trap cleanup EXIT

if [ -n "$MEDIAMTX_BIN" ]; then
    echo "[Step 1] Starting mediamtx..."
    "$MEDIAMTX_BIN" "$PROJECT_DIR/mediamtx.yml" &>/dev/null &
    PIDS+=($!)
    sleep 2
    echo "  mediamtx running (PID ${PIDS[-1]})"
    echo "    RTSP: rtsp://localhost:8554/stream"
    echo "    WHEP: http://localhost:8889/stream/whep"

    echo
    echo "[Step 2] Starting publisher..."
    python3 python/publish_rtsp.py \
        --url rtsp://localhost:8554/stream \
        --source "videotestsrc is-live=true pattern=ball num-buffers=300" \
        --fps 30 &
    PIDS+=($!)
    sleep 3
    echo "  Publisher running (PID ${PIDS[-1]})"

    echo
    echo "[Step 3] Starting RTSP receiver (will run for ~10 seconds)..."
    echo "  ---"
    timeout 10 python3 python/receive_rtsp.py \
        --url rtsp://localhost:8554/stream || true
    echo "  ---"
else
    echo "mediamtx not found. Running local GStreamer-only test instead."
    echo
    echo "Testing pipeline: videotestsrc ! x264enc ! timestamper ! detimestamper ! fakesink"
    echo "---"
    GST_DEBUG=2 gst-launch-1.0 -v \
        videotestsrc is-live=true num-buffers=30 \
        ! videoconvert \
        ! video/x-raw,format=I420,width=320,height=240 \
        ! x264enc tune=zerolatency speed-preset=ultrafast key-int-max=10 \
        ! video/x-h264,stream-format=byte-stream \
        ! timestamper \
        ! detimestamper \
        ! fakesink 2>&1 | head -30
    echo "---"
    echo
    echo "To run the full demo with RTSP/WebRTC, install mediamtx:"
    echo "  bash scripts/install-deps.sh"
fi

echo
echo "============================================"
echo "  Demo complete!"
echo "============================================"
echo
echo "To try the browser WHEP receiver:"
echo "  cd js && node server.js"
echo "  Open http://localhost:8080 in Chrome/Edge"
echo "  Enter WHEP URL: http://localhost:8889/stream/whep"
