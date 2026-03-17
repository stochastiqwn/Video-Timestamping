"""
Helper module for loading the Rust GStreamer timestamp plugin and building pipelines.

Usage:
    import timestamp_plugin
    timestamp_plugin.load()  # Ensures the plugin is registered

    # Then use 'timestamper' and 'detimestamper' elements in GStreamer pipelines
"""

import os
import sys
import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst

# Initialize GStreamer
Gst.init(None)

# Default path to the compiled plugin shared library
_PLUGIN_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "target",
    "debug",
)

_loaded = False


def load(plugin_dir: str | None = None):
    """Load the rstimestamp GStreamer plugin.

    Args:
        plugin_dir: Directory containing libgstrstimestamp.so.
                    Defaults to ../target/debug/ relative to this file.
    """
    global _loaded
    if _loaded:
        return

    search_dir = plugin_dir or _PLUGIN_DIR

    # Also check release build
    release_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "target",
        "release",
    )

    for d in [search_dir, release_dir]:
        lib_path = os.path.join(d, "libgstrstimestamp.so")
        if os.path.exists(lib_path):
            registry = Gst.Registry.get()
            result = registry.scan_path(d)
            _loaded = True
            print(f"Loaded rstimestamp plugin from {d}")
            return

    # Try GST_PLUGIN_PATH as fallback
    gst_path = os.environ.get("GST_PLUGIN_PATH", "")
    if gst_path:
        for d in gst_path.split(":"):
            lib_path = os.path.join(d, "libgstrstimestamp.so")
            if os.path.exists(lib_path):
                _loaded = True
                print(f"Plugin available via GST_PLUGIN_PATH: {d}")
                return

    print(
        f"Warning: Could not find libgstrstimestamp.so in {search_dir} or {release_dir}.\n"
        f"Build the plugin with: cargo build\n"
        f"Or set GST_PLUGIN_PATH to the directory containing the .so file.",
        file=sys.stderr,
    )


def make_timestamper_pipeline(
    source_desc: str = "videotestsrc is-live=true",
    encoder_desc: str = "x264enc tune=zerolatency bitrate=2000 speed-preset=ultrafast key-int-max=30",
    sink_desc: str = "fakesink",
) -> Gst.Pipeline:
    """Create a GStreamer pipeline with the timestamper element.

    Args:
        source_desc: GStreamer element description for the video source.
        encoder_desc: GStreamer element description for the H.264 encoder.
        sink_desc: GStreamer element description for the output sink.

    Returns:
        A GStreamer Pipeline ready to be set to PLAYING state.
    """
    load()

    pipeline_str = (
        f"{source_desc} ! videoconvert ! video/x-raw,format=I420 ! "
        f"{encoder_desc} ! video/x-h264,stream-format=byte-stream ! "
        f"timestamper ! h264parse ! {sink_desc}"
    )

    pipeline = Gst.parse_launch(pipeline_str)
    return pipeline
