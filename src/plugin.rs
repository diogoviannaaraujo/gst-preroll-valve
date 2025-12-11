use gstreamer as gst;
use crate::prerollvalve;

pub fn plugin_init(plugin: &gst::Plugin) -> Result<(), glib::BoolError> {
    prerollvalve::register(plugin)
}

