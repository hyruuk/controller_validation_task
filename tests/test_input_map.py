"""Covers the keyboard -> controller-button mapping.

The pad is read as a keyboard, and which keys it sends depends on the
hardware: a D-pad may arrive as ``left`` or, behind an AntiMicroX profile, as
``l``. These tests pin down that both work, and that a misconfigured map is
rejected loudly rather than silently recording no responses.

:func:`controller_validation_task.input.translate` is pure, so this needs no
window; the psychopy import at the top of ``input.py`` is the only reason this
lives outside the integration set.
"""

from __future__ import annotations

import dataclasses

import pytest
from conftest import import_or_skip

from controller_validation_task import settings as S

I = import_or_skip("controller_validation_task.input", reason="input.py imports psychopy")  # noqa: E741


def test_default_map_accepts_arrow_keys():
    km = S.default_settings().input.key_map
    assert I.translate([("left", 1.0), ("right", 2.0), ("up", 3.0), ("down", 4.0)], km) == [
        ("l", 1.0),
        ("r", 2.0),
        ("u", 3.0),
        ("d", 4.0),
    ]


def test_default_map_accepts_antimicrox_letters():
    km = S.default_settings().input.key_map
    assert I.translate([("l", 1.0), ("u", 2.0)], km) == [("l", 1.0), ("u", 2.0)]


def test_default_map_passes_face_buttons_through():
    km = S.default_settings().input.key_map
    assert I.translate([("a", 1.0), ("b", 2.0), ("x", 3.0), ("y", 4.0)], km) == [
        ("a", 1.0),
        ("b", 2.0),
        ("x", 3.0),
        ("y", 4.0),
    ]


def test_unmapped_keys_pass_through_unchanged():
    # Kept, not dropped: a stray keystroke must still show up in
    # all_keypresses so the trial can be excluded offline.
    assert I.translate([("escape", 1.0)], {"left": "l"}) == [("escape", 1.0)]


def test_custom_map_supports_arbitrary_keys():
    km = {"1": "a", "2": "b"}
    assert I.translate([("1", 1.0), ("2", 2.0)], km) == [("a", 1.0), ("b", 2.0)]


def test_translated_events_match_via_first_match():
    km = S.default_settings().input.key_map
    events = I.translate([("noise", 0.5), ("left", 1.25)], km)
    assert I.first_match(events, "l") == 1.25
    assert I.first_match(events, "r") is None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _with_map(key_map, keys=None):
    base = S.default_settings()
    design = base.design if keys is None else dataclasses.replace(base.design, keys=keys)
    return dataclasses.replace(
        base, design=design, input=dataclasses.replace(base.input, key_map=key_map)
    )


def test_default_map_covers_every_default_key():
    S._validate(S.default_settings())


def test_empty_map_is_rejected():
    with pytest.raises(ValueError, match="key_map is empty"):
        S._validate(_with_map({}))


def test_map_onto_an_uncued_button_is_allowed():
    # Narrowing design.keys for a pilot must not require rewriting the map;
    # the surplus entries simply never match.
    S._validate(_with_map(dict(S.DEFAULT_KEY_MAP), keys=("a", "b")))


def test_uncovered_button_is_rejected():
    # The dangerous case: the run looks fine but those trials can never be
    # answered, because no key produces that button.
    with pytest.raises(ValueError, match="could never be answered"):
        S._validate(_with_map({"a": "a"}, keys=("a", "b")))


def test_a_reduced_but_complete_map_is_accepted():
    S._validate(_with_map({"left": "l", "right": "r"}, keys=("l", "r")))


def test_many_keys_may_map_to_one_button():
    S._validate(_with_map({"left": "l", "l": "l", "kp_4": "l"}, keys=("l",)))
