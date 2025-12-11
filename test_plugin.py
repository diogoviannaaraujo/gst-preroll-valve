#!/usr/bin/env python3
"""
Comprehensive prerollvalve exercise.

Scenario:
- 120s long live-like source (videotestsrc, 30fps)
- 8s preroll buffer in the valve
- Valve starts CLOSED, then follows this schedule:
    - open at   +20s
    - close at  +40s
    - open at   +60s
    - close at  +80s (remains closed through the end)
- Output is split with splitmuxsink into 5s segments for inspection.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import tempfile
from typing import List, Tuple

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib  # noqa: E402


# Test configuration
FRAMERATE = 30
DURATION_SECONDS = 120
NUM_BUFFERS = FRAMERATE * DURATION_SECONDS  # 3600 buffers for 120 seconds
PREROLL_SECONDS = 8.0
MAX_HISTORY_MS = int(PREROLL_SECONDS * 1000)
SPLIT_DURATION_SECONDS = 5
SPLIT_DURATION_NS = SPLIT_DURATION_SECONDS * 1_000_000_000

# Valve schedule (absolute times from start, seconds)
VALVE_SCHEDULE: List[Tuple[str, float]] = [
    ("open", 20.0),
    ("close", 40.0),
    ("open", 60.0),
    ("close", 80.0),
]


def schedule_valve_transitions(valve: Gst.Element):
    """Program the open/close cadence."""
    for action, at_seconds in VALVE_SCHEDULE:
        def _toggle(action=action, at_seconds=at_seconds):
            print(f"  -> valve {action.upper()} at +{at_seconds:.1f}s")
            valve.set_property("open", action == "open")
            return False  # one-shot

        GLib.timeout_add(int(at_seconds * 1000), _toggle)


def run_pipeline(output_dir: str) -> tuple[int, list[str]]:
    """Run the pipeline once and return (buffer_count, segment_paths)."""
    Gst.init(None)

    buffer_count = [0]
    output_pattern = os.path.join(output_dir, "segment-%02d.mkv")

    # Pipeline: videotestsrc -> encoder -> prerollvalve -> counter -> splitmuxsink
    pipeline_desc = (
        f"videotestsrc num-buffers={NUM_BUFFERS} is-live=true "
        f"! video/x-raw,width=640,height=360,framerate={FRAMERATE}/1 "
        "! queue "
        "! x264enc tune=zerolatency bitrate=512 speed-preset=ultrafast key-int-max=60 "
        "! h264parse "
        f"! prerollvalve name=valve open=false max-history={MAX_HISTORY_MS} debug=true "
        "! identity name=counter silent=true signal-handoffs=true "
        "! queue "
        "! splitmuxsink name=mux "
        f"max-size-time={SPLIT_DURATION_NS} "
        f'reset-muxer=true location="{output_pattern}" '
        "muxer=matroskamux"
    )

    pipeline = Gst.parse_launch(pipeline_desc)
    valve = pipeline.get_by_name("valve")
    counter = pipeline.get_by_name("counter")

    if valve is None or counter is None:
        print("Failed to retrieve prerollvalve or counter element.", file=sys.stderr)
        sys.exit(1)

    def on_handoff(_identity, _buffer):
        buffer_count[0] += 1

    counter.connect("handoff", on_handoff)

    loop = GLib.MainLoop()
    bus = pipeline.get_bus()
    bus.add_signal_watch()

    error_occurred = [False]

    def on_message(_bus, message, _loop):
        if message.type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print(f"ERROR: {err} ({debug})", file=sys.stderr)
            error_occurred[0] = True
            _loop.quit()
        elif message.type == Gst.MessageType.EOS:
            _loop.quit()

    bus.connect("message", on_message, loop)

    schedule_valve_transitions(valve)

    print("Starting pipeline...")
    pipeline.set_state(Gst.State.PLAYING)

    try:
        loop.run()
    finally:
        pipeline.set_state(Gst.State.NULL)
        bus.remove_signal_watch()

    if error_occurred[0]:
        return -1, []

    segments = sorted(glob.glob(os.path.join(output_dir, "segment-*.mkv")))
    return buffer_count[0], segments


def resolve_output_dir(arg_dir: str | None) -> str:
    """
    Decide where to write output.

    Priority:
    1) --output-dir argument if provided
    2) OUTPUT_DIR env var
    3) /output if it exists or can be created (good for docker bind)
    4) Temporary directory fallback
    """
    candidates = []
    if arg_dir:
        candidates.append(arg_dir)
    env_dir = os.environ.get("OUTPUT_DIR")
    if env_dir:
        candidates.append(env_dir)
    candidates.append("/output")

    for path in candidates:
        try:
            os.makedirs(path, exist_ok=True)
            return os.path.abspath(path)
        except OSError:
            continue

    return tempfile.mkdtemp(prefix="prerollvalve_segments_")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run prerollvalve segmented test.")
    parser.add_argument(
        "--output-dir",
        help="Directory for splitmux output (defaults to OUTPUT_DIR env, then /output, then temp)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    Gst.init(None)

    plugin_path = os.environ.get("GST_PLUGIN_PATH", "")
    print(f"GST_PLUGIN_PATH={plugin_path}")

    factory = Gst.ElementFactory.find("prerollvalve")
    if not factory:
        print("ERROR: prerollvalve element not found. Check GST_PLUGIN_PATH.", file=sys.stderr)
        sys.exit(1)

    output_dir = resolve_output_dir(args.output_dir)

    print("=" * 60)
    print("Preroll Valve segmented test")
    print("=" * 60)
    print(f"Source duration: {DURATION_SECONDS}s @ {FRAMERATE}fps ({NUM_BUFFERS} buffers)")
    print(f"Preroll window:  {PREROLL_SECONDS}s (max-history={MAX_HISTORY_MS}ms)")
    print("Valve schedule:")
    for action, at_s in VALVE_SCHEDULE:
        print(f"  - {action.upper():<5} at +{at_s:>4.1f}s")
    print(f"Splitmux chunks: {SPLIT_DURATION_SECONDS}s each")
    print(f"Output directory: {output_dir}")
    print()

    buffer_count, segments = run_pipeline(output_dir)

    print()
    print("=" * 60)
    print("Run summary")
    print("=" * 60)
    print(f"Total buffers that reached sink: {buffer_count}")
    print(f"Total segments: {len(segments)}")
    if segments:
        print("Segments (order preserved):")
        for seg in segments:
            size = os.path.getsize(seg)
            print(f"  - {os.path.basename(seg)} ({size} bytes)")
    else:
        print("No segments were produced.")

    # Non-zero exit code signals failure
    sys.exit(0 if buffer_count > 0 and segments else 1)


if __name__ == "__main__":
    main()
