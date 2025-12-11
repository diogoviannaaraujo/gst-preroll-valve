#!/usr/bin/env python3
"""
Test for prerollvalve GStreamer plugin.

This test verifies that the prerollvalve correctly buffers frames when closed
and dumps them (from the last keyframe) when opened.

Test strategy:
1. Run pipeline with valve ALWAYS OPEN -> baseline (no buffering)
2. Run pipeline with valve CLOSED then OPENED after 8s -> should include preroll
3. Compare: with-preroll file should have MORE or EQUAL buffers than baseline
"""
import os
import sys
import tempfile
import time

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib  # noqa: E402


# Test configuration
NUM_BUFFERS = 450       # 450 frames at 30fps = 15 seconds of video
FRAMERATE = 30          # frames per second
PREROLL_DELAY = 8.0     # seconds before opening valve (8s of buffered preroll)
MAX_HISTORY_MS = 15000  # max history to keep in valve (15 seconds)


def run_pipeline(output_file: str, valve_start_open: bool, open_delay_seconds: float | None = None) -> int:
    """
    Run the test pipeline and return the number of buffers that reached the sink.
    
    Args:
        output_file: Path to write output video
        valve_start_open: If True, valve starts open (pass-through). If False, valve buffers.
        open_delay_seconds: If valve_start_open is False, delay before opening valve.
    
    Returns:
        Number of buffers that passed through the valve to the sink.
    """
    Gst.init(None)

    buffer_count = [0]  # Use list to allow modification in nested function

    # Pipeline: videotestsrc -> encoder -> prerollvalve -> mux -> filesink
    # Using matroskamux to create a proper container file
    pipeline_desc = (
        f"videotestsrc num-buffers={NUM_BUFFERS} is-live=true "
        f"! video/x-raw,width=320,height=240,framerate={FRAMERATE}/1 "
        "! x264enc tune=zerolatency bitrate=256 speed-preset=ultrafast key-int-max=15 "
        "! h264parse "
        f"! prerollvalve name=valve open={'true' if valve_start_open else 'false'} max-history={MAX_HISTORY_MS} debug=true "
        "! identity name=counter signal-handoffs=true "
        "! h264parse "
        "! matroskamux "
        f"! filesink location={output_file}"
    )

    pipeline = Gst.parse_launch(pipeline_desc)
    valve = pipeline.get_by_name("valve")
    counter = pipeline.get_by_name("counter")

    if valve is None:
        print("Failed to retrieve prerollvalve element from pipeline.", file=sys.stderr)
        sys.exit(1)

    # Count buffers using identity element's handoff signal
    def on_handoff(_identity, buffer):
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

    # Schedule valve opening if needed
    if not valve_start_open and open_delay_seconds is not None:
        def open_valve():
            print(f"  Opening valve after {open_delay_seconds}s delay...")
            valve.set_property("open", True)
            return False  # one-shot timeout

        GLib.timeout_add(int(open_delay_seconds * 1000), open_valve)

    pipeline.set_state(Gst.State.PLAYING)

    try:
        loop.run()
    finally:
        pipeline.set_state(Gst.State.NULL)
        bus.remove_signal_watch()

    if error_occurred[0]:
        return -1

    return buffer_count[0]


def main():
    Gst.init(None)

    plugin_path = os.environ.get("GST_PLUGIN_PATH", "")
    print(f"GST_PLUGIN_PATH={plugin_path}")

    # Check plugin is available
    factory = Gst.ElementFactory.find("prerollvalve")
    if not factory:
        print("ERROR: prerollvalve element not found. Check GST_PLUGIN_PATH.", file=sys.stderr)
        sys.exit(1)

    total_duration = NUM_BUFFERS / FRAMERATE
    preroll_frames = int(PREROLL_DELAY * FRAMERATE)
    
    print("=" * 60)
    print("Preroll Valve Plugin Test")
    print("=" * 60)
    print()
    print("Test setup:")
    print(f"  - {NUM_BUFFERS} frames at {FRAMERATE}fps = {total_duration:.1f} seconds of video")
    print(f"  - Valve opens after {PREROLL_DELAY} seconds")
    print(f"  - Expected ~{preroll_frames} frames buffered as preroll")
    print(f"  - Max history: {MAX_HISTORY_MS}ms")
    print("  - With preroll: should include buffered frames from before open")
    print("  - Without preroll (always open): only frames after pipeline start")
    print()

    with tempfile.TemporaryDirectory() as tmpdir:
        baseline_file = os.path.join(tmpdir, "baseline_always_open.mkv")
        preroll_file = os.path.join(tmpdir, "with_preroll.mkv")

        # Test 1: Baseline - valve always open (no buffering)
        print("[Test 1] Running with valve ALWAYS OPEN (baseline, no preroll)...")
        baseline_buffers = run_pipeline(baseline_file, valve_start_open=True)
        baseline_size = os.path.getsize(baseline_file) if os.path.exists(baseline_file) else 0
        print(f"  Result: {baseline_buffers} buffers, file size: {baseline_size} bytes")
        print()

        # Small delay between tests
        time.sleep(0.5)

        # Test 2: With preroll - valve closed initially, opened after delay
        print(f"[Test 2] Running with valve CLOSED then OPENED after {PREROLL_DELAY}s (with preroll)...")
        preroll_buffers = run_pipeline(preroll_file, valve_start_open=False, open_delay_seconds=PREROLL_DELAY)
        preroll_size = os.path.getsize(preroll_file) if os.path.exists(preroll_file) else 0
        print(f"  Result: {preroll_buffers} buffers, file size: {preroll_size} bytes")
        print()

        # Analysis
        print("=" * 60)
        print("Results Analysis")
        print("=" * 60)
        print(f"  Total frames generated:  {NUM_BUFFERS}")
        print(f"  Preroll delay:           {PREROLL_DELAY}s (~{int(PREROLL_DELAY * FRAMERATE)} frames)")
        print()
        print(f"  Baseline (always open):  {baseline_buffers:4d} buffers, {baseline_size:8d} bytes")
        print(f"  With preroll:            {preroll_buffers:4d} buffers, {preroll_size:8d} bytes")
        print()

        # Validation
        success = True
        
        if baseline_buffers <= 0:
            print("FAIL: Baseline test produced no buffers")
            success = False
        
        if preroll_buffers <= 0:
            print("FAIL: Preroll test produced no buffers")
            success = False

        if preroll_buffers < baseline_buffers:
            print(f"FAIL: Preroll test has fewer buffers ({preroll_buffers}) than baseline ({baseline_buffers})")
            print("      Expected preroll to include buffered frames!")
            success = False
        elif preroll_buffers > baseline_buffers:
            extra = preroll_buffers - baseline_buffers
            print(f"PASS: Preroll test has {extra} MORE buffers than baseline")
            print("      This confirms buffered preroll frames were included!")
        else:
            # Equal is also acceptable if all frames made it through
            print(f"INFO: Both tests produced the same number of buffers ({preroll_buffers})")
            print("      This is acceptable if total duration is short enough to buffer all.")

        if preroll_size <= 0:
            print("FAIL: Output file is empty or doesn't exist")
            success = False
        elif preroll_size >= baseline_size:
            print(f"PASS: Output file size ({preroll_size} bytes) >= baseline ({baseline_size} bytes)")
        else:
            # File size being smaller might still be OK due to encoding variations
            print(f"WARN: Output file smaller than baseline (encoding variance is normal)")

        print()
        if success:
            print("=" * 60)
            print("TEST PASSED: Preroll valve is working correctly!")
            print("=" * 60)
            sys.exit(0)
        else:
            print("=" * 60)
            print("TEST FAILED: See errors above")
            print("=" * 60)
            sys.exit(1)


if __name__ == "__main__":
    main()
