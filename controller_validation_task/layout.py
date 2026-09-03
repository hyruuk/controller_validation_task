"""Controller layout: JSON geometry -> PsychoPy stimuli.

Upstream hard-coded the button hit-boxes as a ``BUTTONS`` dict in the task
class (``gamepad.py:11-20``). Here they live in ``assets/layout.json`` so a
different controller — a different image, a different button arrangement, or
a pad with more or fewer buttons — can be swapped in without touching Python.

Coordinate convention
---------------------
Layout coordinates are **image pixels with the origin at the top-left**, i.e.
exactly what you read off the PNG in an image editor. PsychoPy's ``pix`` units
are centred on the window with y pointing up. :func:`to_psychopy_pix` does the
conversion::

    (x, y)  ->  (x - w/2,  -(y - h/2))

which is upstream's ``(s - size/2) * (1, -1)`` (``gamepad.py:108, 115``),
extracted here so it can be unit-tested without opening a window.

The module is importable headless: ``psychopy.visual`` is imported inside
:func:`build_stimuli`, the only function that needs it.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:  # pragma: no cover - typing only
    from psychopy.visual import Window

ShapeKind = Literal["polygon", "circle", "rect"]


# ---------------------------------------------------------------------------
# Coordinate transform
# ---------------------------------------------------------------------------


def to_psychopy_pix(
    point: Sequence[float], image_size: Sequence[float]
) -> tuple[float, float]:
    """Convert one image-pixel point to centred PsychoPy ``pix`` coordinates.

    >>> to_psychopy_pix((400, 240.5), (800, 481))
    (0.0, 0.0)
    >>> to_psychopy_pix((0, 0), (800, 481))       # top-left corner
    (-400.0, 240.5)
    >>> to_psychopy_pix((800, 481), (800, 481))   # bottom-right corner
    (400.0, -240.5)
    """
    w, h = float(image_size[0]), float(image_size[1])
    # y is written as (h/2 - y) rather than -(y - h/2): identical result,
    # but it never produces -0.0 at the exact centre.
    return (float(point[0]) - w / 2.0, h / 2.0 - float(point[1]))


# ---------------------------------------------------------------------------
# Layout model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ButtonShape:
    """One button's hit shape, already converted to PsychoPy ``pix``.

    Exactly one of ``vertices`` (polygon) or ``center``/``radius`` (circle) is
    populated, selected by ``kind``.
    """

    kind: ShapeKind
    vertices: tuple[tuple[float, float], ...] = ()
    center: tuple[float, float] = (0.0, 0.0)
    radius: float = 0.0


@dataclass(frozen=True)
class ControllerLayout:
    """A parsed, validated controller layout.

    Attributes:
        source:       Path of the layout JSON this came from.
        image_path:   Absolute path to the controller image (resolved relative
                      to ``source``, so a custom PNG can sit beside its JSON).
        image_size:   ``(width, height)`` in pixels, as drawn on screen.
        buttons:      Button name -> :class:`ButtonShape`, in ``pix`` units.
        halves:       Hand label (``"l"``/``"r"``) -> the buttons it owns.
                      Only needed for left/right-hand mode.
        highlight:    Aspect kwargs for the "this button is lit" overlay.
        cues:         Condition name -> cue stimulus spec.
        dim_opacity:  Mask value applied to the un-cued half of the image in
                      left/right mode. -1 is fully dark, 1 fully lit.
    """

    source: Path
    image_path: Path
    image_size: tuple[float, float]
    buttons: dict[str, ButtonShape]
    halves: dict[str, tuple[str, ...]]
    highlight: dict[str, Any]
    cues: dict[str, dict[str, Any]]
    dim_opacity: float

    def half_for(self, key: str) -> str | None:
        """Return the hand label owning ``key``, or ``None`` if unassigned."""
        for hand, keys in self.halves.items():
            if key in keys:
                return hand
        return None


def default_layout_path() -> Path:
    """Path to the layout JSON shipped with the package."""
    from controller_validation_task.paths import default_assets_dir

    return default_assets_dir() / "layout.json"


def load_layout(path: str | os.PathLike[str] | None = None) -> ControllerLayout:
    """Load and parse a controller layout JSON.

    ``path=None`` loads the packaged default (the original ``task_stimuli``
    controller). All coordinates are converted to PsychoPy ``pix`` here, so
    downstream code never deals with image-pixel space.

    Raises:
        FileNotFoundError: the layout file does not exist.
        ValueError: the JSON is malformed, or a button uses an unknown shape.
    """
    layout_path = Path(path) if path is not None else default_layout_path()
    if not layout_path.is_file():
        raise FileNotFoundError(f"controller layout not found: {layout_path}")

    data = json.loads(layout_path.read_text(encoding="utf-8"))

    size = data.get("image_size")
    if not (isinstance(size, (list, tuple)) and len(size) == 2):
        raise ValueError(f"{layout_path}: `image_size` must be [width, height].")
    image_size = (float(size[0]), float(size[1]))

    image_name = data.get("image")
    if not image_name:
        raise ValueError(f"{layout_path}: missing `image`.")
    image_path = (layout_path.parent / image_name).resolve()

    raw_buttons = data.get("buttons") or {}
    if not raw_buttons:
        raise ValueError(f"{layout_path}: `buttons` is empty.")
    buttons = {
        name: _parse_shape(name, spec, image_size, layout_path)
        for name, spec in raw_buttons.items()
    }

    halves = {
        hand: tuple(keys) for hand, keys in (data.get("halves") or {}).items()
    }

    return ControllerLayout(
        source=layout_path,
        image_path=image_path,
        image_size=image_size,
        buttons=buttons,
        halves=halves,
        highlight=dict(data.get("highlight") or {}),
        cues={k: dict(v) for k, v in (data.get("cues") or {}).items()},
        dim_opacity=float(data.get("dim_opacity", -0.5)),
    )


def _parse_shape(
    name: str, spec: dict, image_size: tuple[float, float], source: Path
) -> ButtonShape:
    kind = spec.get("shape")
    if kind == "polygon":
        verts = spec.get("vertices") or []
        if len(verts) < 3:
            raise ValueError(f"{source}: button {name!r} polygon needs >= 3 vertices.")
        return ButtonShape(
            kind="polygon",
            vertices=tuple(to_psychopy_pix(v, image_size) for v in verts),
        )
    if kind == "circle":
        center = spec.get("center")
        if center is None:
            raise ValueError(f"{source}: button {name!r} circle needs a `center`.")
        return ButtonShape(
            kind="circle",
            center=to_psychopy_pix(center, image_size),
            radius=float(spec.get("radius", 25)),
        )
    raise ValueError(
        f"{source}: button {name!r} has unsupported shape {kind!r} "
        f"(expected 'polygon' or 'circle')."
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_layout(
    layout: ControllerLayout,
    keys: Iterable[str],
    *,
    conditions: Iterable[str] = ("short", "long"),
    lr_mode: bool = False,
) -> str | None:
    """Return ``None`` if the layout can drive this design, else a message.

    Like :func:`controller_validation_task.paths.check_assets`, the result is
    an operator-facing string rather than an exception: the session runner
    prints it and exits cleanly instead of dumping a traceback at someone who
    is mid-setup in a scanner console room.
    """
    keys = list(keys)
    problems: list[str] = []

    missing = [k for k in keys if k not in layout.buttons]
    if missing:
        problems.append(
            f"design.keys references buttons that {layout.source} does not define: "
            f"{missing}. Defined buttons: {sorted(layout.buttons)}."
        )

    missing_cues = [c for c in conditions if c not in layout.cues]
    if missing_cues:
        problems.append(
            f"{layout.source} defines no cue for condition(s) {missing_cues}. "
            f"Defined cues: {sorted(layout.cues)}."
        )

    if lr_mode:
        if not layout.halves:
            problems.append(
                f"left/right mode is on, but {layout.source} has no `halves` mapping "
                f"saying which buttons belong to which hand."
            )
        else:
            unassigned = [k for k in keys if layout.half_for(k) is None]
            if unassigned:
                problems.append(
                    f"left/right mode is on, but these keys are in no half: {unassigned}."
                )

    if not layout.image_path.is_file():
        problems.append(f"controller image missing: {layout.image_path}")

    return "\n".join(problems) if problems else None


# ---------------------------------------------------------------------------
# Stimulus construction (needs a window)
# ---------------------------------------------------------------------------


def build_stimuli(win: Window, layout: ControllerLayout, *, lr_mode: bool = False) -> dict:
    """Build the controller image, cue and button-highlight stimuli.

    Returns a dict with keys ``image`` (ImageStim), ``cues`` (condition ->
    stim) and ``buttons`` (button name -> stim). Everything is in ``pix``
    units, positioned by :func:`to_psychopy_pix` at load time.

    ``lr_mode`` attaches the 1x2 texture mask upstream used to dim one half of
    the pad (``gamepad.py:85-86``); :func:`apply_half_mask` drives it per trial.
    """
    import numpy as np
    from psychopy import visual

    image = visual.ImageStim(
        win, image=str(layout.image_path), size=layout.image_size, units="pix"
    )
    if lr_mode:
        # A 1x2 mask stretches across the image, so each element dims exactly
        # one half. Values are in [-1, 1]; 1 = fully visible.
        image.mask = np.ones((1, 2))

    cues = {name: _build_cue(win, spec) for name, spec in layout.cues.items()}

    highlight = _highlight_kwargs(layout.highlight)
    buttons = {}
    for name, shape in layout.buttons.items():
        if shape.kind == "polygon":
            buttons[name] = visual.ShapeStim(
                win, vertices=shape.vertices, units="pix", **highlight
            )
        else:
            buttons[name] = visual.Circle(
                win, radius=shape.radius, pos=shape.center, units="pix", **highlight
            )

    return {"image": image, "cues": cues, "buttons": buttons}


def _highlight_kwargs(spec: dict) -> dict:
    """Build the PsychoPy kwargs for the "this button is lit" overlay.

    ``line_width: 0`` means "no outline". It is NOT passed through as
    ``lineWidth=0``: PsychoPy >= 2026.1 recalculates a Circle's vertex count by
    dividing by the line width, so a zero raises ``ZeroDivisionError`` the
    moment the radius is set. Suppressing the outline with ``lineColor=None``
    gives the identical appearance without the crash.
    """
    kwargs = {
        "fillColor": tuple(spec.get("fill_color", (255, 160, 110))),
        "colorSpace": spec.get("color_space", "rgb255"),
        "opacity": float(spec.get("opacity", 0.6)),
    }
    line_width = float(spec.get("line_width", 0))
    if line_width > 0:
        kwargs["lineWidth"] = line_width
        line_color = spec.get("line_color")
        kwargs["lineColor"] = tuple(line_color) if line_color else None
    else:
        kwargs["lineColor"] = None
    return kwargs


def _build_cue(win: Window, spec: dict):
    from psychopy import visual

    kind = spec.get("shape", "circle")
    color_space = spec.get("color_space", "rgb255")
    pos = tuple(spec.get("pos", (0, 0)))
    common = {"units": "pix", "colorSpace": color_space, "pos": pos}
    fill = tuple(spec.get("fill_color", (255, 255, 255)))

    if kind == "circle":
        line = tuple(spec.get("line_color", fill))
        return visual.Circle(
            win, radius=float(spec.get("radius", 25)), fillColor=fill, lineColor=line, **common
        )
    if kind == "rect":
        return visual.Rect(
            win,
            width=float(spec.get("width", 100)),
            height=float(spec.get("height", 20)),
            fillColor=fill,
            **common,
        )
    raise ValueError(f"unsupported cue shape {kind!r} (expected 'circle' or 'rect')")


def apply_half_mask(image_stim, layout: ControllerLayout, lit_hand: str) -> None:
    """Dim the half of the controller image that ``lit_hand`` does not own.

    ``lit_hand`` is a key of ``layout.halves`` (``"l"`` or ``"r"``). The mask
    is a 1x2 array whose left element covers the image's left half; assigning
    the array back to ``.mask`` is what triggers PsychoPy to re-upload the
    texture (upstream ``gamepad.py:154-157``).
    """
    mask = image_stim.mask
    mask.fill(1)
    # lit_hand == "r" -> dim the LEFT half (column 0), and vice versa.
    dim_col = slice(0, 1) if lit_hand == "r" else slice(1, None)
    mask[:, dim_col] = layout.dim_opacity
    image_stim.mask = mask
