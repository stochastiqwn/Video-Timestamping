/// GStreamer element `detimestamper`: extracts absolute timestamps from H.264 SEI NAL units.
///
/// For each H.264 access unit, it scans for our timestamp SEI, extracts the
/// absolute timestamp, and attaches it to the buffer as a `GstReferenceTimestampMeta`.
/// Optionally strips the SEI NAL unit from the stream.
use gst::glib;
use gst::prelude::*;
use gst::subclass::prelude::*;
use gst_base::subclass::prelude::*;
use std::sync::Mutex;

use crate::sei;


const ELEMENT_LONG_NAME: &str = "Absolute Timestamp Extractor";
const ELEMENT_DESCRIPTION: &str =
    "Extracts absolute UTC timestamps from H.264 SEI NAL units and attaches as buffer metadata";
const ELEMENT_AUTHOR: &str = "Video-Timestamping Authors";

const DEFAULT_STRIP_SEI: bool = false;

#[derive(Debug)]
struct Settings {
    strip_sei: bool,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            strip_sei: DEFAULT_STRIP_SEI,
        }
    }
}

pub struct Detimestamper {
    settings: Mutex<Settings>,
}

impl Default for Detimestamper {
    fn default() -> Self {
        Self {
            settings: Mutex::new(Settings::default()),
        }
    }
}

#[glib::object_subclass]
impl ObjectSubclass for Detimestamper {
    const NAME: &'static str = "RsDetimestamper";
    type Type = super::Detimestamper;
    type ParentType = gst_base::BaseTransform;
}

impl ObjectImpl for Detimestamper {
    fn properties() -> &'static [glib::ParamSpec] {
        static PROPERTIES: std::sync::OnceLock<Vec<glib::ParamSpec>> = std::sync::OnceLock::new();
        PROPERTIES.get_or_init(|| {
            vec![glib::ParamSpecBoolean::builder("strip-sei")
                .nick("Strip SEI")
                .blurb("Remove the timestamp SEI NAL unit after extracting the timestamp")
                .default_value(DEFAULT_STRIP_SEI)
                .mutable_ready()
                .build()]
        })
    }

    fn set_property(&self, _id: usize, value: &glib::Value, pspec: &glib::ParamSpec) {
        match pspec.name() {
            "strip-sei" => {
                let mut settings = self.settings.lock().unwrap();
                settings.strip_sei = value.get().expect("type checked upstream");
            }
            _ => unimplemented!(),
        }
    }

    fn property(&self, _id: usize, pspec: &glib::ParamSpec) -> glib::Value {
        match pspec.name() {
            "strip-sei" => {
                let settings = self.settings.lock().unwrap();
                settings.strip_sei.to_value()
            }
            _ => unimplemented!(),
        }
    }
}

impl GstObjectImpl for Detimestamper {}

impl ElementImpl for Detimestamper {
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

impl BaseTransformImpl for Detimestamper {
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

        let data = map.as_slice();
        let timestamps = sei::find_sei_timestamps(data);

        let settings = self.settings.lock().unwrap();
        let strip = settings.strip_sei;
        drop(settings);

        let output_data = if strip && !timestamps.is_empty() {
            strip_timestamp_sei_nalus(data)
        } else {
            data.to_vec()
        };

        // Write output
        outbuf.set_size(output_data.len());
        {
            let mut out_map = outbuf.map_writable().map_err(|_| {
                gst::element_imp_error!(
                    self,
                    gst::CoreError::Failed,
                    ["Failed to map output buffer"]
                );
                gst::FlowError::Error
            })?;
            out_map.as_mut_slice()[..output_data.len()].copy_from_slice(&output_data);
        }

        // Preserve timing
        outbuf.set_pts(inbuf.pts());
        outbuf.set_dts(inbuf.dts());
        outbuf.set_duration(inbuf.duration());
        outbuf.set_offset(inbuf.offset());
        outbuf.set_offset_end(inbuf.offset_end());

        if let Some(&ts) = timestamps.first() {
            // Attach as ReferenceTimestampMeta using a custom reference caps
            // that identifies our absolute timestamp scheme.
            let reference_caps =
                gst::Caps::builder("timestamp/x-unix-ns").field("source", "sei").build();
            let timestamp = gst::ClockTime::from_nseconds(ts);
            gst::ReferenceTimestampMeta::add(outbuf, &reference_caps, timestamp, gst::ClockTime::NONE);

            gst::log!(
                gst::CAT_DEFAULT,
                imp = self,
                "Extracted absolute timestamp: {} ns",
                ts
            );
        }

        Ok(gst::FlowSuccess::Ok)
    }

    fn transform_size(
        &self,
        _direction: gst::PadDirection,
        _caps: &gst::Caps,
        size: usize,
        _othercaps: &gst::Caps,
    ) -> Option<usize> {
        // Output could be same size or smaller (if stripping SEI)
        Some(size)
    }
}

/// Remove our timestamp SEI NAL units from an H.264 byte-stream.
fn strip_timestamp_sei_nalus(data: &[u8]) -> Vec<u8> {
    let mut result = Vec::with_capacity(data.len());
    let mut last_end: usize = 0;

    // We need to iterate NALUs and skip the ones that are our timestamp SEIs
    let mut pos: usize = 0;
    loop {
        // Find next start code
        let sc_start = find_start_code(data, pos);
        if sc_start.is_none() {
            // Copy remaining data
            result.extend_from_slice(&data[last_end..]);
            break;
        }
        let (sc_offset, sc_len) = sc_start.unwrap();
        let nalu_start = sc_offset + sc_len;

        // Find the end of this NALU (next start code or end of data)
        let nalu_end = if let Some((next_sc, _)) = find_start_code(data, nalu_start) {
            next_sc
        } else {
            data.len()
        };

        if nalu_start < data.len() {
            let nal_type = data[nalu_start] & 0x1F;
            if nal_type == 6 {
                // SEI — check if it contains our timestamp
                let payload = &data[nalu_start + 1..nalu_end];
                if sei::parse_sei_timestamp_from_payload(payload).is_some() {
                    // Skip this NALU: copy everything before it
                    result.extend_from_slice(&data[last_end..sc_offset]);
                    last_end = nalu_end;
                    pos = nalu_end;
                    continue;
                }
            }
        }

        pos = nalu_end;
    }

    result
}

fn find_start_code(data: &[u8], from: usize) -> Option<(usize, usize)> {
    let mut i = from;
    while i + 2 < data.len() {
        if data[i] == 0x00 && data[i + 1] == 0x00 {
            if data[i + 2] == 0x01 {
                return Some((i, 3));
            }
            if i + 3 < data.len() && data[i + 2] == 0x00 && data[i + 3] == 0x01 {
                return Some((i, 4));
            }
        }
        i += 1;
    }
    None
}
