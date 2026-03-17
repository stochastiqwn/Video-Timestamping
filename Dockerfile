# Multi-stage Dockerfile for Video-Timestamping
#
# Stage 1 (builder): Compiles the Rust GStreamer plugin
# Stage 2 (runtime): Lean image with all runtime deps, plugin, Python, Node.js, mediamtx
#
# Usage:
#   docker build -t video-timestamping .
#   docker run -it --rm -p 8554:8554 -p 8889:8889 -p 8080:8080 video-timestamping

# ============================================================
# Stage 1: Build the Rust GStreamer plugin
# ============================================================
FROM rust:1.83-bookworm AS builder

# Install GStreamer dev libraries needed for compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgstreamer1.0-dev \
    libgstreamer-plugins-base1.0-dev \
    libgstreamer-plugins-bad1.0-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy only Cargo files first for dependency caching
COPY Cargo.toml Cargo.lock ./
COPY crates/gst-plugin-timestamp/Cargo.toml crates/gst-plugin-timestamp/Cargo.toml
COPY crates/gst-plugin-timestamp/build.rs crates/gst-plugin-timestamp/build.rs

# Create a dummy source to cache dependency compilation
RUN mkdir -p crates/gst-plugin-timestamp/src && \
    echo "fn main() {}" > crates/gst-plugin-timestamp/src/lib.rs && \
    cargo build --release 2>/dev/null || true && \
    rm -rf crates/gst-plugin-timestamp/src

# Copy real source and build
COPY crates/ crates/
RUN cargo build --release && \
    cargo test --release

# ============================================================
# Stage 2: Runtime image
# ============================================================
FROM debian:bookworm-slim AS runtime

# Avoid interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install runtime dependencies:
# - GStreamer runtime + plugins (base, good, bad, ugly, libav)
# - Python 3 + GObject Introspection bindings
# - Node.js (for JS static server)
# - curl (for mediamtx download)
RUN apt-get update && apt-get install -y --no-install-recommends \
    # GStreamer runtime
    gstreamer1.0-tools \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly \
    gstreamer1.0-libav \
    gstreamer1.0-nice \
    libgstreamer1.0-0 \
    libgstreamer-plugins-base1.0-0 \
    # GStreamer GObject Introspection (for Python bindings)
    gir1.2-gstreamer-1.0 \
    gir1.2-gst-plugins-base-1.0 \
    gir1.2-gst-plugins-bad-1.0 \
    # Python 3
    python3 \
    python3-pip \
    python3-gi \
    python3-gi-cairo \
    # Node.js
    nodejs \
    npm \
    # Utilities
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install mediamtx
ARG MEDIAMTX_VERSION=v1.11.3
RUN ARCH=$(dpkg --print-architecture) && \
    case "$ARCH" in \
      amd64) MTX_ARCH="linux_amd64" ;; \
      arm64) MTX_ARCH="linux_arm64v8" ;; \
      armhf) MTX_ARCH="linux_armv7" ;; \
      *) echo "Unsupported arch: $ARCH" && exit 1 ;; \
    esac && \
    curl -sL "https://github.com/bluenviron/mediamtx/releases/download/${MEDIAMTX_VERSION}/mediamtx_${MEDIAMTX_VERSION}_${MTX_ARCH}.tar.gz" \
    | tar -xz -C /usr/local/bin mediamtx && \
    chmod +x /usr/local/bin/mediamtx

# Install Python dependencies (skip PyGObject since it's installed via apt)
COPY python/requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages \
    aiortc>=1.6.0 \
    aiohttp>=3.9 \
    numpy>=1.24 \
    opencv-python-headless>=4.8

WORKDIR /app

# Copy the compiled plugin from the builder stage
COPY --from=builder /build/target/release/libgstrstimestamp.so /usr/lib/gstreamer-1.0/

# Copy application code
COPY python/ python/
COPY js/ js/
COPY scripts/ scripts/
COPY mediamtx.yml mediamtx.yml
COPY README.md README.md

# Make scripts executable
RUN chmod +x scripts/*.sh

# Set GST_PLUGIN_PATH so the plugin is always found
# (we installed to the standard GStreamer plugin dir, but set this as fallback)
ENV GST_PLUGIN_PATH=/usr/lib/gstreamer-1.0

# Verify the plugin loads
RUN gst-inspect-1.0 timestamper && gst-inspect-1.0 detimestamper

# Expose ports:
# 8554 - RTSP
# 8889 - WebRTC WHIP/WHEP
# 8080 - JS static file server
EXPOSE 8554 8889 8080

# Default: run the demo script
CMD ["bash", "scripts/demo.sh"]
