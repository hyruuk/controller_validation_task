"""Frame-accurate waiting helpers.

The trial loop schedules each flip against the task clock rather than
counting frames, so a dropped frame doesn't shift every subsequent trial.
:func:`wait_until` is the primitive that makes that work: it spins until the
target time while keeping the window's event queue drained, so key presses
and releases are still timestamped accurately during the wait.

Ported from ``task_stimuli/src/shared/utils.py``.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from psychopy.core import MonotonicClock

#: How long to sleep between polls. Short enough that we never overshoot a
#: 60 Hz retrace by a meaningful amount, long enough not to peg a core.
_POLL_INTERVAL = 0.0001


def poll_windows() -> None:
    """Dispatch pending events on every open pyglet window.

    Without this the OS event queue backs up during a long wait and key
    release timestamps arrive late (or the window appears frozen to the
    desktop compositor).
    """
    # Imported lazily: this module must stay importable without a display.
    import pyglet

    for win in pyglet.canvas.get_display().get_windows():
        try:
            win.dispatch_events()
        except Exception:  # noqa: BLE001 - a closing window must not kill a run
            pass


def wait_until(clock: MonotonicClock, target: float, *, poll: bool = True) -> float:
    """Block until ``clock`` reads ``target`` seconds. Returns the overshoot.

    A negative-or-zero wait returns immediately, so a trial that is already
    late simply proceeds rather than sleeping for a negative duration. The
    returned overshoot (actual - target) is useful for logging timing slip.

    Args:
        clock:  The task clock; ``target`` is in its reference frame.
        target: Absolute time on ``clock`` to wait for.
        poll:   Drain window events while waiting. Turn off only when no
                window exists yet (e.g. in tests).
    """
    while True:
        now = clock.getTime()
        remaining = target - now
        if remaining <= 0:
            return now - target
        if poll:
            poll_windows()
        # Sleep at most the remaining time so we don't overshoot the target.
        time.sleep(min(_POLL_INTERVAL, remaining))


def wait_seconds(clock: MonotonicClock, duration: float, **kwargs: Any) -> float:
    """Convenience wrapper: wait ``duration`` seconds from *now* on ``clock``."""
    return wait_until(clock, clock.getTime() + duration, **kwargs)
