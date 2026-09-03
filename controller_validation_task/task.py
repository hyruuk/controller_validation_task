"""The button-press trial loop.

:class:`_TaskBase` is a frame-generator harness: the public ``instructions`` /
``run`` / ``stop`` methods are generators that yield once per frame, and the
session driver flips the window between yields. Subclasses implement the
``_instructions`` / ``_run`` / ``_stop`` counterparts and yield a
``clearBuffer`` flag each frame. This keeps the operator's shortcuts live
during every phase of the task, including long waits.

:class:`ButtonPressTask` is a direct port of ``task_stimuli``'s
``ButtonPressTask`` (``gamepad.py``). The trial structure is deliberately
unchanged — three flips per trial:

1. controller + cue (immediately, so the cue is visible before the button)
2. controller + cue + **highlighted button**, scheduled at ``onset``
3. controller + cue, highlight removed, scheduled at ``onset + duration``

Each timed flip is scheduled one frame early (``- 1/frame_rate``) so the
*rendered* frame lands on the intended time rather than one refresh late.
Timing is against the task clock throughout, so a dropped frame doesn't shift
every subsequent trial.

After flip 3 the loop waits ``response_window`` seconds, then drains the key
buffers **once**. Everything captured in that window is attributed to the
trial, which is why ``all_keypresses`` / ``all_keyreleases`` are recorded in
full: a trial contaminated by a stray press of another button can be excluded
offline.

Do not refactor the flip order or the ``wait_until`` targets without
re-validating against the original task — the measured ``onset_flip`` /
``offset_flip`` columns are what downstream analysis regresses against.
"""

from __future__ import annotations

import logging as stdlogging
from collections.abc import Generator, Sequence
from typing import TYPE_CHECKING, Any

from psychopy import core, logging, visual

from controller_validation_task import events as events_mod
from controller_validation_task import input as input_mod
from controller_validation_task import layout as layout_mod
from controller_validation_task import markers
from controller_validation_task.timing import wait_until

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandas as pd
    from psychopy.visual import Window

    from controller_validation_task.layout import ControllerLayout
    from controller_validation_task.paths import BidsPaths
    from controller_validation_task.settings import Settings

log = stdlogging.getLogger(__name__)

#: Shown before every run.
DEFAULT_INSTRUCTION = (
    "You will be asked to press the left and right hand buttons of the "
    "controller for short or long durations."
)

#: Shown on the first run only, over the controller image with the long cue.
LONG_INSTRUCTION = (
    "The long bar indicates long keypresses blocks,\n"
    "you need to time the press and the release to the button that light-up"
)

#: Shown on the first run only, over the controller image with the short cue.
SHORT_INSTRUCTION = (
    "The dot indicates short keypresses,\n"
    "You have to press and release immediately the button that light-up."
)

#: Text wrap width, in NORMALISED units (2.0 = the full window width).
WRAP_WIDTH = 2

#: Instruction text is positioned and wrapped in ``norm`` units, NOT the
#: window's ``pix``. The window has to be in pixels for the controller
#: geometry, but text laid out in pixels would take `wrapWidth=2` as two
#: pixels (a line break after every word) and `pos=(0, -0.75)` as most of a
#: pixel below centre (text on top of the controller). Always pass units
#: explicitly on a TextStim here.
TEXT_UNITS = "norm"

#: Height of the instruction text, in ``norm`` units.
TEXT_HEIGHT = 0.06

#: Caption position for the cue-explanation screens: below the controller.
CAPTION_POS = (0, -0.75)


def _absolute(flip_time: float) -> float:
    """Convert a PsychoPy flip time to the absolute monotonic timebase.

    ``Window.timeOnFlip`` stamps its value from ``logging.defaultClock``, whose
    zero point depends on whether (and when) the session log was opened. The
    task clock and :func:`controller_validation_task.input.drain` both reason
    in the absolute timebase, so convert once here rather than relying on the
    default clock happening to have been reset to 0 first.
    """
    return flip_time + logging.defaultClock._timeAtLastReset


# ---------------------------------------------------------------------------
# Base harness
# ---------------------------------------------------------------------------


class _TaskBase:
    """Frame-generator lifecycle shared by every task in this repo.

    Subclasses override ``_setup`` / ``_instructions`` / ``_run`` / ``_stop``.
    The public methods are final: they own the window flips, the task clock,
    and the completion flags.
    """

    def __init__(self, *, name: str, use_markers: bool = False) -> None:
        self.name = name
        self.use_markers = use_markers
        self.task_timer: core.MonotonicClock | None = None
        self._events: list[dict] = []
        self._exp_win_first_flip_time: float = 0.0
        self._exp_win_last_flip_time: float = 0.0
        self._task_completed = False

    # ----- lifecycle -----

    def setup(self, exp_win: Window, **kwargs: Any) -> None:
        self._setup(exp_win, **kwargs)

    def instructions(self, exp_win: Window) -> Generator[None, None, None]:
        gen = self._instructions(exp_win)
        if gen is None:
            return
        for clear_buffer in gen:
            yield
            self._flip(exp_win, clear_buffer)

    def run(self, exp_win: Window) -> Generator[None, None, None]:
        # timeOnFlip stamps the attribute at the moment of the vsync, which is
        # the only accurate zero point for the task clock.
        exp_win.timeOnFlip(self, "_exp_win_first_flip_time")
        self._flip(exp_win, True)
        self.task_timer = core.MonotonicClock(_absolute(self._exp_win_first_flip_time))

        for clear_buffer in self._run(exp_win):
            yield
            self._flip(exp_win, clear_buffer)
        self._task_completed = True

    def stop(self, exp_win: Window) -> Generator[None, None, None]:
        gen = self._stop(exp_win)
        if gen is None:
            return
        for clear_buffer in gen:
            yield
            self._flip(exp_win, clear_buffer)

    def save(self) -> None:
        """Write this task's events. Subclasses override the format."""

    def unload(self) -> None:
        """Release any resources held by the task. Safe to call twice."""

    # ----- helpers -----

    def _flip(self, exp_win: Window, clear_buffer: bool = True) -> None:
        exp_win.timeOnFlip(self, "_exp_win_last_flip_time")
        exp_win.flip(clearBuffer=clear_buffer)

    def _flip_time(self) -> float:
        """Last flip, relative to the run's first flip."""
        return self._exp_win_last_flip_time - self._exp_win_first_flip_time

    # ----- subclass hooks -----

    def _setup(self, exp_win: Window, **kwargs: Any) -> None:
        return None

    def _instructions(self, exp_win: Window) -> Generator[bool, None, None] | None:
        return None

    def _run(self, exp_win: Window) -> Generator[bool, None, None]:
        raise NotImplementedError

    def _stop(self, exp_win: Window) -> Generator[bool, None, None] | None:
        return None


# ---------------------------------------------------------------------------
# The task
# ---------------------------------------------------------------------------


class ButtonPressTask(_TaskBase):
    """One run of the controller validation task.

    Args:
        name:      BIDS task name, e.g. ``"task-gamepad_run-01"``.
        design:    The run's trial sequence (see :mod:`.design`).
        run_id:    1-indexed run number. The extra explanation screens are
                   shown on run 1 only.
        layout:    Parsed controller layout.
        settings:  Full settings; the design/display/trigger sections are read.
        paths:     Output paths, used by :meth:`save`.
    """

    def __init__(
        self,
        *,
        name: str,
        design: pd.DataFrame,
        run_id: int,
        layout: ControllerLayout,
        settings: Settings,
        paths: BidsPaths,
        use_markers: bool = False,
    ) -> None:
        super().__init__(name=name, use_markers=use_markers)
        self.design = design
        self.run_id = run_id
        self.layout = layout
        self.settings = settings
        self.paths = paths

        self.lr_mode = "lr_condition" in design.columns
        self._trials: list[dict] = design.to_dict("records")
        self._stims: dict[str, Any] = {}
        self._handlers_installed = False

    # ----- setup -----

    def _setup(self, exp_win: Window, **kwargs: Any) -> None:
        self._stims = layout_mod.build_stimuli(exp_win, self.layout, lr_mode=self.lr_mode)

    def unload(self) -> None:
        self._stims = {}

    # ----- instructions -----

    def _instructions(self, exp_win: Window) -> Generator[bool, None, None]:
        duration = self.settings.display.instruction_duration
        text = visual.TextStim(
            exp_win,
            text=DEFAULT_INSTRUCTION,
            alignText="center",
            color="white",
            units=TEXT_UNITS,
            height=TEXT_HEIGHT,
            wrapWidth=WRAP_WIDTH,
        )
        text.draw(exp_win)
        yield True
        core.wait(duration)

        # The cue explanations only need to be shown once per session.
        if self.run_id != 1:
            return

        for message, cue_name in ((LONG_INSTRUCTION, "long"), (SHORT_INSTRUCTION, "short")):
            caption = visual.TextStim(
                exp_win,
                text=message,
                alignText="center",
                color="white",
                units=TEXT_UNITS,
                height=TEXT_HEIGHT,
                pos=CAPTION_POS,
                wrapWidth=WRAP_WIDTH,
            )
            self._stims["image"].draw(exp_win)
            caption.draw(exp_win)
            cue = self._stims["cues"].get(cue_name)
            if cue is not None:
                cue.draw(exp_win)
            yield True
            core.wait(duration)
        yield True

    # ----- the trial loop -----

    def _run(self, exp_win: Window) -> Generator[bool, None, None]:
        input_mod.install(exp_win)
        self._handlers_installed = True

        frame_rate = self.settings.display.frame_rate
        one_frame = 1.0 / frame_rate
        response_window = self.settings.design.response_window
        keys = list(self.settings.design.keys)
        key_map = self.settings.input.key_map

        image = self._stims["image"]
        cues = self._stims["cues"]
        buttons = self._stims["buttons"]

        trial: dict = {}
        for trial_n, trial in enumerate(self._trials):
            condition = trial["condition"]
            key = trial["key"]
            cue = cues[condition]

            if self.lr_mode:
                layout_mod.apply_half_mask(image, self.layout, trial["lr_condition"])

            # --- flip 1: cue visible, no button lit yet ---
            image.draw(exp_win)
            cue.draw(exp_win)
            yield True

            exp_win.logOnFlip(level=logging.EXP, msg=f"trial {trial_n}: {condition} {key}")

            # --- flip 2: light the cued button, scheduled at `onset` ---
            image.draw(exp_win)
            cue.draw(exp_win)
            buttons[key].draw(exp_win)
            wait_until(self.task_timer, trial["onset"] - one_frame)
            if self.use_markers and self.settings.triggers.on_trial_onset:
                exp_win.callOnFlip(
                    markers.send_signal, markers.encode_trial_onset(condition), markers.now()
                )
            yield True
            trial["onset_flip"] = self._flip_time()

            # --- flip 3: unlight, scheduled at `onset + duration` ---
            image.draw(exp_win)
            cue.draw(exp_win)
            wait_until(self.task_timer, trial["onset"] + trial["duration"] - one_frame)
            if self.use_markers and self.settings.triggers.on_trial_offset:
                exp_win.callOnFlip(markers.send_signal, markers.TRIAL_OFFSET, markers.now())
            yield True
            trial["offset_flip"] = self._flip_time()

            # --- collect the response ---
            # One drain per trial, `response_window` after the button goes
            # dark, so a release that lands just after the offset still counts.
            wait_until(self.task_timer, trial["offset_flip"] + response_window)
            presses, releases = input_mod.drain(exp_win, self.task_timer)
            # Captured keystrokes carry keyboard names ("left"); the design and
            # the layout speak button names ("l"). Translate before matching.
            presses = input_mod.translate(presses, key_map)
            releases = input_mod.translate(releases, key_map)
            self._score_trial(trial, presses, releases, keys)

            log.debug(
                "trial %d: %s %s rt=%s", trial_n, condition, key, trial.get("key_press_rt")
            )

        # Hold the final screen so the last trial's haemodynamic response is
        # captured before the run ends.
        if trial:
            wait_until(
                self.task_timer,
                trial["onset"] + trial["duration"] + self.settings.design.final_wait,
            )
        input_mod.uninstall(exp_win)
        self._handlers_installed = False
        yield True

    def _score_trial(
        self,
        trial: dict,
        presses: list[tuple[str, float]],
        releases: list[tuple[str, float]],
        keys: Sequence[str],
    ) -> None:
        """Fill in the response columns for one trial.

        Only the *first* press and release of the cued button count; the full
        lists are stored so contaminated trials can be excluded offline.
        """
        key = trial["key"]
        press_t = input_mod.first_match(presses, key)
        release_t = input_mod.first_match(releases, key)

        # Seed every response column so a trial with no response still writes
        # empty cells rather than being absent from the row.
        for column in events_mod.RESPONSE_COLUMNS:
            trial.setdefault(column, None)

        if press_t is not None:
            trial["key_press_time"] = press_t
            trial["key_press_rt"] = press_t - trial["onset_flip"]
        if release_t is not None:
            trial["key_release_time"] = release_t
            # Meaningful mainly for long presses, but computed for both so the
            # column is never conditionally absent.
            trial["key_release_rt"] = release_t - trial["offset_flip"]
            if press_t is not None:
                trial["key_duration"] = release_t - press_t

        trial["all_keypresses"] = presses
        trial["all_keyreleases"] = releases

        if self.use_markers and self.settings.triggers.on_keypress and press_t is not None:
            try:
                markers.send_signal(markers.encode_key(key, keys), markers.now())
            except ValueError:
                # A cued key outside the configured marker order: log rather
                # than abort, the trial data is still valid.
                log.warning("no marker code for key %r; skipping keypress marker", key)

    # ----- teardown -----

    def _stop(self, exp_win: Window) -> Generator[bool, None, None]:
        if self._handlers_installed:
            input_mod.uninstall(exp_win)
            self._handlers_installed = False
        yield True

    def save(self) -> None:
        """Write the events TSV for this run."""
        path = self.paths.events_tsv(self.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        n = events_mod.write_events_tsv(self._trials, path, lr_mode=self.lr_mode)
        logging.exp(f"saved {n} trials to {path}")
        log.info("Wrote %d trials to %s", n, path)
