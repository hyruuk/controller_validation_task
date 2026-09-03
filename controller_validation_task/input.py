"""Controller input: pyglet key press/release capture with timestamps.

The fMRI-compatible pad is read **as a keyboard**. An external AntiMicroX
profile (shipped in ``assets/``) maps each physical control to one of the
keystrokes ``u d l r a b x y``; this module captures those keystrokes with
press *and* release timestamps.

Why not ``psychopy.event.getKeys``? It reports keys pressed since the last
call and gives no release events at all, so it cannot measure how long a
button was held — which is the entire point of the long-press condition. We
install our own pyglet handlers instead, which see both edges.

Load-bearing side effect
------------------------
:func:`install` replaces PsychoPy's ``event._onPygletKey`` handler, so while it
is active **unmodified** keys no longer reach ``event.getKeys()``. Keys pressed
*with* a modifier are forwarded to PsychoPy's handler, which is what keeps the
operator's Ctrl+C / Ctrl+N / Ctrl+Q shortcuts working mid-run. Nothing
unmodified is forwarded — every key a participant can press belongs to the
task.

The consequence for scanner sync: ``sync.mode = "wait"`` with the keyboard
backend reads via ``event.getKeys`` and must therefore run *before*
:func:`install` (the session does this — sync happens between instructions and
the run).

Ported from ``task_stimuli/src/tasks/videogame.py:20-45``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

import pyglet
from psychopy import core, event, logging

if TYPE_CHECKING:  # pragma: no cover - typing only
    from psychopy.visual import Window

# Module-level because pyglet's hooks are plain functions, not bound methods.
# Entries are (key_name, psychopy_clock_time).
_keyPressBuffer: list[tuple[str, float]] = []
_keyReleaseBuffer: list[tuple[str, float]] = []


def _normalize_key(symbol: int) -> str:
    """Convert a pyglet key symbol to the lowercase name the design uses.

    ``pyglet.window.key.A`` -> ``"a"``, ``pyglet.window.key.UP`` -> ``"up"``.
    The lstrip removes the leading underscore pyglet puts on digits (``_1`` ->
    ``1``).
    """
    return pyglet.window.key.symbol_string(symbol).lower().lstrip("_")


def _on_pyglet_key_press(symbol: int, modifier: int) -> None:
    if modifier:
        # Forward modified keys so PsychoPy still sees the operator shortcuts;
        # Ctrl+Q is the only way out of a run, and it has to work mid-trial.
        event._onPygletKey(symbol, modifier)
    _keyPressBuffer.append((_normalize_key(symbol), core.getTime()))


def _on_pyglet_key_release(symbol: int, modifier: int) -> None:
    key = _normalize_key(symbol)
    logging.data(f"Keyrelease: {key}")
    _keyReleaseBuffer.append((key, core.getTime()))


def install(exp_win: Window) -> None:
    """Install the pyglet key hooks and clear any stale buffered events."""
    clear()
    exp_win.winHandle.on_key_press = _on_pyglet_key_press
    exp_win.winHandle.on_key_release = _on_pyglet_key_release


def uninstall(exp_win: Window) -> None:
    """Restore PsychoPy's default key handler. Idempotent."""
    exp_win.winHandle.on_key_press = event._onPygletKey


def clear() -> None:
    """Discard everything buffered so far."""
    _keyPressBuffer.clear()
    _keyReleaseBuffer.clear()


def drain(
    exp_win: Window, task_timer: core.MonotonicClock
) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
    """Return ``(presses, releases)` since the last drain, in task-clock time.

    Both buffers are cleared. Timestamps are recorded against PsychoPy's global
    clock and shifted into the task's reference frame here, so they are
    directly comparable with the flip times stored on each trial.

    The offset is read from the two clocks' reset times rather than by
    subtracting two ``getTime()`` calls (upstream's approach), which drifts by
    however long elapses between the two calls.
    """
    exp_win.winHandle.dispatch_events()

    offset = core.monotonicClock._timeAtLastReset - task_timer._timeAtLastReset
    presses = [(k, t + offset) for k, t in _keyPressBuffer]
    releases = [(k, t + offset) for k, t in _keyReleaseBuffer]

    _keyPressBuffer.clear()
    _keyReleaseBuffer.clear()
    return presses, releases


def translate(
    events: list[tuple[str, float]], key_map: Mapping[str, str]
) -> list[tuple[str, float]]:
    """Rename captured keystrokes to controller button names.

    The pad reaches us as a keyboard, and which keys it sends depends on the
    hardware and on whether an AntiMicroX-style profile is remapping it: a
    D-pad may arrive as ``left`` or as ``l``. ``key_map`` collapses those onto
    the button names used by ``design.keys`` and the controller layout.

    A key with no entry in the map is passed through **unchanged** rather than
    dropped, so a stray keystroke still shows up in ``all_keypresses`` and can
    be used to exclude a contaminated trial offline.

    >>> translate([("left", 1.0), ("esc", 2.0)], {"left": "l"})
    [('l', 1.0), ('esc', 2.0)]
    """
    return [(key_map.get(k, k), t) for k, t in events]


def first_match(events: list[tuple[str, float]], key: str) -> float | None:
    """Time of the first event matching ``key``, or ``None``.

    Only the first match counts: if a participant presses the cued button
    twice in one trial, the first press is the response and the rest is noise
    (still recorded in ``all_keypresses`` for offline exclusion).
    """
    for k, t in events:
        if k == key:
            return t
    return None
