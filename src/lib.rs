use gstreamer as gst;
use glib::translate::from_glib_borrow;
use std::os::raw::c_char;

mod plugin;
mod prerollvalve;

fn plugin_init(plugin: &gst::Plugin) -> Result<(), glib::BoolError> {
    plugin::plugin_init(plugin)
}

// Glue for older GStreamer loaders that expect a `gst_plugin_desc` symbol.
unsafe extern "C" fn plugin_init_glue(plugin: *mut gst::ffi::GstPlugin) -> glib::ffi::gboolean {
    let plugin = unsafe { from_glib_borrow(plugin) };
    match plugin_init(&plugin) {
        Ok(()) => glib::ffi::GTRUE,
        Err(_) => glib::ffi::GFALSE,
    }
}

#[repr(transparent)]
struct PluginDescExport(gst::ffi::GstPluginDesc);
unsafe impl Sync for PluginDescExport {}

#[unsafe(no_mangle)]
pub static gst_plugin_desc: PluginDescExport = PluginDescExport(gst::ffi::GstPluginDesc {
    major_version: 1,
    minor_version: 22,
    name: b"gstprerollvalve\0".as_ptr() as *const c_char,
    description: b"Preroll Valve Plugin\0".as_ptr() as *const c_char,
    plugin_init: Some(plugin_init_glue),
    version: b"1.0\0".as_ptr() as *const c_char,
    license: b"MIT\0".as_ptr() as *const c_char,
    source: b"gstprerollvalve\0".as_ptr() as *const c_char,
    package: b"gstprerollvalve\0".as_ptr() as *const c_char,
    origin: b"https://example.com\0".as_ptr() as *const c_char,
    release_datetime: std::ptr::null(),
    _gst_reserved: [std::ptr::null_mut(); 4],
});

gst::plugin_define!(
    gstprerollvalve,
    "Preroll Valve Plugin",
    plugin_init,
    "1.0",
    "MIT",
    "gstprerollvalve",
    "gstprerollvalve",
    "https://example.com"
);

