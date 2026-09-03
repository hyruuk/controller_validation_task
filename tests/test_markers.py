"""Covers the marker code scheme and the never-crash backend contract.

The transports themselves (pylsl / pyserial / pyparallel) are not exercised —
they need hardware. What IS tested is the promise that a broken transport
degrades to dropping markers instead of raising, because that promise is what
lets the task start when a port is unplugged.
"""

from __future__ import annotations

import logging

import pytest

from controller_validation_task import markers
from controller_validation_task.markers import TriggerCodes


@pytest.fixture(autouse=True)
def reset_markers():
    """Module state is global; reset it around every test."""
    markers._reset_for_tests()
    yield
    markers._reset_for_tests()


# ---------------------------------------------------------------------------
# Codes
# ---------------------------------------------------------------------------


def test_dynamic_constants_follow_the_configured_codes():
    assert markers.TASK_START == 1
    markers.set_codes(TriggerCodes(task_start=77))
    assert markers.TASK_START == 77


def test_unknown_module_attribute_still_raises():
    with pytest.raises(AttributeError):
        _ = markers.NOT_A_MARKER


def test_trial_onset_depends_on_condition():
    assert markers.encode_trial_onset("short") == 10
    assert markers.encode_trial_onset("long") == 11
    # An unrecognised condition falls back rather than crashing mid-run.
    assert markers.encode_trial_onset("medium") == 10


def test_encode_key_indexes_from_key_base():
    keys = ("r", "l", "u", "d")
    assert markers.encode_key("r", keys) == 20
    assert markers.encode_key("d", keys) == 23


def test_encode_key_uses_the_configured_order_by_default():
    markers.set_keys(("a", "b"))
    assert markers.encode_key("b") == 21


def test_encode_key_rejects_an_unknown_key():
    with pytest.raises(ValueError, match="not in the configured key order"):
        markers.encode_key("z", ("a", "b"))


@pytest.mark.parametrize(
    "value,label",
    [(1, "task_start"), (2, "task_stop"), (10, "trial_onset_short"),
     (11, "trial_onset_long"), (12, "trial_offset")],
)
def test_decode_lifecycle_markers(value, label):
    assert markers.decode_marker(value) == label


def test_decode_key_markers():
    markers.set_keys(("r", "l", "u"))
    assert markers.decode_marker(20) == "key:r"
    assert markers.decode_marker(22) == "key:u"


def test_decode_unknown_value():
    assert markers.decode_marker(200) == "unknown(200)"


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


def test_null_backend_is_the_default():
    backend = markers.configure(backend="null")
    assert isinstance(backend, markers._NullBackend)


def test_unknown_backend_falls_back_without_raising(caplog):
    with caplog.at_level(logging.WARNING):
        backend = markers.configure(backend="carrier-pigeon")
    assert isinstance(backend, markers._NullBackend)
    assert "failed to open" in caplog.text


def test_serial_without_a_port_falls_back(caplog):
    with caplog.at_level(logging.WARNING):
        backend = markers.configure(backend="serial", port=None)
    assert isinstance(backend, markers._NullBackend)
    assert "port" in caplog.text.lower()


def test_serial_with_a_bad_port_falls_back(caplog):
    with caplog.at_level(logging.WARNING):
        backend = markers.configure(backend="serial", port="/dev/definitely-not-a-port")
    assert isinstance(backend, markers._NullBackend)


def test_null_backend_only_warns_once(caplog):
    markers.configure(backend="null")
    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            markers.send_signal(1)
    # A marker per trial edge would otherwise bury the log.
    assert caplog.text.count("being dropped") == 1


def test_send_signal_lazily_configures():
    assert markers.get_backend() is None
    markers.send_signal(1)
    assert isinstance(markers.get_backend(), markers._NullBackend)


def test_send_failure_downgrades_instead_of_raising(caplog):
    class Exploding:
        def send(self, value, timestamp=None):
            raise OSError("port went away")

        def close(self):
            pass

    markers._backend = Exploding()
    with caplog.at_level(logging.WARNING):
        markers.send_signal(1)  # must not raise
    assert isinstance(markers.get_backend(), markers._NullBackend)


def test_configure_records_codes_and_keys():
    markers.configure(backend="null", codes=TriggerCodes(task_start=5), keys=("x", "y"))
    assert markers.TASK_START == 5
    assert markers.encode_key("y") == 21


def test_now_returns_a_float():
    assert isinstance(markers.now(), float)


def test_close_is_idempotent():
    markers.configure(backend="null")
    markers.close()
    markers.close()
    assert markers.get_backend() is None
