use gstreamer as gst;
use glib::prelude::*;
use gst::prelude::*;
use std::collections::VecDeque;
use std::sync::Mutex;
use once_cell::sync::Lazy;

// Example pipeline:
// gst-launch-1.0 filesrc location=video.h264 ! h264parse ! prerollvalve open=true max-history=5000 ! h264parse ! avdec_h264 ! autovideosink

// Property defaults
const DEFAULT_OPEN: bool = false;
const DEFAULT_MAX_HISTORY: u64 = 5000; // ms
const DEFAULT_DEBUG: bool = false;

// Properties
#[derive(Debug, Clone, Copy)]
struct Settings {
    open: bool,
    max_history: u64,
    debug: bool,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            open: DEFAULT_OPEN,
            max_history: DEFAULT_MAX_HISTORY,
            debug: DEFAULT_DEBUG,
        }
    }
}

struct StoredBuffer {
    buffer: gst::Buffer,
    timestamp: gst::ClockTime,
    is_keyframe: bool,
}

struct State {
    queue: VecDeque<StoredBuffer>,
}

impl Default for State {
    fn default() -> Self {
        Self {
            queue: VecDeque::new(),
        }
    }
}

mod imp {
    use super::*;
    use glib::subclass::prelude::*;
    use gst::subclass::prelude::*;

    pub struct PrerollValve {
        pub settings: Mutex<Settings>,
        pub state: Mutex<State>,
        pub srcpad: gst::Pad,
        pub sinkpad: gst::Pad,
    }

    impl PrerollValve {
        fn sink_chain(
            &self,
            _pad: &gst::Pad,
            _element: &super::PrerollValve,
            buffer: gst::Buffer,
        ) -> Result<gst::FlowSuccess, gst::FlowError> {
            let settings = self.settings.lock().unwrap();
            let mut state = self.state.lock().unwrap();

            // Check debug property or GST log level
            if settings.debug {
                 gst::trace!(CAT, "Received buffer: pts={:?}, dts={:?}", buffer.pts(), buffer.dts());
            }

            if settings.open {
                // If we have data in queue, we must dump it first
                // This happens on the transition from closed -> open
                // Since we are in the chain function, we are serialized with upstream
                if !state.queue.is_empty() {
                    gst::info!(CAT, "Valve opened. Dumping {} buffered frames.", state.queue.len());
                    
                    // Find first keyframe index
                    let mut start_index = None;
                    // Search forwards for the first keyframe to maximize preroll
                    // We need to start from a keyframe so the decoder can decode
                    for (i, stored) in state.queue.iter().enumerate() {
                        if stored.is_keyframe {
                            start_index = Some(i);
                            break;
                        }
                    }
                    
                    // Use first keyframe if found, otherwise dump from start
                    let idx = start_index.unwrap_or_else(|| {
                        gst::warning!(CAT, "No keyframe found in buffer, dumping from start");
                        0
                    });
                    
                    let frames_to_dump = state.queue.len() - idx;
                    gst::info!(CAT, "Starting dump from index {} (is_keyframe={}), dumping {} frames", 
                        idx, 
                        state.queue.get(idx).map(|b| b.is_keyframe).unwrap_or(false),
                        frames_to_dump
                    );

                    // Dump buffers
                    for i in idx..state.queue.len() {
                        if let Some(stored) = state.queue.get(i) {
                             if settings.debug {
                                gst::trace!(CAT, "Pushing stored buffer pts={:?}", stored.buffer.pts());
                            }
                            let buf_to_push = stored.buffer.clone();
                            if let Err(e) = self.srcpad.push(buf_to_push) {
                                gst::error!(CAT, "Failed to push stored buffer: {:?}", e);
                                state.queue.clear();
                                return Err(e);
                            }
                        }
                    }
                    state.queue.clear();
                }

                // Forward the current live buffer
                drop(state);
                drop(settings);
                self.srcpad.push(buffer)
            } else {
                // Valve is closed (default)
                // Store incoming buffers
                
                // Identify keyframe
                // GST_BUFFER_FLAG_DELTA_UNIT == FALSE means keyframe (usually)
                let is_keyframe = !buffer.flags().contains(gst::BufferFlags::DELTA_UNIT);
                let pts = buffer.pts().or_else(|| buffer.dts()).unwrap_or(gst::ClockTime::ZERO);

                let stored = StoredBuffer {
                    buffer: buffer, // ownership moved to struct
                    timestamp: pts,
                    is_keyframe,
                };
                
                state.queue.push_back(stored);

                // Prune old buffers
                let max_history = gst::ClockTime::from_mseconds(settings.max_history);
                // We use the timestamp of the *latest* buffer (pts) as reference current time?
                // Or system time?
                // "current_timestamp - buffer.timestamp <= max_history". 
                // Usually this implies relative to the stream head.
                let current_ts = pts;
                
                while let Some(front) = state.queue.front() {
                    if current_ts > front.timestamp && (current_ts - front.timestamp) > max_history {
                        state.queue.pop_front();
                    } else {
                        break;
                    }
                }
                
                Ok(gst::FlowSuccess::Ok)
            }
        }

        fn sink_event(
            &self,
            _pad: &gst::Pad,
            _element: &super::PrerollValve,
            event: gst::Event,
        ) -> bool {
            // Forward all incoming events (e.g., CAPS/EOS/FLUSH) to src pad to
            // keep negotiation working.
            self.srcpad.push_event(event)
        }
    }

    #[glib::object_subclass]
    impl ObjectSubclass for PrerollValve {
        const NAME: &'static str = "GstPrerollValve";
        type Type = super::PrerollValve;
        type ParentType = gst::Element;

        fn with_class(klass: &Self::Class) -> Self {
            let templ_sink = klass.pad_template("sink").unwrap();
            let templ_src = klass.pad_template("src").unwrap();

            let sinkpad = gst::Pad::builder_from_template(&templ_sink)
                .chain_function(|pad, parent, buffer| {
                    PrerollValve::catch_panic_pad_function(
                        parent,
                        || Err(gst::FlowError::Error),
                        |preroll| preroll.sink_chain(pad, &preroll.obj(), buffer),
                    )
                })
                .event_function(|pad, parent, event| {
                    PrerollValve::catch_panic_pad_function(
                        parent,
                        || false,
                        |preroll| preroll.sink_event(pad, &preroll.obj(), event),
                    )
                })
                .build();

            let srcpad = gst::Pad::builder_from_template(&templ_src)
                .build();

            Self {
                settings: Mutex::new(Settings::default()),
                state: Mutex::new(State::default()),
                sinkpad,
                srcpad,
            }
        }
    }

    impl ObjectImpl for PrerollValve {
        fn constructed(&self) {
            self.parent_constructed();
            let obj = self.obj();
            obj.add_pad(&self.sinkpad).unwrap();
            obj.add_pad(&self.srcpad).unwrap();
        }

        fn properties() -> &'static [glib::ParamSpec] {
            static PROPERTIES: Lazy<Vec<glib::ParamSpec>> = Lazy::new(|| {
                vec![
                    glib::ParamSpecBoolean::builder("open")
                        .nick("Open")
                        .blurb("Valve state (true=open/dump, false=closed/buffer)")
                        .default_value(DEFAULT_OPEN)
                        .mutable_ready()
                        .mutable_playing()
                        .build(),
                    glib::ParamSpecUInt64::builder("max-history")
                        .nick("Max History")
                        .blurb("Max history in milliseconds to buffer")
                        .default_value(DEFAULT_MAX_HISTORY)
                        .mutable_ready()
                        .mutable_playing()
                        .build(),
                    glib::ParamSpecBoolean::builder("debug")
                        .nick("Debug")
                        .blurb("Enable extra debug logging")
                        .default_value(DEFAULT_DEBUG)
                        .mutable_ready()
                        .mutable_playing()
                        .build(),
                ]
            });
            PROPERTIES.as_ref()
        }

        fn set_property(&self, _id: usize, value: &glib::Value, pspec: &glib::ParamSpec) {
            let mut settings = self.settings.lock().unwrap();
            match pspec.name() {
                "open" => settings.open = value.get().expect("type checked upstream"),
                "max-history" => settings.max_history = value.get().expect("type checked upstream"),
                "debug" => settings.debug = value.get().expect("type checked upstream"),
                _ => unimplemented!(),
            }
        }

        fn property(&self, _id: usize, pspec: &glib::ParamSpec) -> glib::Value {
            let settings = self.settings.lock().unwrap();
            match pspec.name() {
                "open" => settings.open.to_value(),
                "max-history" => settings.max_history.to_value(),
                "debug" => settings.debug.to_value(),
                _ => unimplemented!(),
            }
        }
    }

    impl GstObjectImpl for PrerollValve {}

    impl ElementImpl for PrerollValve {
        fn metadata() -> Option<&'static gst::subclass::ElementMetadata> {
            static ELEMENT_METADATA: Lazy<gst::subclass::ElementMetadata> = Lazy::new(|| {
                gst::subclass::ElementMetadata::new(
                    "Preroll Valve",
                    "Generic/Filter/Video",
                    "Buffers video and dumps on command",
                    "Cursor AI",
                )
            });
            Some(&*ELEMENT_METADATA)
        }

        fn pad_templates() -> &'static [gst::PadTemplate] {
            static PAD_TEMPLATES: Lazy<Vec<gst::PadTemplate>> = Lazy::new(|| {
                let caps = gst::Caps::new_any(); // Accepting ANY for flexibility, specifically H264
                
                vec![
                    gst::PadTemplate::new(
                        "sink",
                        gst::PadDirection::Sink,
                        gst::PadPresence::Always,
                        &caps,
                    )
                    .unwrap(),
                    gst::PadTemplate::new(
                        "src",
                        gst::PadDirection::Src,
                        gst::PadPresence::Always,
                        &caps,
                    )
                    .unwrap(),
                ]
            });
            PAD_TEMPLATES.as_ref()
        }
    }
}

glib::wrapper! {
    pub struct PrerollValve(ObjectSubclass<imp::PrerollValve>)
        @extends gst::Element, gst::Object;
}

pub fn register(plugin: &gst::Plugin) -> Result<(), glib::BoolError> {
    gst::Element::register(
        Some(plugin),
        "prerollvalve",
        gst::Rank::NONE,
        PrerollValve::static_type(),
    )
}

static CAT: Lazy<gst::DebugCategory> = Lazy::new(|| {
    gst::DebugCategory::new(
        "prerollvalve",
        gst::DebugColorFlags::empty(),
        Some("Preroll Valve Element"),
    )
});

