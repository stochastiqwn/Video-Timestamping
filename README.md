# Video-Timestamping

A GStreamer plugin (Rust) that embeds absolute timestamps into H.264 video streams via SEI NAL units, with Python and JavaScript receivers that extract per-frame timestamps from RTSP and WebRTC (WHEP) streams.

## Architecture

```
                           ┌─────────────┐
                           │  mediamtx   │
                           │ (RTSP +     │
[Video Source] ──► [GStreamer Pipeline] ──► [RTSP push] ──►│  WebRTC)    │
                    │                │     └──────┬──────┘
                    │  timestamper   │            │
                    │  (injects SEI) │     ┌──────┴──────┐
                    └────────────────┘     │             │
                                     RTSP ▼        WHEP ▼
                                 ┌──────────┐  ┌──────────┐
                                 │  Python   │  │  Browser  │
                                 │  Receiver │  │  (JS)     │
                                 └──────────┘  └──────────┘
```

**How timestamps travel**: The `timestamper` element injects a **H.264 SEI User Data Unregistered** NAL unit into each access unit. This NAL contains a 16-byte UUID identifier followed by an 8-byte UTC timestamp (nanoseconds since Unix epoch). Because SEI NAL units are part of the H.264 bitstream, they survive any transport — RTSP, RTP, WebRTC — without needing sideband channels.

## Components

| Component | Language | Description |
|-----------|----------|-------------|
| `crates/gst-plugin-timestamp/` | Rust | GStreamer plugin with `timestamper` and `detimestamper` elements |
| `python/publish_rtsp.py` | Python | Publish timestamped video to an RTSP server |
| `python/receive_rtsp.py` | Python | Receive RTSP stream and extract timestamps |
| `python/receive_whep.py` | Python | Receive WHEP/WebRTC stream and extract timestamps |
| `python/sei_parser.py` | Python | H.264 SEI timestamp parser (shared library) |
| `js/index.html` | HTML/JS | Browser WHEP receiver with timestamp display |
| `js/whep-client.js` | JavaScript | WHEP client with Encoded Transforms + DataChannel fallback |
| `js/sei-parser-worker.js` | JavaScript | Web Worker that parses H.264 SEI from encoded frames |

## Quick Start

### 1. Install Dependencies

```bash
bash scripts/install-deps.sh
```

Or manually:

```bash
# System packages
sudo apt install libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
    libgstreamer-plugins-bad1.0-dev gstreamer1.0-plugins-{base,good,bad,ugly} \
    gstreamer1.0-libav gstreamer1.0-tools

# Build the Rust plugin
cargo build --release

# Python packages
pip install -r python/requirements.txt
```

### 2. Start mediamtx

Download [mediamtx](https://github.com/bluenviron/mediamtx/releases) and run:

```bash
./bin/mediamtx mediamtx.yml
```

### 3. Publish a Timestamped Stream

```bash
GST_PLUGIN_PATH=target/release python3 python/publish_rtsp.py \
    --url rtsp://localhost:8554/stream
```

### 4. Receive and Extract Timestamps

**Python (RTSP):**

```bash
GST_PLUGIN_PATH=target/release python3 python/receive_rtsp.py \
    --url rtsp://localhost:8554/stream
```

Output:
```
Frame      1 | Timestamp: 2026-03-17 12:00:00.123456 UTC | Latency: 45.2 ms
Frame      2 | Timestamp: 2026-03-17 12:00:00.156789 UTC | Latency: 43.1 ms
...
```

**JavaScript (WHEP/WebRTC):**

```bash
cd js && node server.js
```

Open `http://localhost:8080` in Chrome/Edge, enter the WHEP URL `http://localhost:8889/stream/whep`, and click Connect.

## GStreamer Elements

### `timestamper`

Injects absolute UTC timestamps into H.264 byte-stream data via SEI NAL units.

```bash
gst-launch-1.0 videotestsrc ! x264enc ! timestamper ! h264parse ! fakesink
```

- **Pad caps**: `video/x-h264, stream-format=byte-stream`
- **Direction**: Transform (sink → src)

### `detimestamper`

Extracts timestamps from SEI NAL units and attaches them as `GstReferenceTimestampMeta`.

```bash
gst-launch-1.0 ... ! detimestamper strip-sei=true ! ...
```

- **Properties**:
  - `strip-sei` (bool, default `false`): Remove timestamp SEI after extraction

## Python Bindings

The Rust plugin is a standard GStreamer element — it's automatically available from Python via GObject Introspection:

```python
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst

Gst.init(None)
# Set GST_PLUGIN_PATH or use timestamp_plugin.load()

pipeline = Gst.parse_launch(
    "videotestsrc ! x264enc ! video/x-h264,stream-format=byte-stream ! "
    "timestamper ! h264parse ! fakesink"
)
pipeline.set_state(Gst.State.PLAYING)
```

Or use the helper:

```python
from timestamp_plugin import load, make_timestamper_pipeline
load()
pipeline = make_timestamper_pipeline(sink_desc="rtspclientsink location=rtsp://localhost:8554/stream")
```

## SEI Timestamp Format

The timestamp is embedded as an H.264 SEI User Data Unregistered (payload type 5) message:

```
Offset  Bytes  Description
0       4      Start code: 00 00 00 01
4       1      NAL header: 06 (SEI)
5       1      Payload type: 05 (user_data_unregistered)
6       1      Payload size: 18 (24 bytes)
7       16     UUID: a1b2c3d4-e5f6-4789-abcd-ef0123456789
23      8      Timestamp: u64 big-endian, nanoseconds since Unix epoch
31      1      RBSP trailing bits: 80
```

**UUID**: `a1b2c3d4-e5f6-4789-abcd-ef0123456789` — receivers use this to identify timestamp SEI messages.

## JavaScript Timestamp Extraction

The JS receiver uses two strategies:

1. **Encoded Transforms** (Chromium/Edge): `RTCRtpScriptTransform` intercepts encoded H.264 frames before decoding, allowing direct SEI parsing in a Web Worker.

2. **DataChannel fallback** (all browsers): If available, timestamps are also sent via a WebRTC DataChannel as JSON messages.

## Project Structure

```
├── Cargo.toml                          # Rust workspace
├── crates/gst-plugin-timestamp/        # GStreamer plugin (Rust)
│   └── src/
│       ├── lib.rs                      # Plugin registration
│       ├── sei.rs                      # H.264 SEI read/write utilities
│       ├── timestamper.rs              # Timestamp injection element
│       └── detimestamper.rs            # Timestamp extraction element
├── python/                             # Python tools
│   ├── sei_parser.py                   # H.264 SEI parser
│   ├── timestamp_plugin.py             # Plugin loader helper
│   ├── publish_rtsp.py                 # RTSP publisher
│   ├── receive_rtsp.py                 # RTSP receiver
│   └── receive_whep.py                 # WHEP receiver
├── js/                                 # JavaScript WHEP receiver
│   ├── index.html                      # Browser UI
│   ├── whep-client.js                  # WHEP client
│   ├── sei-parser-worker.js            # Encoded Transform worker
│   └── server.js                       # Dev server
├── mediamtx.yml                        # Media server config
└── scripts/
    ├── install-deps.sh                 # Dependency installer
    └── demo.sh                         # End-to-end demo
```

## License

MIT OR Apache-2.0
