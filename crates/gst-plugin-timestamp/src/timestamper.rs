/// GStreamer element `timestamper`: injects absolute timestamps into H.264 streams
/// via SEI User Data Unregistered NAL units.
///
/// The element operates as a GstBaseTransform on `video/x-h264` buffers in
/// byte-stream (Annex B) format. For each buffer (access unit), it:
/// 1. Captures the current wall-clock time (UTC nanoseconds since Unix epoch)
/// 2. Constructs an SEI NAL unit containing that timestamp
/// 3. Injects the SEI before the first VCL NAL unit in the access unit
use gst::glib;

use gst::subclass::prelude::*;
use gst_base::subclass::prelude::*;

use crate::sei;

/// Element metadata

const ELEMENT_LONG_NAME: &str = "Absolute Timestamp Injector";
const ELEMENT_DESCRIPTION: &str =
    "Injects absolute UTC timestamps into H.264 streams via SEI NAL units";
const ELEMENT_AUTHOR: &str = "Video-Timestamping Authors";

#[derive(Default)]
pub struct Timestamper;

#[glib::object_subclass]
impl ObjectSubclass for Timestamper {
    const NAME: &'static str = "RsTimestamper";
    type Type = super::Timestamper;
    type ParentType = gst_base::BaseTransform;
}

impl ObjectImpl for Timestamper {}

impl GstObjectImpl for Timestamper {}

impl ElementImpl for Timestamper {
    fn metadata() -> Option<&'static gst::subclass::ElementMetadata> {
        static ELEMENT_METADATA: std::sync::OnceLock<gst::subclass::ElementMetadata> =
            std::sync::OnceLock::new();
        Some(ELEMENT_METADATA.get_or_init(|| {
            gst::subclass::ElementMetadata::new(
                ELEMENT_LONG_NAME,
                "Codec/Parser/Video",
                ELEMENT_DESCRIPTION,
                ELEMENT_AUTHOR,
            )
        }))
    }

    fn pad_templates() -> &'static [gst::PadTemplate] {
        static PAD_TEMPLATES: std::sync::OnceLock<Vec<gst::PadTemplate>> =
            std::sync::OnceLock::new();
        PAD_TEMPLATES.get_or_init(|| {
            let caps = gst::Caps::builder("video/x-h264")
                .field("stream-format", "byte-stream")
                .build();
            let sink_pad = gst::PadTemplate::new(
                "sink",
                gst::PadDirection::Sink,
                gst::PadPresence::Always,
                &caps,
            )
            .unwrap();
            let src_pad = gst::PadTemplate::new(
                "src",
                gst::PadDirection::Src,
                gst::PadPresence::Always,
                &caps,
            )
            .unwrap();
            vec![sink_pad, src_pad]
        })
    }
}

impl BaseTransformImpl for Timestamper {
    const MODE: gst_base::subclass::BaseTransformMode =
        gst_base::subclass::BaseTransformMode::Both;
    const PASSTHROUGH_ON_SAME_CAPS: bool = false;
    const TRANSFORM_IP_ON_PASSTHROUGH: bool = false;

    fn transform(
        &self,
        inbuf: &gst::Buffer,
        outbuf: &mut gst::BufferRef,
    ) -> Result<gst::FlowSuccess, gst::FlowError> {
        let map = inbuf.map_readable().map_err(|_| {
            gst::element_imp_error!(self, gst::CoreError::Failed, ["Failed to map input buffer"]);
            gst::FlowError::Error
        })?;

        let timestamp_ns = sei::now_nanos();
        let injected = sei::inject_timestamp_sei(map.as_slice(), timestamp_ns);

        // Resize output buffer and copy data
        outbuf.set_size(injected.len());
        {
            let mut out_map = outbuf.map_writable().map_err(|_| {
                gst::element_imp_error!(
                    self,
                    gst::CoreError::Failed,
                    ["Failed to map output buffer"]
                );
                gst::FlowError::Error
            })?;
            out_map.as_mut_slice()[..injected.len()].copy_from_slice(&injected);
        }

        // Preserve timing metadata
        outbuf.set_pts(inbuf.pts());
        outbuf.set_dts(inbuf.dts());
        outbuf.set_duration(inbuf.duration());
        outbuf.set_offset(inbuf.offset());
        outbuf.set_offset_end(inbuf.offset_end());

        gst::log!(
            gst::CAT_DEFAULT,
            imp = self,
            "Injected timestamp {} ns into frame",
            timestamp_ns
        );

        Ok(gst::FlowSuccess::Ok)
    }

    fn transform_size(
        &self,
        _direction: gst::PadDirection,
        _caps: &gst::Caps,
        size: usize,
        _othercaps: &gst::Caps,
    ) -> Option<usize> {
        // Output is larger than input by the SEI NAL unit size.
        // SEI NAL = 4 (start code) + 1 (header) + 1 (type) + 1 (size) + 16 (UUID) + 8 (ts) + 1 (trailing) = 32 bytes
        Some(size + 32)
    }
}
