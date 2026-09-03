"""Session orchestration: window, log, triggers, and the run loop.

:func:`run_session` is the whole experiment. It is written as a numbered
procedure rather than a class because it is read far more often than it is
extended — usually by someone debugging a rig at 8am.

Exit codes:

===  ======================================================
0    session completed (or was ended cleanly by the operator)
2    the controller assets / layout are unusable
130  the operator quit with Ctrl+Q
===  ======================================================

Operator shortcuts, live during every phase including the scanner wait:

======  =====================================
Ctrl+C  abort this run, continue to the next
Ctrl+N  restart this run
Ctrl+Q  quit the session
======  =====================================

Ctrl+Q is also registered as a PsychoPy global key, so it is caught even
between the frames the shortcut poller runs on.
"""

from __future__ import annotations

import logging as stdlogging
import sys
from collections.abc import Generator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from controller_validation_task import design as design_mod
from controller_validation_task import layout as layout_mod
from controller_validation_task import log_setup, markers, sync
from controller_validation_task.paths import BidsPaths

if TYPE_CHECKING:  # pragma: no cover - typing only
    from psychopy.visual import Window

    from controller_validation_task.settings import Settings
    from controller_validation_task.task import ButtonPressTask

log = stdlogging.getLogger(__name__)

EXIT_OK = 0
EXIT_BAD_ASSETS = 2
EXIT_QUIT = 130

#: Flush the session log about once a second at 60 Hz.
_FLUSH_EVERY_N_FRAMES = 60


@dataclass
class RunConfig:
    """Everything one session needs, assembled by the CLI."""

    subject: str
    session: str
    settings: Settings
    paths: BidsPaths
    log_file: Any = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------


def list_screens() -> list[tuple[int, int, int]]:
    """Return ``(index, width, height)`` for every attached screen.

    Falls back to a single 1920x1080 entry when pyglet cannot enumerate
    displays (headless CI, no X server), so callers never have to special-case
    the empty result.
    """
    try:
        import pyglet

        screens = pyglet.canvas.Display().get_screens()
    except Exception:  # noqa: BLE001 - headless or no pyglet display
        screens = []
    if not screens:
        return [(0, 1920, 1080)]
    return [(i, s.width, s.height) for i, s in enumerate(screens)]


def resolve_display(settings: Settings) -> tuple[tuple[int, int], int, bool]:
    """Decide the window size, screen index and fullscreen flag.

    Resolution order for the screen:

    1. ``display.screen_index`` in config.json / ``--screen`` / ``EXP_WIN_SCREEN``
    2. otherwise the **last** attached screen, which on a scanner rig is the
       projector rather than the operator's console.

    The size then defaults to *that screen's* native resolution, so a
    fullscreen run fills the display at its real resolution.
    ``display.window_size`` overrides it for windowed piloting.
    """
    screens = list_screens()
    requested = settings.display.screen_index
    if requested is not None and 0 <= requested < len(screens):
        idx = requested
    else:
        if requested is not None:
            log.warning(
                "display.screen_index=%d but only %d screen(s) detected; using screen %d.",
                requested,
                len(screens),
                len(screens) - 1,
            )
        idx = len(screens) - 1

    _, width, height = screens[idx]
    size = tuple(settings.display.window_size) if settings.display.window_size else (width, height)
    return size, idx, settings.display.fullscreen


def _build_window(settings: Settings) -> Window:
    from psychopy import visual

    size, screen_idx, fullscreen = resolve_display(settings)
    log.info(
        "Display: screen %d, %dx%d, %s (%d screen(s) detected).",
        screen_idx,
        size[0],
        size[1],
        "fullscreen" if fullscreen else "windowed",
        len(list_screens()),
    )

    win = visual.Window(
        size=size,
        screen=screen_idx,
        fullscr=fullscreen,
        color=(-1, -1, -1),
        colorSpace="rgb",
        gammaErrorPolicy="warn",
        units="pix",
        allowGUI=not fullscreen,
        waitBlanking=True,
    )
    win.mouseVisible = False
    try:
        win.winHandle.set_caption("controller_validation_task")
    except Exception:  # noqa: BLE001 - cosmetic only
        pass
    return win


# ---------------------------------------------------------------------------
# Frame loop
# ---------------------------------------------------------------------------


#: Set by the Ctrl+Q global key; consumed by :func:`_listen_shortcuts`.
_quit_requested = False


def _request_quit() -> None:
    """Remember a Ctrl+Q. Called by PsychoPy the instant the key is pressed.

    It only raises a flag — quitting still goes through the normal shortcut
    path, so the run's events file is written and every backend closed in
    order. Calling ``core.quit()`` here would kill the process mid-trial and
    lose the run.
    """
    global _quit_requested
    _quit_requested = True


def _install_quit_key() -> None:
    """Register Ctrl+Q with PsychoPy's global key handler.

    :func:`_listen_shortcuts` only sees a keypress on a frame it is polled on;
    a global key is dispatched by PsychoPy the moment the key arrives, so a
    Ctrl+Q during a blocking wait — the ``core.wait`` between instruction
    screens, say — is remembered rather than dropped.

    It is not an OS-level hotkey: the experiment window still has to have
    keyboard focus, because PsychoPy dispatches these from the same pyglet
    handler as everything else.
    """
    global _quit_requested
    _quit_requested = False
    from psychopy import event

    try:
        event.globalKeys.add(key="q", modifiers=["ctrl"], func=_request_quit, name="quit")
    except Exception:  # noqa: BLE001 - already registered, or no global keys
        log.debug("Could not register the Ctrl+Q global key.", exc_info=True)


def _remove_quit_key() -> None:
    """Unregister Ctrl+Q. ``globalKeys`` outlives the session otherwise."""
    from psychopy import event

    try:
        event.globalKeys.remove("q", modifiers=["ctrl"])
    except Exception:  # noqa: BLE001 - never registered, or already gone
        pass


def _listen_shortcuts() -> str | None:
    """Return ``"c"`` / ``"n"`` / ``"q"``, or ``None`` if nothing was pressed.

    Every shortcut requires Ctrl, without exception: during a run the
    participant's unmodified keystrokes are captured by
    :mod:`controller_validation_task.input` and never reach PsychoPy's buffer,
    and a bare key is far more likely to be a stray keystroke than a decision.
    Ctrl+Q is the one way out — Escape does nothing.

    Passing an explicit key list to ``getKeys`` matters: PsychoPy only drops
    the keys it was asked about and leaves the rest in the buffer, so polling
    for shortcuts every frame cannot swallow the scanner trigger the sync
    waiter is watching for.
    """
    global _quit_requested
    from psychopy import event

    if _quit_requested:
        _quit_requested = False
        return "q"

    for name, mods in event.getKeys(["n", "c", "q"], modifiers=True):
        if mods.get("ctrl"):
            return name
    return None


def _drive(generator: Generator) -> str | None:
    """Run a frame generator to completion, watching for operator shortcuts.

    Returns the shortcut that interrupted it, or ``None`` if it finished.
    """
    for frame_n, _ in enumerate(generator):
        shortcut = _listen_shortcuts()
        if shortcut:
            return shortcut
        if frame_n % _FLUSH_EVERY_N_FRAMES == 0:
            log_setup.flush()
    return None


def _run_task(
    task: ButtonPressTask,
    exp_win: Window,
    sync_obj: sync.Sync,
    *,
    use_markers: bool,
) -> str | None:
    """Instructions -> sync -> markers -> run -> stop -> save.

    ``save()`` runs in a ``finally`` so an aborted or crashed run still leaves
    its partial events file behind.
    """
    from psychopy import logging as psylog

    print(f"Next task: {task.name}")
    shortcut = _drive(task.instructions(exp_win))

    # Sync sits between the instructions and the run: the keyboard waiter
    # reads via event.getKeys, which stops working once input.install()
    # replaces the pyglet handler at the top of task.run().
    if not shortcut:
        shortcut = _drive(sync_obj.start(exp_win))

    psylog.exp(f"task_start name={task.name}")
    try:
        if not shortcut:
            if use_markers:
                exp_win.callOnFlip(markers.send_signal, markers.TASK_START, markers.now())
            shortcut = _drive(task.run(exp_win))
        if use_markers:
            exp_win.callOnFlip(markers.send_signal, markers.TASK_STOP, markers.now())
        _drive(task.stop(exp_win))
    finally:
        psylog.exp(f"task_stop name={task.name}")
        task.save()
    return shortcut


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


def run_session(config: RunConfig) -> int:
    """Run every run of one session. Returns a process exit code."""
    # Imported here, not at module scope: psychopy parses sys.argv at import
    # time (its preferences module installs its own --help), so importing it
    # before argparse has run would hijack this program's command line.
    from controller_validation_task.task import ButtonPressTask

    settings = config.settings

    # 1. Controller assets must be usable before anything is opened.
    layout_path = _resolve_layout(settings)
    try:
        layout = layout_mod.load_layout(layout_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Cannot load the controller layout:\n{exc}", file=sys.stderr)
        return EXIT_BAD_ASSETS

    problem = layout_mod.validate_layout(
        layout,
        settings.design.keys,
        conditions=settings.design.conditions,
        lr_mode=settings.design.lr_mode,
    )
    if problem:
        print(f"Controller layout problem:\n{problem}", file=sys.stderr)
        return EXIT_BAD_ASSETS

    # 2. Output directories.
    config.paths.sourcedata_session_dir.mkdir(parents=True, exist_ok=True)

    # 3. Session log. Held as a local for the whole scope: psychopy only keeps
    #    a weak reference and stops logging if it is collected.
    log_file = log_setup.create_session_log(config.paths.log_path)
    config.log_file = log_file

    # 4. Triggers and scanner sync. Neither can raise.
    stream = markers.StreamConfig(
        name=settings.triggers.lsl_stream_name,
        type=settings.triggers.lsl_stream_type,
        source_id=settings.triggers.lsl_stream_source_id,
    )
    markers.configure(
        backend=settings.triggers.backend,
        port=settings.triggers.port,
        stream=stream,
        codes=settings.triggers.codes,
        keys=settings.design.keys,
    )
    use_markers = settings.triggers.backend != "null"
    sync_obj = sync.configure(settings.sync, stream=stream)

    # 5. Window, and the quit key that has to outlive every phase in it.
    exp_win = _build_window(settings)
    _install_quit_key()

    exit_code = EXIT_OK
    try:
        # 6. One task per run, with the design generated on demand.
        run = 1
        while run <= settings.design.n_runs:
            design_df = design_mod.ensure_design(
                config.paths.design_tsv(run),
                subject=config.subject,
                session=config.session,
                run=run,
                params=settings.design,
                halves=layout.halves,
            )
            task = ButtonPressTask(
                name=f"task-gamepad_run-{run:02d}",
                design=design_df,
                run_id=run,
                layout=layout,
                settings=settings,
                paths=config.paths,
                use_markers=use_markers,
            )
            log.info(
                "Run %d/%d: %d trials, ~%.1f min",
                run,
                settings.design.n_runs,
                len(design_df),
                design_mod.run_duration(design_df, settings.design.final_wait) / 60.0,
            )

            task.setup(exp_win)
            try:
                shortcut = _run_task(task, exp_win, sync_obj, use_markers=use_markers)
            finally:
                task.unload()

            # 7. Act on the operator's shortcut.
            if shortcut == "q":
                log.info("Operator quit (Ctrl+Q).")
                exit_code = EXIT_QUIT
                break
            if shortcut == "n":
                log.info("Restarting run %d (Ctrl+N).", run)
                continue  # same run number, fresh task
            if shortcut == "c":
                log.info("Run %d aborted (Ctrl+C); continuing.", run)
            run += 1
    finally:
        # 8. Tear down in reverse order, never masking an in-flight exception.
        _remove_quit_key()
        try:
            exp_win.close()
        except Exception:  # noqa: BLE001
            pass
        sync_obj.close()
        markers.close()
        log_setup.close()

    return exit_code


def _resolve_layout(settings: Settings):
    from controller_validation_task.paths import resolve_layout_path

    return resolve_layout_path(settings.paths.layout_file, settings.paths.assets_dir)
