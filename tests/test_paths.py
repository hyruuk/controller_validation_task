"""Covers BIDS path construction, label validation and session inference.

Pure filesystem logic — no psychopy, no display. The parts of
:mod:`controller_validation_task.paths` that resolve packaged assets are
covered by ``test_layout.py`` instead, since they only matter through the
layout loader.
"""

from __future__ import annotations

import pytest

from controller_validation_task import paths


def test_normalize_subject_strips_prefix():
    assert paths.normalize_subject("sub-01") == "01"
    assert paths.normalize_subject("01") == "01"
    assert paths.normalize_subject("pilot1") == "pilot1"


def test_normalize_session_pads_and_strips():
    assert paths.normalize_session("ses-001") == "001"
    assert paths.normalize_session("1") == "001"
    assert paths.normalize_session("12") == "012"
    # Non-numeric labels are left alone rather than mangled.
    assert paths.normalize_session("pilot") == "pilot"


@pytest.fixture
def bids(tmp_path):
    return paths.BidsPaths(
        subject="01", session="001", output_root=tmp_path, timestamp="20260101-120000"
    )


def test_session_prefix_and_filenames(bids):
    assert bids.session_prefix == "sub-01_ses-001_20260101-120000"
    assert bids.log_path.name == "sub-01_ses-001_20260101-120000.log"
    assert (
        bids.events_tsv("task-gamepad_run-01").name
        == "sub-01_ses-001_20260101-120000_task-gamepad_run-01_events.tsv"
    )


def test_events_live_in_the_session_dir_design_at_subject_level(bids):
    # The design is deliberately one level up, so re-running a session re-uses
    # the same trial sequence instead of regenerating it.
    assert bids.events_tsv("task-gamepad_run-01").parent == bids.sourcedata_session_dir
    assert bids.design_tsv(1).parent == bids.sourcedata_subject_dir
    assert bids.design_tsv(2).name == "sub-01_ses-001_run-02_design.tsv"


def test_output_root_is_coerced_to_path():
    b = paths.BidsPaths(subject="01", session="001", output_root="output")
    assert b.output_root.name == "output"
    assert hasattr(b.output_root, "joinpath")


@pytest.mark.parametrize("bad", ["../evil", "sub 01", "-leading", "_leading", "a/b", ""])
def test_invalid_labels_are_rejected(bad, tmp_path):
    with pytest.raises(ValueError, match="Invalid"):
        paths.BidsPaths(subject=bad, session="001", output_root=tmp_path)
    with pytest.raises(ValueError, match="Invalid"):
        paths.BidsPaths(subject="01", session=bad, output_root=tmp_path)


@pytest.mark.parametrize("bad", ["../escape", "a/b", "a\\b", ".hidden"])
def test_task_name_cannot_escape_the_session_dir(bids, bad):
    with pytest.raises(ValueError, match="invalid task_name"):
        bids.events_tsv(bad)


def test_infer_next_session_counts_up(tmp_path):
    assert paths.infer_next_session(tmp_path, "01") == "001"

    subj = tmp_path / "sourcedata" / "sub-01"
    (subj / "ses-001").mkdir(parents=True)
    assert paths.infer_next_session(tmp_path, "01") == "002"

    (subj / "ses-004").mkdir()
    assert paths.infer_next_session(tmp_path, "01") == "005"

    # Non-numeric sessions are ignored when picking the next number.
    (subj / "ses-pilot").mkdir()
    assert paths.infer_next_session(tmp_path, "01") == "005"


def test_list_subjects(tmp_path):
    assert paths.list_subjects(tmp_path) == []
    src = tmp_path / "sourcedata"
    (src / "sub-02").mkdir(parents=True)
    (src / "sub-01").mkdir()
    (src / "not-a-subject").mkdir()
    assert paths.list_subjects(tmp_path) == ["01", "02"]
