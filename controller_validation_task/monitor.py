"""Live marker monitor: ``python -m controller_validation_task.monitor``.

Subscribes to the task's LSL outlet and prints each marker as it arrives,
decoded into a human-readable label. Run it in a second terminal before a
participant arrives to confirm the trigger chain end to end — that the stream
appears, that ``task_start`` fires, and that a trial produces an onset/offset
pair.

It reads ``config.json`` so its decoding always matches the code scheme the
experiment is actually using.
"""

from __future__ import annotations

import argparse
import sys
import time

from controller_validation_task import markers
from controller_validation_task import settings as settings_mod


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m controller_validation_task.monitor",
        description="Print controller-validation markers from the LSL stream as they arrive.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--stream", help="Stream name to resolve. Default: read from config.json.")
    p.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Seconds to wait for the stream to appear before giving up.",
    )
    p.add_argument(
        "--config", default="config.json", help="Config file to read the code scheme from."
    )
    p.add_argument("--stats", action="store_true", help="Print a tally on exit (Ctrl+C).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        settings = settings_mod.load(config_path=args.config)
    except (OSError, ValueError) as exc:
        print(f"Could not read {args.config} ({exc}); using default codes.", file=sys.stderr)
        settings = settings_mod.default_settings()

    markers.set_codes(settings.triggers.codes)
    markers.set_keys(settings.design.keys)
    stream_name = args.stream or settings.triggers.lsl_stream_name

    try:
        import pylsl
    except ImportError:
        print("pylsl is not installed; cannot monitor an LSL stream.", file=sys.stderr)
        return 2

    print(f"Looking for LSL stream {stream_name!r} (timeout {args.timeout:g}s)…")
    streams = pylsl.resolve_byprop("name", stream_name, timeout=args.timeout)
    if not streams:
        print(
            f"No stream named {stream_name!r} found.\n"
            f"Start the task with --trigger-backend lsl, and check that "
            f"triggers.lsl_stream_name matches.",
            file=sys.stderr,
        )
        return 1

    inlet = pylsl.StreamInlet(streams[0])
    print(f"Connected. Press Ctrl+C to stop.\n{'time':>12}  {'value':>5}  label")

    counts: dict[str, int] = {}
    t0 = time.monotonic()
    try:
        while True:
            sample, timestamp = inlet.pull_sample(timeout=1.0)
            if sample is None:
                continue
            value = int(sample[0])
            label = markers.decode_marker(value)
            counts[label] = counts.get(label, 0) + 1
            print(f"{time.monotonic() - t0:12.3f}  {value:5d}  {label}")
    except KeyboardInterrupt:
        print()
        if args.stats:
            print("Markers received:")
            for label, n in sorted(counts.items(), key=lambda kv: -kv[1]):
                print(f"  {n:6d}  {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
