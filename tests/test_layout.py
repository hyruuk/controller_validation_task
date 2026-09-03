"""Covers the controller layout loader, coordinate transform and validation.

Stimulus construction (:func:`layout.build_stimuli`) needs a window and is
verified on hardware, not here.
"""

from __future__ import annotations

import json

import pytest

from controller_validation_task import layout as L

IMAGE_SIZE = (800, 481)


# ---------------------------------------------------------------------------
# Coordinate transform
# ---------------------------------------------------------------------------


def test_centre_maps_to_origin():
    assert L.to_psychopy_pix((400, 240.5), IMAGE_SIZE) == (0.0, 0.0)


@pytest.mark.parametrize(
    "point,expected",
    [
        ((0, 0), (-400.0, 240.5)),        # top-left
        ((800, 481), (400.0, -240.5)),    # bottom-right
        ((800, 0), (400.0, 240.5)),       # top-right
        ((0, 481), (-400.0, -240.5)),     # bottom-left
    ],
)
def test_corners_map_correctly(point, expected):
    assert L.to_psychopy_pix(point, IMAGE_SIZE) == expected


def test_y_axis_is_flipped():
    # Image y grows downwards; psychopy y grows upwards.
    above = L.to_psychopy_pix((400, 100), IMAGE_SIZE)
    below = L.to_psychopy_pix((400, 400), IMAGE_SIZE)
    assert above[1] > below[1]


# ---------------------------------------------------------------------------
# Packaged layout
# ---------------------------------------------------------------------------


@pytest.fixture
def default_layout():
    return L.load_layout()


def test_packaged_layout_loads(default_layout):
    assert set(default_layout.buttons) == set("rludabxy")
    assert default_layout.image_size == (800.0, 481.0)
    assert default_layout.image_path.is_file()


def test_packaged_geometry_matches_upstream(default_layout):
    # Upstream BUTTONS: 'a' was a circle centred at (648, 200) r=25, and 'l'
    # was a polygon whose first vertex was (132, 176). Both in image pixels.
    a = default_layout.buttons["a"]
    assert a.kind == "circle"
    assert a.center == (248.0, 40.5)
    assert a.radius == 25

    left = default_layout.buttons["l"]
    assert left.kind == "polygon"
    assert left.vertices[0] == (-268.0, 64.5)
    assert len(left.vertices) == 5


def test_halves_partition_the_buttons(default_layout):
    assert default_layout.half_for("a") == "r"
    assert default_layout.half_for("u") == "l"
    assert default_layout.half_for("nope") is None


def test_cues_are_defined_for_both_conditions(default_layout):
    assert set(default_layout.cues) == {"short", "long"}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_valid_layout_reports_no_problem(default_layout):
    assert L.validate_layout(default_layout, list("rludabxy"), lr_mode=True) is None


def test_key_without_a_button_is_reported(default_layout):
    problem = L.validate_layout(default_layout, ["a", "zzz"])
    assert problem is not None
    assert "zzz" in problem


def test_missing_cue_is_reported(default_layout):
    problem = L.validate_layout(default_layout, ["a"], conditions=("short", "medium"))
    assert problem is not None
    assert "medium" in problem


def test_lr_mode_without_halves_is_reported(tmp_path, default_layout):
    stripped = L.ControllerLayout(
        source=default_layout.source,
        image_path=default_layout.image_path,
        image_size=default_layout.image_size,
        buttons=default_layout.buttons,
        halves={},
        highlight=default_layout.highlight,
        cues=default_layout.cues,
        dim_opacity=default_layout.dim_opacity,
    )
    assert L.validate_layout(stripped, ["a"], lr_mode=False) is None
    problem = L.validate_layout(stripped, ["a"], lr_mode=True)
    assert problem is not None
    assert "halves" in problem


# ---------------------------------------------------------------------------
# Custom layouts
# ---------------------------------------------------------------------------


def _write_layout(tmp_path, **overrides):
    data = {
        "image": "pad.png",
        "image_size": [100, 100],
        "buttons": {"a": {"shape": "circle", "center": [50, 50], "radius": 10}},
        "cues": {"short": {"shape": "circle", "radius": 5}},
    }
    data.update(overrides)
    path = tmp_path / "layout.json"
    path.write_text(json.dumps(data))
    (tmp_path / "pad.png").write_bytes(b"not really a png")
    return path


def test_custom_layout_resolves_image_relative_to_itself(tmp_path):
    path = _write_layout(tmp_path)
    lay = L.load_layout(path)
    # The image is found beside the JSON, so a custom pad travels as one dir.
    assert lay.image_path == (tmp_path / "pad.png").resolve()


def test_missing_layout_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        L.load_layout(tmp_path / "nope.json")


def test_unknown_shape_is_rejected(tmp_path):
    path = _write_layout(tmp_path, buttons={"a": {"shape": "hexagon"}})
    with pytest.raises(ValueError, match="unsupported shape"):
        L.load_layout(path)


def test_polygon_needs_three_vertices(tmp_path):
    path = _write_layout(
        tmp_path, buttons={"a": {"shape": "polygon", "vertices": [[0, 0], [1, 1]]}}
    )
    with pytest.raises(ValueError, match=">= 3 vertices"):
        L.load_layout(path)


def test_empty_buttons_is_rejected(tmp_path):
    path = _write_layout(tmp_path, buttons={})
    with pytest.raises(ValueError, match="`buttons` is empty"):
        L.load_layout(path)


def test_comment_keys_are_ignored(tmp_path):
    # The packaged layout carries a _comment block; it must not break parsing.
    path = _write_layout(tmp_path, _comment=["anything at all"])
    assert L.load_layout(path).buttons["a"].radius == 10
