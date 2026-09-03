"""Covers the events TSV schema and writer.

The column order here is a compatibility contract with the original
``task_stimuli`` output, so these tests assert the exact header rather than
just its contents.
"""

from __future__ import annotations

import pandas as pd

from controller_validation_task import events

#: The header the upstream task produced, verbatim.
UPSTREAM_HEADER = [
    "TrialNumber",
    "block",
    "condition",
    "key",
    "duration",
    "onset",
    "onset_flip",
    "offset_flip",
    "key_press_time",
    "key_press_rt",
    "key_release_time",
    "key_release_rt",
    "key_duration",
    "all_keypresses",
    "all_keyreleases",
]


def test_column_order_matches_upstream():
    assert events.event_columns() == UPSTREAM_HEADER


def test_lr_condition_is_the_fifth_column():
    cols = events.event_columns(lr_mode=True)
    assert cols[:5] == ["TrialNumber", "block", "condition", "key", "lr_condition"]
    assert cols[5:] == UPSTREAM_HEADER[4:]


def _row(**kw):
    base = {
        "block": 0,
        "condition": "short",
        "key": "a",
        "duration": 0.3,
        "onset": 3.0,
        "onset_flip": 3.001,
        "offset_flip": 3.301,
        "all_keypresses": [("a", 3.2)],
        "all_keyreleases": [("a", 3.3)],
    }
    base.update(kw)
    return base


def test_written_header_matches_exactly(tmp_path):
    path = tmp_path / "events.tsv"
    events.write_events_tsv([_row()], path)
    assert path.read_text().splitlines()[0].split("\t") == UPSTREAM_HEADER


def test_trial_number_is_one_indexed(tmp_path):
    path = tmp_path / "events.tsv"
    events.write_events_tsv([_row(), _row(), _row()], path)
    df = pd.read_csv(path, sep="\t")
    assert df["TrialNumber"].tolist() == [1, 2, 3]


def test_explicit_trial_number_is_preserved(tmp_path):
    path = tmp_path / "events.tsv"
    events.write_events_tsv([_row(TrialNumber=7)], path)
    assert pd.read_csv(path, sep="\t")["TrialNumber"].tolist() == [7]


def test_missing_responses_are_empty_not_nan(tmp_path):
    # A trial where the participant never pressed: the response columns must
    # be blank, so "no response" stays distinguishable from a genuine zero.
    path = tmp_path / "events.tsv"
    events.write_events_tsv([_row()], path)
    line = path.read_text().splitlines()[1].split("\t")
    header = path.read_text().splitlines()[0].split("\t")
    cells = dict(zip(header, line, strict=True))
    assert cells["key_press_time"] == ""
    assert cells["key_press_rt"] == ""
    assert cells["key_duration"] == ""


def test_unknown_keys_are_dropped_not_appended(tmp_path):
    # A stray key must not shift the header — the schema is fixed.
    path = tmp_path / "events.tsv"
    events.write_events_tsv([_row(unexpected="x")], path)
    assert path.read_text().splitlines()[0].split("\t") == UPSTREAM_HEADER


def test_round_trips_through_pandas(tmp_path):
    path = tmp_path / "events.tsv"
    rows = [_row(key_press_time=3.2, key_press_rt=0.199), _row(key="b", block=1)]
    n = events.write_events_tsv(rows, path)
    assert n == 2
    df = pd.read_csv(path, sep="\t")
    assert len(df) == 2
    assert df["key"].tolist() == ["a", "b"]
    assert df["key_press_rt"].iloc[0] == 0.199


def test_all_keypresses_round_trip_via_literal_eval(tmp_path):
    import ast

    path = tmp_path / "events.tsv"
    events.write_events_tsv([_row(all_keypresses=[("a", 3.25), ("b", 3.4)])], path)
    df = pd.read_csv(path, sep="\t")
    parsed = ast.literal_eval(df["all_keypresses"].iloc[0])
    assert parsed == [("a", 3.25), ("b", 3.4)]


def test_zero_rows_still_writes_a_header(tmp_path):
    # An aborted run should leave evidence, not an absent file.
    path = tmp_path / "events.tsv"
    assert events.write_events_tsv([], path) == 0
    assert path.read_text().splitlines()[0].split("\t") == UPSTREAM_HEADER
