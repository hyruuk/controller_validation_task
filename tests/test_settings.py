"""Covers the config schema, the layered precedence chain, and validation.

The validation tests double as documentation of what an operator can and
cannot configure, so each one names the constraint it is pinning down.
"""

from __future__ import annotations

import json

import pytest

from controller_validation_task import settings as S
from controller_validation_task.markers import TriggerCodes

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_defaults_reproduce_the_original_task():
    d = S.default_settings().design
    assert d.keys == ("r", "l", "u", "d", "a", "b", "x", "y")
    assert d.conditions == ("short", "long")
    assert d.n_runs == 2
    assert d.n_blocks_per_condition == 5
    assert d.short_press_duration == 0.3
    assert d.long_duration_range == (1.0, 3.0)
    assert d.tr == 1.49
    assert d.initial_wait == 3.0
    assert d.final_wait == 9.0
    assert not d.lr_mode


def test_defaults_are_behaviour_only():
    # A fresh checkout must run with no hardware attached.
    s = S.default_settings()
    assert s.sync.mode == "none"
    assert s.triggers.backend == "null"


def test_defaults_validate():
    S._validate(S.default_settings())


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_round_trip_through_json(tmp_path):
    original = S.default_settings()
    path = tmp_path / "config.json"
    S.save(original, path)
    assert S.load_from_file(path) == original


def test_saved_json_is_readable_and_sorted(tmp_path):
    path = tmp_path / "config.json"
    S.save(S.default_settings(), path)
    data = json.loads(path.read_text())
    assert data["schema_version"] == S.SCHEMA_VERSION
    assert set(data) == {
        "schema_version",
        "display",
        "paths",
        "design",
        "input",
        "sync",
        "triggers",
    }
    # Tuples must serialise as JSON lists.
    assert isinstance(data["design"]["keys"], list)


def test_save_is_atomic(tmp_path):
    path = tmp_path / "config.json"
    S.save(S.default_settings(), path)
    assert list(tmp_path.glob("*.tmp")) == []


def test_partial_config_falls_back_to_defaults():
    s = S.from_dict({"design": {"n_runs": 5}})
    assert s.design.n_runs == 5
    assert s.design.keys == S.DesignSettings().keys       # untouched
    assert s.display.fullscreen is True                    # whole section absent


def test_unknown_keys_are_ignored(caplog):
    # Forward compatibility: a config written by a newer version still loads.
    s = S.from_dict({"design": {"n_runs": 3, "future_option": True}, "_comment": "hi"})
    assert s.design.n_runs == 3


def test_schema_version_mismatch_is_a_hard_error():
    with pytest.raises(ValueError, match="schema_version"):
        S.from_dict({"schema_version": 999})


def test_lists_are_coerced_to_tuples():
    s = S.from_dict({"design": {"keys": ["a", "b"], "isi_range": [1.0, 2.0]}})
    assert s.design.keys == ("a", "b")
    assert s.design.isi_range == (1.0, 2.0)


def test_trigger_codes_are_nested_correctly():
    s = S.from_dict({"triggers": {"codes": {"task_start": 9}}})
    assert s.triggers.codes.task_start == 9
    assert s.triggers.codes.task_stop == 2  # default preserved


# ---------------------------------------------------------------------------
# Precedence: defaults < config.json < env < CLI
# ---------------------------------------------------------------------------


def test_env_overrides_config_file(tmp_path):
    path = tmp_path / "config.json"
    S.save(S.default_settings(), path)
    s = S.load(config_path=path, env={"CVT_N_RUNS": "4"}, cli_overrides={})
    assert s.design.n_runs == 4


def test_cli_overrides_env(tmp_path):
    path = tmp_path / "config.json"
    S.save(S.default_settings(), path)
    s = S.load(config_path=path, env={"CVT_N_RUNS": "4"}, cli_overrides={"n_runs": 9})
    assert s.design.n_runs == 9


def test_none_cli_values_are_ignored():
    # argparse gives None for "flag not supplied"; it must not clobber config.
    s = S.load(config_path=None, env={}, cli_overrides={"n_runs": None})
    assert s.design.n_runs == 2


def test_missing_config_file_is_not_an_error(tmp_path):
    s = S.load(config_path=tmp_path / "nope.json", env={})
    assert s == S.default_settings()


def test_env_bool_parsing():
    assert S.load(env={"CVT_LR_MODE": "1"}).design.lr_mode is True
    assert S.load(env={"CVT_LR_MODE": "true"}).design.lr_mode is True
    assert S.load(env={"CVT_LR_MODE": "0"}).design.lr_mode is False
    assert S.load(env={"CVT_LR_MODE": "off"}).design.lr_mode is False


def test_env_window_size_needs_both_dimensions():
    assert S.load(env={"EXP_WIN_W": "1280"}).display.window_size is None
    s = S.load(env={"EXP_WIN_W": "1280", "EXP_WIN_H": "720"})
    assert s.display.window_size == (1280, 720)


def test_unparseable_env_value_names_the_variable():
    with pytest.raises(ValueError, match="CVT_N_RUNS"):
        S.load(env={"CVT_N_RUNS": "many"})


def test_sync_and_trigger_env_keys():
    s = S.load(
        env={
            "CVT_SYNC_MODE": "send",
            "CVT_SYNC_BACKEND": "serial",
            "CVT_SYNC_PORT": "/dev/ttyUSB0",
            "CVT_SYNC_VALUE": "s",
            "CVT_TRIGGER_BACKEND": "lsl",
        }
    )
    assert s.sync.mode == "send"
    assert s.sync.port == "/dev/ttyUSB0"
    assert s.triggers.backend == "lsl"


# ---------------------------------------------------------------------------
# Validation — design
# ---------------------------------------------------------------------------


def _with(section, **kw):
    base = S.default_settings()
    import dataclasses

    return dataclasses.replace(base, **{section: dataclasses.replace(getattr(base, section), **kw)})


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"keys": ()}, "empty"),
        ({"keys": ("a", "a")}, "duplicates"),
        ({"conditions": ()}, "empty"),
        ({"n_runs": 0}, "n_runs"),
        ({"n_blocks_per_condition": 0}, "n_blocks_per_condition"),
        ({"short_press_duration": 0}, "short_press_duration"),
        ({"long_duration_range": (3.0, 1.0)}, "long_duration_range"),
        ({"long_duration_range": (0.0, 1.0)}, "long_duration_range"),
        ({"tr": 0}, "tr"),
        ({"isi_range": (2.0, 1.0)}, "isi_range"),
        ({"block_isi": -1}, "block_isi"),
        ({"initial_wait": -1}, "initial_wait"),
        ({"response_window": 0}, "response_window"),
    ],
)
def test_invalid_design_is_rejected(kwargs, match):
    with pytest.raises(ValueError, match=match):
        S._validate(_with("design", **kwargs))


def test_isi_range_null_is_allowed():
    S._validate(_with("design", isi_range=None, block_isi=None))


# ---------------------------------------------------------------------------
# Validation — display
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"frame_rate": 0}, "frame_rate"),
        ({"instruction_duration": -1}, "instruction_duration"),
        ({"window_size": (0, 100)}, "window_size"),
        ({"screen_index": -1}, "screen_index"),
    ],
)
def test_invalid_display_is_rejected(kwargs, match):
    with pytest.raises(ValueError, match=match):
        S._validate(_with("display", **kwargs))


# ---------------------------------------------------------------------------
# Validation — sync
# ---------------------------------------------------------------------------


def test_unknown_sync_mode_is_rejected():
    with pytest.raises(ValueError, match="sync.mode"):
        S._validate(_with("sync", mode="telepathy"))


def test_send_over_serial_without_a_port_is_allowed():
    # Not a config error: sync.configure() falls back to waiting for the sync
    # key on the keyboard, so one config works at the scanner and on a desk.
    S._validate(_with("sync", mode="send", backend="serial", port=None))


def test_wait_over_serial_without_a_port_is_allowed():
    S._validate(_with("sync", mode="wait", backend="serial", port=None))


def test_send_over_markers_needs_no_port():
    S._validate(_with("sync", mode="send", backend="markers", port=None))


def test_send_needs_a_signal():
    with pytest.raises(ValueError, match="sync.signal"):
        S._validate(_with("sync", mode="send", backend="markers", signal=()))


def test_wait_rejects_a_send_only_backend():
    with pytest.raises(ValueError, match="sync.backend"):
        S._validate(_with("sync", mode="wait", backend="lsl"))


def test_wait_needs_a_signal():
    with pytest.raises(ValueError, match="sync.signal"):
        S._validate(_with("sync", mode="wait", backend="keyboard", signal=()))


def test_signal_accepts_a_bare_string_from_json():
    # One key is the common case; quoting it as a plain string is what anyone
    # hand-editing config.json will do.
    s = S.from_dict({**S.to_dict(S.default_settings()), "sync": {"signal": "t"}})
    assert s.sync.signal == ("t",)


def test_negative_dummy_scans_rejected():
    with pytest.raises(ValueError, match="n_dummy_scans"):
        S._validate(_with("sync", n_dummy_scans=-1))


def test_zero_timeout_rejected():
    with pytest.raises(ValueError, match="timeout_seconds"):
        S._validate(_with("sync", mode="none", timeout_seconds=0))


# ---------------------------------------------------------------------------
# Validation — triggers
# ---------------------------------------------------------------------------


def test_unknown_trigger_backend_is_rejected():
    with pytest.raises(ValueError, match="triggers.backend"):
        S._validate(_with("triggers", backend="smoke-signal"))


def test_serial_triggers_need_a_port():
    with pytest.raises(ValueError, match="triggers.port"):
        S._validate(_with("triggers", backend="serial", port=None))


def test_marker_codes_must_fit_in_a_byte():
    with pytest.raises(ValueError, match="0..255"):
        S._validate(_with("triggers", codes=TriggerCodes(task_start=300)))


def test_duplicate_lifecycle_codes_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        S._validate(_with("triggers", codes=TriggerCodes(task_start=2, task_stop=2)))


def test_key_codes_must_not_overflow_the_byte():
    with pytest.raises(ValueError, match="past the 255"):
        # key_base 250 + 8 keys reaches 257.
        S._validate(_with("triggers", codes=TriggerCodes(key_base=250)))


def test_key_codes_must_not_collide_with_lifecycle_codes():
    with pytest.raises(ValueError, match="overlap"):
        # key_base 8 spans 8..15, swallowing trial_onset_short=10.
        S._validate(_with("triggers", codes=TriggerCodes(key_base=8)))


def test_fewer_keys_allows_a_higher_key_base():
    import dataclasses

    base = S.default_settings()
    s = dataclasses.replace(
        base,
        design=dataclasses.replace(base.design, keys=("a", "b")),
        triggers=dataclasses.replace(base.triggers, codes=TriggerCodes(key_base=254)),
    )
    S._validate(s)
