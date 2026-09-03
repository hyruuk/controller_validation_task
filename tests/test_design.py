"""Covers the trial-sequence generator.

Pure numpy/pandas — no psychopy. These tests pin down the structural
guarantees the task relies on (block/key counts, monotonic non-overlapping
onsets, determinism) rather than specific random draws.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from controller_validation_task import design
from controller_validation_task.settings import DesignSettings

HALVES = {"l": ("l", "r", "u", "d"), "r": ("a", "b", "x", "y")}


@pytest.fixture
def params():
    return DesignSettings()


def gen(params, subject="01", session="001", run=1, halves=None):
    return design.generate_design(
        subject=subject, session=session, run=run, params=params, halves=halves
    )


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def test_seed_is_stable_and_varies_by_field():
    assert design.seed_for("01", "001", 1) == design.seed_for("01", "001", 1)
    assert design.seed_for("01", "001", 1) != design.seed_for("01", "001", 2)
    assert design.seed_for("01", "001", 1) != design.seed_for("02", "001", 1)
    assert design.seed_for("01", "001", 1) != design.seed_for("01", "002", 1)


def test_explicit_seed_overrides_the_hash():
    assert design.seed_for("01", "001", 1, override=42) == 42
    assert design.seed_for("99", "999", 9, override=42) == 42


def test_same_inputs_give_an_identical_design(params):
    a = gen(params)
    b = gen(params)
    pd.testing.assert_frame_equal(a, b)


def test_different_runs_give_different_designs(params):
    a = gen(params, run=1)
    b = gen(params, run=2)
    assert not a.equals(b)


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def test_default_run_is_80_trials(params):
    # 8 keys x (5 short + 5 long) blocks — matches the original task.
    df = gen(params)
    assert len(df) == 80
    assert list(df.columns) == ["block", "condition", "key", "duration", "onset"]


def test_every_block_contains_every_key_once(params):
    df = gen(params)
    for _, block in df.groupby("block"):
        assert sorted(block["key"]) == sorted(params.keys)


def test_each_block_has_a_single_condition(params):
    df = gen(params)
    assert (df.groupby("block")["condition"].nunique() == 1).all()


def test_conditions_are_balanced_across_blocks(params):
    df = gen(params)
    per_block = df.groupby("block")["condition"].first()
    assert (per_block == "short").sum() == params.n_blocks_per_condition
    assert (per_block == "long").sum() == params.n_blocks_per_condition


def test_short_trials_use_the_fixed_duration(params):
    df = gen(params)
    assert (df.loc[df.condition == "short", "duration"] == params.short_press_duration).all()


def test_long_trials_are_drawn_from_the_range(params):
    df = gen(params)
    lo, hi = params.long_duration_range
    longs = df.loc[df.condition == "long", "duration"]
    assert longs.between(lo, hi).all()
    # Drawn per trial, not a single constant.
    assert longs.nunique() > 1


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------


def test_onsets_are_strictly_increasing(params):
    assert gen(params)["onset"].is_monotonic_increasing


def test_first_onset_is_the_initial_wait(params):
    assert gen(params)["onset"].iloc[0] == pytest.approx(params.initial_wait)


def test_trials_never_overlap(params):
    df = gen(params)
    ends = df["onset"].to_numpy()[:-1] + df["duration"].to_numpy()[:-1]
    gaps = df["onset"].to_numpy()[1:] - ends
    assert (gaps > 0).all()


def test_inter_trial_gaps_respect_the_isi_range(params):
    df = gen(params)
    ends = df["onset"].to_numpy()[:-1] + df["duration"].to_numpy()[:-1]
    gaps = df["onset"].to_numpy()[1:] - ends
    isi_range, block_isi = design.resolve_isi(params.isi_range, params.block_isi, params.tr)
    changed = np.diff(df["block"].to_numpy()) != 0
    # Within a block: just the ISI. At a boundary: ISI + the extra block gap.
    assert gaps[~changed].min() >= isi_range[0] - 1e-9
    assert gaps[~changed].max() <= isi_range[1] + 1e-9
    assert gaps[changed].min() >= isi_range[0] + block_isi - 1e-9


def test_resolve_isi_derives_from_tr():
    assert design.resolve_isi(None, None, 1.49) == ((1.49, 2.98), 2.98)


def test_resolve_isi_passes_explicit_values_through():
    assert design.resolve_isi((0.5, 1.5), 3.0, 1.49) == ((0.5, 1.5), 3.0)


def test_run_duration_adds_the_final_wait(params):
    df = gen(params)
    expected = df["onset"].iloc[-1] + df["duration"].iloc[-1] + params.final_wait
    assert design.run_duration(df, params.final_wait) == pytest.approx(expected)


def test_default_run_is_about_five_minutes(params):
    # Sanity check against the original task's ~5.1 min runs.
    minutes = design.run_duration(gen(params), params.final_wait) / 60.0
    assert 4.0 < minutes < 7.0


# ---------------------------------------------------------------------------
# Duration estimate
# ---------------------------------------------------------------------------


def _mean_generated_seconds(params, n=40, halves=None):
    return np.mean(
        [
            design.run_duration(gen(params, subject=f"{i:02d}", halves=halves), params.final_wait)
            for i in range(n)
        ]
    )


def test_estimate_matches_the_mean_of_generated_runs(params):
    est = design.estimate_duration(params)
    assert est.n_blocks == 10
    assert est.n_trials == 80
    # Jitter averages out over many seeds; the estimate is the expectation.
    assert est.run_seconds == pytest.approx(_mean_generated_seconds(params), rel=0.03)


def test_estimate_tracks_the_block_count():
    small = DesignSettings(n_blocks_per_condition=2)
    est = design.estimate_duration(small)
    assert est.n_blocks == 4
    assert est.n_trials == 32
    assert est.run_seconds == pytest.approx(_mean_generated_seconds(small), rel=0.03)


def test_estimate_in_lr_mode_counts_one_hand_per_block():
    lr = DesignSettings(lr_mode=True)
    est = design.estimate_duration(lr, halves=HALVES)
    assert est.n_trials == 40
    assert est.run_seconds == pytest.approx(
        _mean_generated_seconds(lr, halves=HALVES), rel=0.03
    )
    # Without a layout the pad is assumed to split evenly.
    assert design.estimate_duration(lr).n_trials == 40


def test_estimate_uses_explicit_isis_over_the_tr():
    fast = DesignSettings(isi_range=(0.2, 0.4), block_isi=0.5)
    slow = DesignSettings(isi_range=(3.0, 4.0), block_isi=6.0)
    assert design.estimate_duration(fast).run_seconds < design.estimate_duration(slow).run_seconds
    assert design.estimate_duration(fast).run_seconds == pytest.approx(
        _mean_generated_seconds(fast), rel=0.03
    )


def test_session_estimate_adds_runs_and_instruction_screens(params):
    est = design.estimate_duration(params, instruction_duration=3.0)
    # Run 1 shows three screens, run 2 one: 4 x 3 s on top of two runs.
    assert est.n_runs == 2
    assert est.session_seconds == pytest.approx(2 * est.run_seconds + 4 * 3.0)
    one_run = design.estimate_duration(DesignSettings(n_runs=1), instruction_duration=3.0)
    assert one_run.session_seconds == pytest.approx(one_run.run_seconds + 3 * 3.0)


def test_format_seconds_and_describe():
    assert design.format_seconds(0) == "0 s"
    assert design.format_seconds(59.6) == "1 min 0 s"
    assert design.format_seconds(3725) == "62 min 5 s"
    text = design.describe_estimate(design.DurationEstimate(10, 80, 307.0, 2, 627.0))
    assert "80 trials" in text and "10 blocks" in text
    assert "~307 s" in text and "~627 s" in text


# ---------------------------------------------------------------------------
# Left/right mode
# ---------------------------------------------------------------------------


def test_lr_mode_adds_the_column_and_restricts_keys(params):
    p = type(params)(**{**params.__dict__, "lr_mode": True})
    df = design.generate_design(
        subject="01", session="001", run=1, params=p, halves=HALVES
    )
    assert "lr_condition" in df.columns
    assert list(df.columns) == ["block", "condition", "key", "lr_condition", "duration", "onset"]
    for _, row in df.iterrows():
        assert row["key"] in HALVES[row["lr_condition"]]


def test_lr_mode_blocks_have_four_trials(params):
    p = type(params)(**{**params.__dict__, "lr_mode": True})
    df = design.generate_design(subject="01", session="001", run=1, params=p, halves=HALVES)
    assert (df.groupby("block").size() == 4).all()
    assert len(df) == 40


def test_lr_mode_uses_both_hands(params):
    p = type(params)(**{**params.__dict__, "lr_mode": True})
    df = design.generate_design(subject="01", session="001", run=1, params=p, halves=HALVES)
    assert set(df["lr_condition"]) == {"l", "r"}


def test_lr_mode_without_halves_raises(params):
    p = type(params)(**{**params.__dict__, "lr_mode": True})
    with pytest.raises(ValueError, match="halves"):
        design.generate_design(subject="01", session="001", run=1, params=p, halves=None)


# ---------------------------------------------------------------------------
# ensure_design
# ---------------------------------------------------------------------------


def test_ensure_design_writes_then_reuses(tmp_path, params):
    path = tmp_path / "sub-01_design.tsv"
    first = design.ensure_design(
        path, subject="01", session="001", run=1, params=params
    )
    assert path.is_file()

    # Second call must re-read, not regenerate: a mid-study config change must
    # not silently give a subject a different sequence.
    path.write_text("block\tcondition\tkey\tduration\tonset\n0\tshort\ta\t0.3\t3.0\n")
    second = design.ensure_design(path, subject="01", session="001", run=1, params=params)
    assert len(second) == 1
    assert len(first) == 80


def test_ensure_design_overwrite_regenerates(tmp_path, params):
    path = tmp_path / "d.tsv"
    design.ensure_design(path, subject="01", session="001", run=1, params=params)
    path.write_text("block\tcondition\tkey\tduration\tonset\n0\tshort\ta\t0.3\t3.0\n")
    again = design.ensure_design(
        path, subject="01", session="001", run=1, params=params, overwrite=True
    )
    assert len(again) == 80


def test_ensure_design_leaves_no_tmp_file(tmp_path, params):
    path = tmp_path / "d.tsv"
    design.ensure_design(path, subject="01", session="001", run=1, params=params)
    assert list(tmp_path.glob("*.tmp")) == []


# ---------------------------------------------------------------------------
# Configurability
# ---------------------------------------------------------------------------


def test_custom_keys_and_blocks_change_the_shape(params):
    p = type(params)(
        **{**params.__dict__, "keys": ("a", "b"), "n_blocks_per_condition": 2}
    )
    df = gen(p)
    assert len(df) == 2 * 2 * 2  # 2 keys x 2 conditions x 2 blocks each
    assert set(df["key"]) == {"a", "b"}


def test_three_conditions_are_supported(params):
    p = type(params)(
        **{
            **params.__dict__,
            "conditions": ("short", "long", "short"[:0] + "medium"),
            "n_blocks_per_condition": 1,
        }
    )
    df = gen(p)
    assert set(df["condition"]) == {"short", "long", "medium"}
    # Only "long" draws from the range; everything else gets the fixed duration.
    assert (df.loc[df.condition == "medium", "duration"] == p.short_press_duration).all()
