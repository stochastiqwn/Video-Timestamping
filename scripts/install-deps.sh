#!/usr/bin/env bash
# Install all dependencies for the Video-Timestamping project.
# Run with: bash scripts/install-deps.sh

set -euo pipefail

echo "=== Video-Timestamping: Installing Dependencies ==="
echo

# --- System packages (GStreamer dev, encoder, etc.) ---
echo "[1/5] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    libgstreamer1.0-dev \
    libgstreamer-plugins-base1.0-dev \
    libgstreamer-plugins-bad1.0-dev \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly \
    gstreamer1.0-libav \
    gstreamer1.0-tools \
    gstreamer1.0-x \
    gstreamer1.0-nice \
    gir1.2-gst-plugins-base-1.0 \
    gir1.2-gst-plugins-bad-1.0 \
    python3-gi \
    python3-gi-cairo \
    gir1.2-gstreamer-1.0

echo "  GStreamer $(pkg-config --modversion gstreamer-1.0) installed"

# --- Rust build ---
echo
echo "[2/5] Building Rust GStreamer plugin..."
if ! command -v cargo &>/dev/null; then
    echo "  Installing Rust via rustup..."
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
    source "$HOME/.cargo/env"
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

cargo build --release
echo "  Plugin built: target/release/libgstrstimestamp.so"

# --- Python dependencies ---
echo
echo "[3/5] Installing Python dependencies..."
pip3 install --quiet -r python/requirements.txt 2>/dev/null || \
    pip install --quiet -r python/requirements.txt
echo "  Python packages installed"

# --- Node.js (for JS static server) ---
echo
echo "[4/5] Checking Node.js..."
if command -v node &>/dev/null; then
    echo "  Node.js $(node --version) available"
else
    echo "  Node.js not found. Install from https://nodejs.org/ for the JS receiver."
fi

# --- mediamtx ---
echo
echo "[5/5] Installing mediamtx..."
MEDIAMTX_VERSION="v1.11.3"
MEDIAMTX_ARCH="linux_amd64"
MEDIAMTX_DIR="$PROJECT_DIR/bin"
MEDIAMTX_BIN="$MEDIAMTX_DIR/mediamtx"

if [ -f "$MEDIAMTX_BIN" ]; then
    echo "  mediamtx already installed at $MEDIAMTX_BIN"
else
    mkdir -p "$MEDIAMTX_DIR"
    MEDIAMTX_URL="https://github.com/bluenviron/mediamtx/releases/download/${MEDIAMTX_VERSION}/mediamtx_${MEDIAMTX_VERSION}_${MEDIAMTX_ARCH}.tar.gz"
    echo "  Downloading mediamtx ${MEDIAMTX_VERSION}..."
    curl -sL "$MEDIAMTX_URL" | tar -xz -C "$MEDIAMTX_DIR" mediamtx
    chmod +x "$MEDIAMTX_BIN"
    echo "  mediamtx installed at $MEDIAMTX_BIN"
fi

echo
echo "=== All dependencies installed ==="
echo
echo "Quick start:"
echo "  1. Start mediamtx:    ./bin/mediamtx mediamtx.yml"
echo "  2. Publish stream:    GST_PLUGIN_PATH=target/release python3 python/publish_rtsp.py"
echo "  3. Receive (RTSP):    GST_PLUGIN_PATH=target/release python3 python/receive_rtsp.py"
echo "  4. Receive (browser): cd js && node server.js  # then open http://localhost:8080"
