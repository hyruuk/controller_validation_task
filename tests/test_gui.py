"""Covers the display-free helpers behind the wizard and subject picker.

The dialogs themselves need PsychoPy and a display, so only the pure mapping
and suggestion helpers are tested here — the same split the rest of the suite
uses.
"""

from __future__ import annotations

import pytest

from controller_validation_task import gui
from controller_validation_task import settings as S


def test_subject_choices_always_offers_a_new_subject(tmp_path):
    assert gui.subject_choices(tmp_path) == [gui.NEW_SUBJECT]

    src = tmp_path / "sourcedata"
    (src / "sub-01").mkdir(parents=True)
    (src / "sub-02").mkdir()
    assert gui.subject_choices(tmp_path) == ["01", "02", gui.NEW_SUBJECT]


def test_suggest_session_for_a_new_subject(tmp_path):
    assert gui.suggest_session(tmp_path, "01") == "001"
    assert gui.suggest_session(tmp_path, "") == "001"


def test_suggest_session_counts_up(tmp_path):
    (tmp_path / "sourcedata" / "sub-01" / "ses-001").mkdir(parents=True)
    assert gui.suggest_session(tmp_path, "01") == "002"


# ---------------------------------------------------------------------------
# Reading dialog results
#
# Regression tests for the bug where results were zipped positionally against
# our key list. PsychoPy's Dlg.show() returns an IndexDict keyed by each
# field's *label*, so zipping paired every key with a label string and the
# wizard died with "could not convert string to float: 'frame_rate (Hz)'".
# ---------------------------------------------------------------------------


def _labelled_result(fields, **overrides):
    """Build what PsychoPy 2026 returns: a dict keyed by field label."""
    result = {f.label: f.initial for f in fields}
    by_key = {f.key: f.label for f in fields}
    for key, value in overrides.items():
        result[by_key[key]] = value
    return result


def test_reads_a_dict_keyed_by_label():
    fields = gui.wizard_fields(S.default_settings())
    answers = gui.read_dialog_values(fields, _labelled_result(fields))
    assert set(answers) == {f.key for f in fields}
    # The give-away symptom: a value equal to its own label.
    assert answers["frame_rate"] != "frame_rate (Hz)"
    assert answers["frame_rate"] == S.default_settings().display.frame_rate


def test_reads_a_legacy_positional_list():
    fields = gui.wizard_fields(S.default_settings())
    answers = gui.read_dialog_values(fields, [f.initial for f in fields])
    assert answers["frame_rate"] == S.default_settings().display.frame_rate


def test_cancelled_dialog_yields_no_answers():
    fields = gui.wizard_fields(S.default_settings())
    assert gui.read_dialog_values(fields, None) == {}


def test_labels_and_keys_are_distinct_but_complete():
    # Labels carry units, which is exactly why reading by position broke.
    fields = gui.wizard_fields(S.default_settings())
    assert len({f.key for f in fields}) == len(fields)
    assert len({f.label for f in fields}) == len(fields)
    assert any(f.key != f.label for f in fields)


def test_labels_are_names_not_explanations():
    """A label names the setting — matching ``config.json`` — and adds units.

    The tab blurb above the fields is where any longer explanation goes.
    """
    for f in gui.wizard_fields(S.default_settings()):
        assert len(f.label) <= 24, f"{f.key} label reads like a sentence: {f.label!r}"


def test_changing_the_tr_round_trips_through_the_wizard():
    """The exact reported failure: switch the TR, save, reload."""
    base = S.default_settings()
    fields = gui.wizard_fields(base)
    returned = _labelled_result(fields, tr=1.60)
    answers = gui.read_dialog_values(fields, returned)
    result = gui.settings_from_wizard(base, answers)
    S._validate(result)
    assert result.design.tr == 1.60
    assert result.display.frame_rate == base.display.frame_rate


def test_wizard_fields_prefill_from_the_given_settings():
    import dataclasses

    base = S.default_settings()
    base = dataclasses.replace(base, design=dataclasses.replace(base.design, tr=2.5))
    fields = {f.key: f.initial for f in gui.wizard_fields(base)}
    assert fields["tr"] == 2.5


def test_subject_picker_fields_read_by_label():
    fields = [
        gui.Field("picked", "existing subject", "01"),
        gui.Field("typed", "new subject id", ""),
        gui.Field("session", "session", ""),
    ]
    answers = gui.read_dialog_values(
        fields,
        {"existing subject": "01", "new subject id": "", "session": "003"},
    )
    assert answers == {"picked": "01", "typed": "", "session": "003"}


def test_wizard_answers_map_into_nested_settings():
    base = S.default_settings()
    result = gui.settings_from_wizard(
        base,
        {
            "fullscreen": False,
            "frame_rate": 120,
            "output_root": "/data/out",
            "n_runs": 4,
            "lr_mode": True,
            "tr": 2.0,
            "sync_mode": "send",
            "sync_backend": "serial",
            "sync_port": "/dev/ttyUSB0",
            "sync_value": "s",
            "trigger_backend": "lsl",
            "trigger_port": "",
        },
    )
    assert result.display.fullscreen is False
    assert result.display.frame_rate == 120.0
    assert result.paths.output_root == "/data/out"
    assert result.design.n_runs == 4
    assert result.design.lr_mode is True
    assert result.design.tr == 2.0
    assert result.sync.mode == "send"
    assert result.sync.port == "/dev/ttyUSB0"
    assert result.triggers.backend == "lsl"
    # A blank port must become None, not the empty string.
    assert result.triggers.port is None


def test_wizard_result_validates():
    base = S.default_settings()
    result = gui.settings_from_wizard(
        base,
        {
            "fullscreen": True,
            "frame_rate": 60,
            "output_root": "output",
            "n_runs": 2,
            "lr_mode": False,
            "tr": 1.49,
            "sync_mode": "none",
            "sync_backend": "serial",
            "sync_port": "",
            "sync_value": "s",
            "trigger_backend": "null",
            "trigger_port": "",
        },
    )
    S._validate(result)


def test_blank_answers_fall_back_to_the_base():
    base = S.default_settings()
    result = gui.settings_from_wizard(base, {})
    assert result == base


def test_design_fields_prefill_and_round_trip():
    """Every design knob the wizard shows survives edit -> save -> reload."""
    base = S.default_settings()
    fields = gui.wizard_fields(base)
    initial = {f.key: f.initial for f in fields}
    # Optional TR-derived values show as blank; ranges as 'low, high'.
    assert initial["isi_range"] == ""
    assert initial["block_isi"] == ""
    assert initial["long_duration_range"] == "1.0, 3.0"
    assert initial["n_blocks_per_condition"] == 5

    returned = _labelled_result(
        fields,
        n_blocks_per_condition=3,
        isi_range="0.5, 1.5",
        block_isi="4",
        short_press_duration=0.25,
        long_duration_range="[2, 4]",
        initial_wait=1.0,
        final_wait=5.0,
    )
    result = gui.settings_from_wizard(base, gui.read_dialog_values(fields, returned))
    S._validate(result)
    assert result.design.n_blocks_per_condition == 3
    assert result.design.isi_range == (0.5, 1.5)
    assert result.design.block_isi == 4.0
    assert result.design.short_press_duration == 0.25
    assert result.design.long_duration_range == (2.0, 4.0)
    assert result.design.initial_wait == 1.0
    assert result.design.final_wait == 5.0

    # Re-opening the wizard on the saved settings shows what was typed.
    again = {f.key: f.initial for f in gui.wizard_fields(result)}
    assert again["isi_range"] == "0.5, 1.5"
    assert again["block_isi"] == "4.0"
    assert again["long_duration_range"] == "2.0, 4.0"


def test_blanking_isi_fields_restores_the_tr_defaults():
    import dataclasses

    base = S.default_settings()
    base = dataclasses.replace(
        base, design=dataclasses.replace(base.design, isi_range=(0.5, 1.5), block_isi=4.0)
    )
    result = gui.settings_from_wizard(base, {"isi_range": "", "block_isi": " "})
    assert result.design.isi_range is None
    assert result.design.block_isi is None


def test_a_malformed_range_is_a_readable_error():
    with pytest.raises(ValueError, match="long_duration_range"):
        gui.settings_from_wizard(S.default_settings(), {"long_duration_range": "1"})


# ---------------------------------------------------------------------------
# Duration estimate
# ---------------------------------------------------------------------------


def test_estimate_line_reflects_the_answers():
    base = S.default_settings()
    line = gui.wizard_estimate(base, {})
    assert line.startswith("Estimated duration")
    assert "80 trials in 10 blocks" in line
    assert "2 runs" in line

    fewer = gui.wizard_estimate(base, {"n_blocks_per_condition": 2, "n_runs": 1})
    assert "32 trials in 4 blocks" in fewer
    assert "1 run," in fewer


def test_estimate_survives_half_typed_input():
    base = S.default_settings()
    for answers in ({"tr": ""}, {"n_blocks_per_condition": "x"}, {"n_blocks_per_condition": 0}):
        line = gui.wizard_estimate(base, answers)
        assert line.startswith("Estimated duration: n/a")


def test_estimate_uses_the_controller_halves_in_lr_mode():
    base = S.default_settings()
    halves = {"l": ("l", "r", "u", "d"), "r": ("a", "b", "x", "y")}
    line = gui.wizard_estimate(base, {"lr_mode": True}, halves)
    assert "40 trials in 10 blocks" in line


def test_layout_halves_come_from_the_packaged_controller():
    halves = gui._layout_halves(S.default_settings())
    assert halves is not None and set(halves) == {"l", "r"}


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------


def test_every_wizard_field_lands_in_exactly_one_tab():
    fields = gui.wizard_fields(S.default_settings())
    placed = [key for tab in gui.WIZARD_TABS for key in tab.keys]
    assert sorted(placed) == sorted(f.key for f in fields)
    assert len(placed) == len(set(placed))


def test_wizard_sections_group_fields_in_tab_order():
    fields = gui.wizard_fields(S.default_settings())
    sections = gui.wizard_sections(fields)

    assert [tab.title for tab, _ in sections] == [t.title for t in gui.WIZARD_TABS]
    titles = dict(gui.wizard_sections(fields))
    sync_tab = next(t for t in gui.WIZARD_TABS if t.title == "Scanner sync")
    assert [f.key for f in titles[sync_tab]] == list(sync_tab.keys)


def test_wizard_sections_reject_a_tab_naming_an_unknown_field(monkeypatch):
    monkeypatch.setattr(
        gui, "WIZARD_TABS", (gui.Tab("Bogus", "blurb", ("no_such_field",)),)
    )
    with pytest.raises(KeyError, match="no_such_field"):
        gui.wizard_sections(gui.wizard_fields(S.default_settings()))


def test_every_tab_has_a_blurb():
    for tab in gui.WIZARD_TABS:
        assert tab.blurb.strip()
        assert tab.title.strip()


def test_subject_picker_asks_for_subject_and_session(tmp_path):
    fields = gui.subject_fields(tmp_path)
    assert [f.key for f in fields] == ["picked", "typed", "session"]
    # The dropdown offers the sentinel that unlocks the "new subject" box.
    assert gui.NEW_SUBJECT in (fields[0].choices or [])


def test_tabbed_layout_declines_when_there_is_no_qt(monkeypatch):
    """On a wx (or headless) PsychoPy the wizard must not half-build a dialog."""
    monkeypatch.setattr(gui, "_qt_widgets", lambda: None)
    assert gui._add_tabbed_fields(object(), []) is False


def test_inline_fallback_adds_every_field_under_its_heading():
    class FakeDlg:
        def __init__(self):
            self.calls = []

        def addText(self, text):
            self.calls.append(("text", text))

        def addField(self, label, initial="", choices=None):
            self.calls.append(("field", label))

    dlg = FakeDlg()
    fields = gui.wizard_fields(S.default_settings())
    gui._add_inline_sections(dlg, gui.wizard_sections(fields))

    headings = [c[1] for c in dlg.calls if c[0] == "text"]
    labels = [c[1] for c in dlg.calls if c[0] == "field"]
    assert headings == [t.title for t in gui.WIZARD_TABS]
    assert sorted(labels) == sorted(f.label for f in fields)
