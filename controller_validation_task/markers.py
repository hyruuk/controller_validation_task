"""Outgoing event markers for MEG / EEG / LSL recordings.

Four interchangeable transports sit behind one :class:`_Backend` protocol:

======== ==========================================================
lsl      pylsl outlet; the richest option (markers carry timestamps)
serial   one byte over a serial port
parallel one byte on a parallel port's data lines
null     drop everything (behaviour-only runs)
======== ==========================================================

Two rules govern this module:

1. **Never crash a live session.** :func:`configure` catches every failure —
   a missing library, an absent port, a permissions error — logs a warning and
   substitutes the null backend. Losing markers is bad; losing a scanner slot
   because the task refused to start is worse.
2. **Codes are configurable but always one byte.** Serial and parallel can
   only carry 0..255, so every code is validated into that range by
   :func:`controller_validation_task.settings._validate`.

Marker scheme
-------------
Lifecycle codes are small and fixed; per-key codes occupy a contiguous span
starting at ``key_base``, one per entry of ``design.keys``::

    task_start=1  task_stop=2
    trial_onset_short=10  trial_onset_long=11  trial_offset=12
    key 'r'=20  key 'l'=21  key 'u'=22  ...

Do not ``from ... import TASK_START``: the module-level constants are served
by :func:`__getattr__` from the *currently configured* codes, so a static
import would freeze whatever the defaults were at import time. Always write
``markers.TASK_START``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)

#: Whether markers are emitted on the window flip that renders the event
#: (rather than when the code decides to send). Kept as a module flag so the
#: task can be run without markers by flipping one switch.
MARKERS_ON_FLIP = True


@dataclass(frozen=True)
class TriggerCodes:
    """The marker value for each event type.

    ``key_base`` is the first of ``len(design.keys)`` consecutive per-key
    codes; see :func:`encode_key`.
    """

    task_start: int = 1
    task_stop: int = 2
    trial_onset_short: int = 10
    trial_onset_long: int = 11
    trial_offset: int = 12
    key_base: int = 20


@dataclass(frozen=True)
class StreamConfig:
    """LSL stream identity. Ignored by the non-LSL backends."""

    name: str = "controller_validation"
    type: str = "Markers"
    source_id: str = "controller_validation_markers"


# ---------------------------------------------------------------------------
# Backend protocol
# ---------------------------------------------------------------------------


class _Backend(Protocol):
    def send(self, value: int, timestamp: float | None = None) -> None: ...

    def close(self) -> None: ...


class _LSLBackend:
    """pylsl outlet. Markers carry an explicit timestamp when given one."""

    def __init__(self, stream: StreamConfig) -> None:
        # Imported here so a rig that never uses LSL needs no pylsl.
        import pylsl

        info = pylsl.StreamInfo(
            name=stream.name,
            type=stream.type,
            channel_count=1,
            nominal_srate=pylsl.IRREGULAR_RATE,
            channel_format=pylsl.cf_int32,
            source_id=stream.source_id,
        )
        channel = info.desc().append_child("channels").append_child("channel")
        channel.append_child_value("label", "marker")
        channel.append_child_value("unit", "code")
        channel.append_child_value("type", "Marker")
        self._outlet = pylsl.StreamOutlet(info)

    def send(self, value: int, timestamp: float | None = None) -> None:
        # pylsl reads 0.0 as "stamp it at push time"; passing an explicit
        # local_clock() value pins the sample to when the event happened.
        self._outlet.push_sample([int(value)], 0.0 if timestamp is None else float(timestamp))

    def close(self) -> None:
        self._outlet = None


class _SerialBackend:
    def __init__(self, port_address: str) -> None:
        import serial

        self._port = serial.Serial(port_address)

    def send(self, value: int, timestamp: float | None = None) -> None:
        self._port.write((int(value) & 0xFF).to_bytes(1, byteorder="big"))

    def close(self) -> None:
        try:
            self._port.close()
        except Exception:  # noqa: BLE001 - closing must never raise at teardown
            pass


class _ParallelBackend:
    def __init__(self, port_address: str) -> None:
        import parallel  # pyparallel

        try:
            self._port = parallel.Parallel(port_address)
        except TypeError:
            # Older pyparallel builds only accept the keyword form.
            self._port = parallel.Parallel(port=port_address)

    def send(self, value: int, timestamp: float | None = None) -> None:
        self._port.setData(int(value) & 0xFF)

    def close(self) -> None:
        pass


class _NullBackend:
    """Drops markers. Logs the first one, then stays quiet.

    Silence matters: a run emits a marker per trial edge, and a warning per
    marker would bury every other line in the session log.
    """

    def __init__(self, reason: str = "") -> None:
        self._reason = reason
        self._warned = False

    def send(self, value: int, timestamp: float | None = None) -> None:
        if not self._warned:
            logger.warning(
                "Markers are being dropped (%s). First dropped value: %s.",
                self._reason or "backend=null",
                value,
            )
            self._warned = True

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

_backend: _Backend | None = None
_codes: TriggerCodes = TriggerCodes()
_keys: tuple[str, ...] = ()


def configure(
    *,
    backend: str = "null",
    port: str | None = None,
    stream: StreamConfig | None = None,
    codes: TriggerCodes | None = None,
    keys: Sequence[str] | None = None,
) -> _Backend:
    """Open the marker backend. Never raises.

    Any failure (missing library, bad port, no permission) is logged and
    downgraded to :class:`_NullBackend`, so a hardware problem in the console
    room degrades the recording instead of aborting the session.
    """
    global _backend, _codes, _keys
    if codes is not None:
        _codes = codes
    if keys is not None:
        _keys = tuple(keys)

    name = (backend or "null").lower()
    try:
        if name == "lsl":
            _backend = _LSLBackend(stream or StreamConfig())
        elif name == "serial":
            if not port:
                raise ValueError("`port` is required for the serial backend.")
            _backend = _SerialBackend(port)
        elif name == "parallel":
            if not port:
                raise ValueError("`port` is required for the parallel backend.")
            _backend = _ParallelBackend(port)
        elif name == "null":
            _backend = _NullBackend(reason="backend=null (explicit)")
        else:
            raise ValueError(f"unknown marker backend: {backend!r}")
    except Exception as exc:  # noqa: BLE001 - a dead port must not kill the run
        logger.warning("Marker backend %r failed to open; dropping markers: %s", backend, exc)
        _backend = _NullBackend(reason=f"{backend} init failed: {exc}")
    else:
        if name != "null":
            logger.info("Marker backend ready: %s%s", name, f" on {port}" if port else "")
    return _backend


def get_backend() -> _Backend | None:
    """The configured backend, or ``None`` if :func:`configure` hasn't run.

    Used by :mod:`controller_validation_task.sync` so one serial port can
    carry both the scanner start signal and the event markers.
    """
    return _backend


def send_signal(value: int, timestamp: float | None = None) -> None:
    """Emit one marker. Safe to call before :func:`configure` (lazy null init)."""
    global _backend
    if _backend is None:
        configure(backend="null")
    assert _backend is not None
    try:
        _backend.send(value, timestamp=timestamp)
    except Exception as exc:  # noqa: BLE001 - a mid-run port fault must not abort
        logger.warning("Marker send failed (%s); switching to null backend.", exc)
        _backend = _NullBackend(reason=f"send failed: {exc}")


def now() -> float:
    """LSL-clock timestamp for the current instant.

    Call it immediately after the event you are marking, then hand the value
    to :func:`send_signal`. Falls back to a monotonic clock when pylsl is
    unavailable, so behaviour-only runs still get sensible timestamps.
    """
    try:
        import pylsl

        return pylsl.local_clock()
    except Exception:  # noqa: BLE001 - pylsl is optional at runtime
        import time

        return time.monotonic()


def close() -> None:
    """Release the backend. Idempotent."""
    global _backend
    if _backend is not None:
        try:
            _backend.close()
        except Exception:  # noqa: BLE001
            pass
    _backend = None


# ---------------------------------------------------------------------------
# Code helpers
# ---------------------------------------------------------------------------


def set_codes(codes: TriggerCodes) -> None:
    """Replace the active code set (also settable via :func:`configure`)."""
    global _codes
    _codes = codes


def get_codes() -> TriggerCodes:
    return _codes


def set_keys(keys: Sequence[str]) -> None:
    """Set the key order that :func:`encode_key` indexes into."""
    global _keys
    _keys = tuple(keys)


def encode_trial_onset(condition: str) -> int:
    """Marker for a trial's highlight-on flip, chosen by condition.

    Unknown conditions fall back to the ``short`` code so an operator who adds
    a third condition still gets a marker rather than a crash mid-run.
    """
    return _codes.trial_onset_long if condition == "long" else _codes.trial_onset_short


def encode_key(key: str, keys: Sequence[str] | None = None) -> int:
    """Marker for a specific button: ``key_base + index(key)``.

    Raises:
        ValueError: ``key`` is not in the configured key order.
    """
    order = tuple(keys) if keys is not None else _keys
    try:
        return _codes.key_base + order.index(key)
    except ValueError:
        raise ValueError(f"key {key!r} is not in the configured key order {order}.") from None


def decode_marker(value: int, keys: Sequence[str] | None = None) -> str:
    """Human-readable label for a marker value. Used by the live monitor."""
    order = tuple(keys) if keys is not None else _keys
    for name in ("task_start", "task_stop", "trial_onset_short", "trial_onset_long", "trial_offset"):
        if value == getattr(_codes, name):
            return name
    span = range(_codes.key_base, _codes.key_base + max(len(order), 1))
    if value in span and order:
        return f"key:{order[value - _codes.key_base]}"
    return f"unknown({value})"


def __getattr__(name: str) -> Any:
    """Serve TASK_START / TASK_STOP / ... from the *current* code set."""
    mapping = {
        "TASK_START": "task_start",
        "TASK_STOP": "task_stop",
        "TRIAL_ONSET_SHORT": "trial_onset_short",
        "TRIAL_ONSET_LONG": "trial_onset_long",
        "TRIAL_OFFSET": "trial_offset",
        "KEY_BASE": "key_base",
    }
    if name in mapping:
        return getattr(_codes, mapping[name])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _reset_for_tests() -> None:
    """Restore pristine module state. Used by an autouse test fixture."""
    global _backend, _codes, _keys
    _backend = None
    _codes = TriggerCodes()
    _keys = ()
