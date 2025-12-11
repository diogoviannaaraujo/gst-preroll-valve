# Preroll Valve GStreamer Plugin

Custom GStreamer element that buffers encoded video for a rolling window, then flushes from the latest keyframe when you open the valve.

## What it does
- Buffers incoming buffers while `open=false` (default), keeping up to `max-history` milliseconds.
- When `open` becomes `true`, dumps the queued data starting from the most recent keyframe, then forwards live data.
- Accepts any caps; intended primarily for H.264 elementary streams.
- Optional `debug` flag for extra logging on the `prerollvalve` debug category.

## Properties
- `open` (bool, default `false`): Valve state. Set to `true` to flush queued buffers and pass through live data.
- `max-history` (u64 ms, default `5000`): Maximum buffered window while closed.
- `debug` (bool, default `false`): Emit additional trace-level logs for each buffer.

## Build & install
1) Install Rust toolchain and GStreamer development headers (`gstreamer`, `gstreamer-base`, `gstreamer-video`).
2) Build the plugin:
   - `cargo build --release`
3) Make it discoverable:
   - `export GST_PLUGIN_PATH="$PWD/target/release${GST_PLUGIN_PATH:+:$GST_PLUGIN_PATH}"`
4) Verify:
   - `gst-inspect-1.0 prerollvalve`

## Usage examples
- Basic playback with preroll dump:
  - `gst-launch-1.0 filesrc location=video.h264 ! h264parse ! prerollvalve open=true max-history=5000 ! h264parse ! avdec_h264 ! autovideosink`
- Programmatic toggle (Rust-ish pseudocode):
  - Create pipeline with `prerollvalve open=false max-history=5000`
  - When ready to release buffered content: `element.set_property("open", true)`

## Notes
- The element keeps the latest keyframe and following buffers within `max-history`; older data is discarded.
- Logging category is `prerollvalve` (enable with `GST_DEBUG=prerollvalve:5,...`).