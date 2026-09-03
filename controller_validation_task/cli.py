"""Command-line entry point.

Resolves subject / session and the effective settings, then hands off to
:func:`controller_validation_task.session.run_session`.

Two conveniences make the common case a single word:

* No ``config.json`` (or ``--reconfigure``) opens the config wizard.
* No SUBJECT opens the subject picker; an omitted SESSION becomes the next
  unused number for that subject.

So ``bash run.sh`` is enough on a fresh machine, and ``bash run.sh 01`` is
enough on every subsequent visit.
"""

from __future__ import annotations

import argparse
import logging
import sys

from dotenv import load_dotenv

from controller_validation_task import __version__
from controller_validation_task import settings as settings_mod
from controller_validation_task.paths import (
    BidsPaths,
    infer_next_session,
    make_timestamp,
    normalize_session,
    normalize_subject,
)
from controller_validation_task.session import RunConfig, run_session

# .env feeds the environment layer of the settings precedence chain.
load_dotenv()

log = logging.getLogger(__name__)


def _parse_window_size(text: str) -> tuple[int, int]:
    """Parse a ``WxH`` window size for argparse.

    >>> _parse_window_size("1280x720")
    (1280, 720)
    """
    try:
        w, h = text.lower().split("x")
        size = (int(w), int(h))
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"window size must look like 1280x720, got {text!r}"
        ) from None
    if size[0] <= 0 or size[1] <= 0:
        raise argparse.ArgumentTypeError(f"window size must be positive, got {text!r}")
    return size


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="controller-validation-task",
        description=(
            "Controller validation task: cue button presses of short and long "
            "durations to validate an fMRI/MEG/EEG-compatible game controller."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "subject",
        nargs="?",
        help="Subject label, with or without the 'sub-' prefix. Omit to open the subject picker.",
    )
    p.add_argument(
        "session",
        nargs="?",
        help=(
            "Session label, with or without the 'ses-' prefix. A bare number is "
            "zero-padded to 3 digits. Omit to use the next unused session for this subject."
        ),
    )

    p.add_argument("--output", dest="output_root", help="Root of the BIDS output tree.")
    p.add_argument("--runs", dest="n_runs", type=int, help="Number of runs in this session.")
    p.add_argument("--layout", dest="layout_file", help="Controller layout JSON to use.")

    lr = p.add_mutually_exclusive_group()
    lr.add_argument(
        "--lr",
        dest="lr_mode",
        action="store_const",
        const=True,
        default=None,
        help="Left/right hand mode: dim half the pad and cue only the lit hand.",
    )
    lr.add_argument(
        "--no-lr", dest="lr_mode", action="store_const", const=False, help="Disable lr mode."
    )

    p.add_argument(
        "--sync-mode",
        dest="sync_mode",
        choices=("send", "wait", "none"),
        help="none: start immediately. wait: wait for the sync signal. send: start the scanner.",
    )
    p.add_argument(
        "--sync-backend",
        dest="sync_backend",
        choices=("none", "serial", "parallel", "lsl", "key", "keyboard", "markers"),
        help="Transport for the start signal ('markers' re-uses the trigger port).",
    )
    p.add_argument("--sync-port", dest="sync_port", help="Port for the sync backend.")
    p.add_argument(
        "--sync-signal",
        dest="sync_signal",
        type=lambda v: tuple(x.strip() for x in v.split(",") if x.strip()),
        help="The sync signal: what to send, or what to wait for. Comma-separated to accept alternatives (e.g. '5,percent').",
    )

    p.add_argument(
        "--trigger-backend",
        dest="trigger_backend",
        choices=("lsl", "serial", "parallel", "null"),
        help="Transport for outgoing event markers.",
    )
    p.add_argument("--trigger-port", dest="trigger_port", help="Port for the trigger backend.")

    screen = p.add_mutually_exclusive_group()
    screen.add_argument(
        "--no-fullscreen",
        dest="fullscreen",
        action="store_const",
        const=False,
        default=None,
        help="Run in a window. Useful for piloting on a laptop.",
    )
    screen.add_argument(
        "--fullscreen",
        dest="fullscreen",
        action="store_const",
        const=True,
        help="Force fullscreen at the screen's native resolution.",
    )
    p.add_argument(
        "--screen",
        dest="screen_index",
        type=int,
        help="Monitor to display on (0-based). Default: the last screen detected.",
    )
    p.add_argument(
        "--window-size",
        dest="window_size",
        type=_parse_window_size,
        metavar="WxH",
        help="Window size, e.g. 1280x720. Default: the chosen screen's resolution.",
    )
    p.add_argument(
        "--instruction-duration",
        dest="instruction_duration",
        type=float,
        help="Seconds each instruction screen stays on screen.",
    )
    p.add_argument(
        "--list-screens",
        action="store_true",
        help="Print the detected monitors and exit.",
    )
    p.add_argument(
        "--reconfigure", action="store_true", help="Re-run the configuration wizard."
    )
    p.add_argument("-v", "--verbose", action="store_true", help="Debug-level console logging.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


#: argparse dests that map onto settings fields (see settings._CLI_KEYS).
_OVERRIDE_DESTS = (
    "output_root",
    "layout_file",
    "n_runs",
    "lr_mode",
    "fullscreen",
    "screen_index",
    "window_size",
    "instruction_duration",
    "sync_mode",
    "sync_backend",
    "sync_port",
    "sync_signal",
    "trigger_backend",
    "trigger_port",
)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    args = _build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )

    # None means "flag not given", so those keys are dropped and the lower
    # precedence layers (env, config.json, defaults) win.
    cli_overrides = {
        dest: getattr(args, dest)
        for dest in _OVERRIDE_DESTS
        if getattr(args, dest, None) is not None
    }

    if args.list_screens:
        from controller_validation_task.session import list_screens

        screens = list_screens()
        print(f"{len(screens)} screen(s) detected:")
        for idx, width, height in screens:
            default = " (default)" if idx == len(screens) - 1 else ""
            print(f"  --screen {idx}   {width}x{height}{default}")
        return 0

    config_path = settings_mod.config_path_default()
    if args.reconfigure or not config_path.exists():
        from controller_validation_task import gui

        reason = "--reconfigure" if args.reconfigure else "no config.json found"
        log.info("Launching the configuration wizard (%s).", reason)
        if gui.run_config_wizard(config_path) is None:
            log.info("Configuration cancelled; exiting.")
            return 0

    try:
        settings = settings_mod.load(config_path=config_path, cli_overrides=cli_overrides)
    except ValueError as exc:
        print(f"Configuration error:\n{exc}", file=sys.stderr)
        print(
            "\nFix config.json, or re-run with --reconfigure to regenerate it.",
            file=sys.stderr,
        )
        return 2

    if args.subject is None:
        from controller_validation_task import gui

        picked = gui.pick_subject(settings.paths.output_root)
        if picked is None:
            log.info("Subject selection cancelled; exiting.")
            return 0
        subject, session = picked
    else:
        subject = normalize_subject(args.subject)
        session = (
            normalize_session(args.session)
            if args.session
            else infer_next_session(settings.paths.output_root, subject)
        )

    try:
        paths = BidsPaths(
            subject=subject,
            session=session,
            output_root=settings.paths.output_root,
            timestamp=make_timestamp(),
        )
    except ValueError as exc:
        print(f"Invalid subject/session: {exc}", file=sys.stderr)
        return 2

    log.info(
        "Session sub-%s ses-%s%s | %d run(s) | sync=%s | triggers=%s",
        subject,
        session,
        " (auto)" if args.session is None and args.subject is not None else "",
        settings.design.n_runs,
        settings.sync.mode,
        settings.triggers.backend,
    )

    return run_session(RunConfig(subject=subject, session=session, settings=settings, paths=paths))


if __name__ == "__main__":
    raise SystemExit(main())
