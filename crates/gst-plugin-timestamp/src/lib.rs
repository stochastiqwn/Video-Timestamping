/// GStreamer plugin providing absolute timestamp injection/extraction for H.264 streams.
///
/// Elements:
/// - `timestamper`: Injects UTC timestamps as H.264 SEI NAL units
/// - `detimestamper`: Extracts timestamps from SEI and attaches as GstReferenceTimestampMeta
use gst::glib;
use gst::prelude::*;

mod detimestamper;
mod sei;
mod timestamper;

// Define the wrapper types for our elements
glib::wrapper! {
    pub struct Timestamper(ObjectSubclass<timestamper::Timestamper>)
        @extends gst_base::BaseTransform, gst::Element, gst::Object;
}

glib::wrapper! {
    pub struct Detimestamper(ObjectSubclass<detimestamper::Detimestamper>)
        @extends gst_base::BaseTransform, gst::Element, gst::Object;
}

fn plugin_init(plugin: &gst::Plugin) -> Result<(), glib::BoolError> {
    gst::Element::register(
        Some(plugin),
        "timestamper",
        gst::Rank::NONE,
        Timestamper::static_type(),
    )?;
    gst::Element::register(
        Some(plugin),
        "detimestamper",
        gst::Rank::NONE,
        Detimestamper::static_type(),
    )?;
    Ok(())
}

gst::plugin_define!(
    rstimestamp,
    env!("CARGO_PKG_DESCRIPTION"),
    plugin_init,
    concat!(env!("CARGO_PKG_VERSION"), "-", env!("COMMIT_ID")),
    "MIT/Apache-2.0",
    env!("CARGO_PKG_NAME"),
    env!("CARGO_PKG_NAME"),
    env!("CARGO_PKG_REPOSITORY"),
    env!("BUILD_REL_DATE")
);
