"""First-run configuration wizard and subject picker.

Two dialogs, both optional — everything they set can also be given on the
command line or in ``config.json``. They exist so an operator can set up a new
rig, or start a session, without memorising flags.

All PsychoPy / Qt imports are deferred into the function bodies, so this
module is importable on a headless machine (and its pure helpers are
unit-testable there).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from controller_validation_task import design as design_mod
from controller_validation_task import settings as settings_mod
from controller_validation_task.paths import infer_next_session, list_subjects, normalize_subject
from controller_validation_task.settings import Settings

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dialog field table
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Field:
    """One row of a dialog.

    ``key`` is what :func:`settings_from_wizard` looks for; ``label`` is what
    the operator sees. They are deliberately different (labels carry units and
    hints), which is exactly why results must be read back **by label** — see
    :func:`read_dialog_values`.

    Labels carry the units and hints an operator needs, since there is nowhere
    else in the dialog to put them.
    """

    key: str
    label: str
    initial: Any = ""
    choices: Sequence[str] | None = None


def read_dialog_values(fields: Sequence[Field], returned: Any) -> dict[str, Any]:
    """Map a PsychoPy dialog result back onto our answer keys.

    ``Dlg.show()`` returns an ``IndexDict`` **keyed by each field's label**
    (older PsychoPy returned a plain positional list). Zipping our key list
    against the result therefore pairs each key with a *label string* rather
    than a value — which used to surface as
    ``could not convert string to float: 'frame_rate (Hz)'``.

    Reading by label handles both shapes and, more importantly, cannot drift
    when a field is added, removed or reordered.

    >>> fields = [Field("tr", "TR (s)"), Field("n_runs", "n_runs")]
    >>> read_dialog_values(fields, {"TR (s)": 1.6, "n_runs": 2})
    {'tr': 1.6, 'n_runs': 2}
    >>> read_dialog_values(fields, [1.6, 2])          # legacy positional
    {'tr': 1.6, 'n_runs': 2}
    """
    if returned is None:
        return {}
    if isinstance(returned, Mapping):
        return {f.key: returned[f.label] for f in fields if f.label in returned}
    # Legacy: a positional sequence in field order.
    return {f.key: value for f, value in zip(fields, returned, strict=False)}


def _add_fields(dlg: Any, fields: Sequence[Field]) -> None:
    """Add every field to ``dlg``, keyed by its label."""
    for f in fields:
        if f.choices is not None:
            dlg.addField(f.label, initial=f.initial, choices=list(f.choices))
        else:
            dlg.addField(f.label, initial=f.initial)


def wizard_fields(base: Settings) -> list[Field]:
    """The configuration wizard's fields, pre-filled from ``base``.

    Pure, so the label/key mapping can be tested without opening a dialog.
    """
    return [
        Field(
            "fullscreen",
            "fullscreen",
            base.display.fullscreen,
        ),
        Field(
            "frame_rate",
            "frame_rate (Hz)",
            base.display.frame_rate,
        ),
        Field(
            "output_root",
            "output_root",
            base.paths.output_root,
        ),
        Field(
            "n_runs",
            "n_runs",
            base.design.n_runs,
        ),
        Field(
            "lr_mode",
            "lr_mode",
            base.design.lr_mode,
        ),
        Field(
            "n_blocks_per_condition",
            "n_blocks_per_condition",
            base.design.n_blocks_per_condition,
        ),
        Field(
            "tr",
            "TR (s)",
            base.design.tr,
        ),
        Field(
            "isi_range",
            "isi_range (s)",
            _pair_text(base.design.isi_range),
        ),
        Field(
            "block_isi",
            "block_isi (s)",
            "" if base.design.block_isi is None else str(base.design.block_isi),
        ),
        Field(
            "short_press_duration",
            "short_press_duration (s)",
            base.design.short_press_duration,
        ),
        Field(
            "long_duration_range",
            "long_duration_range (s)",
            _pair_text(base.design.long_duration_range),
        ),
        Field(
            "initial_wait",
            "initial_wait (s)",
            base.design.initial_wait,
        ),
        Field(
            "final_wait",
            "final_wait (s)",
            base.design.final_wait,
        ),
        Field(
            "sync_mode",
            "sync_mode",
            base.sync.mode,
            ["none", "send", "wait"],
        ),
        Field(
            "sync_backend",
            "sync_backend",
            base.sync.backend,
            ["none", "serial", "parallel", "lsl", "key", "keyboard", "markers"],
        ),
        Field(
            "sync_port",
            "sync_port",
            base.sync.port or "",
        ),
        Field(
            "sync_signal",
            "sync_signal",
            ",".join(base.sync.signal),
        ),
        Field(
            "trigger_backend",
            "trigger_backend",
            base.triggers.backend,
            ["null", "lsl", "serial", "parallel"],
        ),
        Field(
            "trigger_port",
            "trigger_port",
            base.triggers.port or "",
        ),
    ]



@dataclass(frozen=True)
class Tab:
    """One page of the wizard: a title, a one-line blurb, and its fields."""

    title: str
    blurb: str
    keys: tuple[str, ...]


#: The wizard's tabs, in tab order. Every key in :func:`wizard_fields` must
#: appear in exactly one of them — ``test_every_wizard_field_lands_in_one_tab``
#: enforces that, so adding a field and forgetting to place it is a test
#: failure rather than a field that silently vanishes from the dialog.
WIZARD_TABS: tuple[Tab, ...] = (
    Tab(
        "Session",
        "What one session contains, and where it is written.",
        ("output_root", "n_runs", "lr_mode"),
    ),
    Tab(
        "Design",
        "Trial structure and timing of one run. Blank isi_range / block_isi derive from the TR "
        "as [TR, 2*TR] and 2*TR; ranges are 'low, high'. The estimate below updates as you type.",
        (
            "n_blocks_per_condition",
            "tr",
            "isi_range",
            "block_isi",
            "short_press_duration",
            "long_duration_range",
            "initial_wait",
            "final_wait",
        ),
    ),
    Tab(
        "Display",
        "The screen the participant looks at.",
        ("fullscreen", "frame_rate"),
    ),
    Tab(
        "Scanner sync",
        "How the run and the scanner agree on t=0. Leave as-is outside an MRI.",
        ("sync_mode", "sync_backend", "sync_port", "sync_signal"),
    ),
    Tab(
        "Markers",
        "Per-event triggers for MEG / EEG. Leave as-is for fMRI-only rigs.",
        ("trigger_backend", "trigger_port"),
    ),
)


def wizard_sections(fields: Sequence[Field]) -> list[tuple[Tab, list[Field]]]:
    """Group ``fields`` into :data:`WIZARD_TABS`, in tab order.

    Pure, so the grouping is testable without a display. Raises if a tab names
    a key that :func:`wizard_fields` does not provide.
    """
    by_key = {f.key: f for f in fields}
    sections = []
    for tab in WIZARD_TABS:
        missing = [k for k in tab.keys if k not in by_key]
        if missing:
            raise KeyError(f"tab {tab.title!r} names unknown field(s): {missing}")
        sections.append((tab, [by_key[k] for k in tab.keys]))
    return sections


def _qt_widgets() -> Any:
    """The Qt binding PsychoPy's dialogs are built on, or ``None``.

    ``psychopy.gui`` may be backed by wx instead (or by nothing at all on a
    headless box), so every caller has to cope with ``None``.
    """
    try:
        from psychopy.gui import qtgui
    except Exception:  # noqa: BLE001 - no Qt, or no display
        return None
    return getattr(qtgui, "QtWidgets", None)


def _add_tabbed_fields(dlg: Any, sections: Sequence[tuple[Tab, Sequence[Field]]]) -> bool:
    """Lay ``sections`` out as tabs inside ``dlg``. ``False`` if it can't.

    ``psychopy.gui.Dlg`` is a single-column ``QGridLayout`` with no notion of
    pages, so — as in ``mario_task``'s level grid — we drop one level down to
    Qt: a ``QTabWidget`` spans both columns, and each field is added the normal
    way and then *moved* out of the dialog's grid into its page.

    Adding fields through ``dlg.addField`` rather than building the widgets
    ourselves is what keeps this safe: PsychoPy still owns the change signals,
    so ``dlg.show()`` returns the same label-keyed dict it always did, no
    matter where the widget physically sits.
    """
    qt = _qt_widgets()
    if qt is None or not isinstance(getattr(dlg, "layout", None), qt.QGridLayout):
        return False

    tabs = qt.QTabWidget(parent=dlg)
    dlg.layout.addWidget(tabs, dlg.irow, 0, 1, 2)
    dlg.irow += 1

    for tab, fields in sections:
        page = qt.QWidget(parent=tabs)
        grid = qt.QGridLayout(page)
        grid.setContentsMargins(12, 12, 12, 12)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        grid.setColumnMinimumWidth(1, 260)
        grid.setColumnStretch(1, 1)

        blurb = qt.QLabel(tab.blurb, parent=page)
        blurb.setWordWrap(True)
        grid.addWidget(blurb, 0, 0, 1, 2)

        for row, f in enumerate(fields, start=1):
            origin = dlg.irow
            _add_fields(dlg, [f])
            for col in (0, 1):
                item = dlg.layout.itemAtPosition(origin, col)
                widget = item.widget() if item is not None else None
                if widget is None:  # PsychoPy changed its layout under us
                    return False
                dlg.layout.removeWidget(widget)
                grid.addWidget(widget, row, col)
            # addField consumed a row of the dialog's grid; give it back so the
            # OK/Cancel box lands directly under the tabs.
            dlg.irow = origin

        grid.setRowStretch(len(fields) + 1, 1)
        tabs.addTab(page, tab.title)

    return True


def _polish(dlg: Any, *, ok_label: str) -> None:
    """Two cosmetic fixes ``psychopy.gui.Dlg`` does not do for us.

    ``validate()`` is what hides the "fields marked with an asterisk (*) are
    required" banner. Nothing here is required, but the banner is shown at
    construction and only taken down on the first edit — so call it once.

    ``labelButtonOK`` is accepted by ``Dlg.__init__`` and then never applied
    (the assignment is commented out upstream), so the button is renamed here.

    Both are best-effort: a wx-backed or otherwise unfamiliar dialog just keeps
    its defaults.
    """
    for tweak in (lambda: dlg.validate(), lambda: dlg.okBtn.setText(ok_label)):
        try:
            tweak()
        except Exception:  # noqa: BLE001 - cosmetic only, never worth a crash
            pass


def _add_inline_sections(dlg: Any, sections: Sequence[tuple[Tab, Sequence[Field]]]) -> None:
    """Fallback for :func:`_add_tabbed_fields`: headed sections, one column."""
    for tab, fields in sections:
        dlg.addText(tab.title)
        _add_fields(dlg, fields)

# ---------------------------------------------------------------------------
# Pure helpers (unit-tested)
# ---------------------------------------------------------------------------


def _pair_text(value: Sequence[float] | None) -> str:
    """``(1.0, 3.0)`` -> ``"1.0, 3.0"``; ``None`` -> ``""`` (a blank field).

    >>> _pair_text((1.0, 3.0))
    '1.0, 3.0'
    >>> _pair_text(None)
    ''
    """
    return "" if value is None else ", ".join(str(float(v)) for v in value)


def _parse_pair(value: Any, key: str) -> tuple[float, float]:
    """The inverse of :func:`_pair_text`, tolerant of brackets and spacing.

    >>> _parse_pair("1, 3", "x")
    (1.0, 3.0)
    >>> _parse_pair("[0.5 1.5]", "x")
    (0.5, 1.5)
    >>> _parse_pair((2, 4), "x")
    (2.0, 4.0)
    """
    if isinstance(value, (list, tuple)):
        parts = [str(v) for v in value]
    else:
        text = str(value).strip().strip("[]()")
        parts = [part for part in text.replace(",", " ").split() if part]
    if len(parts) != 2:
        raise ValueError(f"{key} must be two numbers, 'low, high'; got {value!r}.")
    return float(parts[0]), float(parts[1])


def wizard_estimate(
    base: Settings,
    answers: Mapping[str, Any],
    halves: Mapping[str, Sequence[str]] | None = None,
) -> str:
    """The duration line shown under the wizard's tabs, for ``answers`` so far.

    ``answers`` is whatever the dialog currently holds, which mid-edit may be
    half-typed (``"1."``, ``""``); anything that cannot be turned into a
    valid design yields an explanatory line instead of an exception, so the
    label never breaks the dialog.
    """
    try:
        settings = settings_from_wizard(base, answers)
        settings_mod._validate_design(settings.design)
    except (TypeError, ValueError) as exc:
        reason = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
        return f"Estimated duration: n/a ({reason})"
    est = design_mod.estimate_duration(
        settings.design,
        halves=halves,
        instruction_duration=settings.display.instruction_duration,
    )
    return "Estimated duration \u2014 " + design_mod.describe_estimate(est)


def _layout_halves(base: Settings) -> dict[str, tuple[str, ...]] | None:
    """The controller's hand -> buttons split, for the estimate. Best-effort."""
    from controller_validation_task import layout as layout_mod
    from controller_validation_task.paths import resolve_layout_path

    try:
        path = resolve_layout_path(base.paths.layout_file, base.paths.assets_dir)
        return layout_mod.load_layout(path).halves or None
    except Exception as exc:  # noqa: BLE001 - the estimate must not block setup
        log.debug("No controller layout for the duration estimate: %s", exc)
        return None


def _attach_estimate(
    dlg: Any,
    fields: Sequence[Field],
    base: Settings,
    halves: Mapping[str, Sequence[str]] | None,
) -> None:
    """Add a live duration line under the dialog's tabs (Qt only).

    ``Dlg`` keeps ``dlg.data`` — the same label-keyed dict ``show()`` returns
    — current on every edit, so the label simply re-reads it. Our slot is
    connected after PsychoPy's own, and Qt runs slots in connection order, so
    the value is already parsed when we look.
    """
    qt = _qt_widgets()
    if qt is None:
        return

    label = qt.QLabel(parent=dlg)
    label.setWordWrap(True)
    label.setContentsMargins(4, 8, 4, 0)
    dlg.layout.addWidget(label, dlg.irow, 0, 1, 2)
    dlg.irow += 1

    def refresh(*_args: Any) -> None:
        label.setText(wizard_estimate(base, read_dialog_values(fields, dlg.data), halves))

    for widget in getattr(dlg, "inputFields", ()):
        for signal in ("textEdited", "stateChanged", "currentIndexChanged"):
            if hasattr(widget, signal):
                getattr(widget, signal).connect(refresh)
                break
    refresh()


def subject_choices(output_root: str | os.PathLike[str]) -> list[str]:
    """Existing subjects plus a ``"<new subject>"`` sentinel, for a dropdown."""
    return [*list_subjects(output_root), NEW_SUBJECT]


#: Sentinel entry in the subject dropdown.
NEW_SUBJECT = "<new subject>"


def suggest_session(output_root: str | os.PathLike[str], subject: str) -> str:
    """Session number to pre-fill for ``subject`` (``"001"`` if they're new)."""
    return infer_next_session(output_root, subject) if subject else "001"


def subject_fields(output_root: str | os.PathLike[str]) -> list[Field]:
    """The subject picker's fields. Pure, so the choices are testable."""
    choices = subject_choices(output_root)
    return [
        Field(
            "picked",
            "existing subject",
            choices[0],
            choices,
        ),
        Field(
            "typed",
            "new subject id",
            "",
        ),
        Field(
            "session",
            "session",
            "",
        ),
    ]


def settings_from_wizard(base: Settings, answers: dict) -> Settings:
    """Fold a flat dict of wizard answers back into nested :class:`Settings`.

    Kept separate from the dialog so the mapping can be tested without a
    display. Unknown or blank answers fall back to ``base``.
    """

    def _blank_to_none(value):
        text = str(value).strip() if value is not None else ""
        return text or None

    def _split_keys(value):
        text = str(value).strip() if value is not None else ""
        return tuple(part.strip() for part in text.split(",") if part.strip())

    display = replace(
        base.display,
        fullscreen=bool(answers.get("fullscreen", base.display.fullscreen)),
        frame_rate=float(answers.get("frame_rate", base.display.frame_rate)),
    )
    paths = replace(
        base.paths,
        output_root=str(answers.get("output_root") or base.paths.output_root),
    )
    def _opt_float(key, current):
        if key not in answers:
            return current
        text = _blank_to_none(answers[key])
        return None if text is None else float(text)

    def _opt_pair(key, current):
        if key not in answers:
            return current
        text = _blank_to_none(answers[key])
        return None if text is None else _parse_pair(text, key)

    design = replace(
        base.design,
        n_runs=int(answers.get("n_runs", base.design.n_runs)),
        n_blocks_per_condition=int(
            answers.get("n_blocks_per_condition", base.design.n_blocks_per_condition)
        ),
        lr_mode=bool(answers.get("lr_mode", base.design.lr_mode)),
        tr=float(answers.get("tr", base.design.tr)),
        isi_range=_opt_pair("isi_range", base.design.isi_range),
        block_isi=_opt_float("block_isi", base.design.block_isi),
        short_press_duration=float(
            answers.get("short_press_duration", base.design.short_press_duration)
        ),
        long_duration_range=(
            _opt_pair("long_duration_range", base.design.long_duration_range)
            or base.design.long_duration_range
        ),
        initial_wait=float(answers.get("initial_wait", base.design.initial_wait)),
        final_wait=float(answers.get("final_wait", base.design.final_wait)),
    )
    sync = replace(
        base.sync,
        mode=str(answers.get("sync_mode") or base.sync.mode),
        backend=str(answers.get("sync_backend") or base.sync.backend),
        port=_blank_to_none(answers.get("sync_port")),
        signal=_split_keys(answers.get("sync_signal")) or base.sync.signal,
    )
    triggers = replace(
        base.triggers,
        backend=str(answers.get("trigger_backend") or base.triggers.backend),
        port=_blank_to_none(answers.get("trigger_port")),
    )
    return replace(
        base, display=display, paths=paths, design=design, sync=sync, triggers=triggers
    )


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------


def run_config_wizard(config_path: str | os.PathLike[str]) -> Settings | None:
    """Ask for the rig's settings and write ``config.json``.

    Returns the saved settings, or ``None`` if the operator cancelled.
    Re-opening the wizard on an existing config pre-fills it with the current
    values, so it doubles as an editor.
    """
    from psychopy import gui as psygui

    base = settings_mod.default_settings()
    p = Path(config_path)
    if p.exists():
        try:
            base = settings_mod.load_from_file(p)
        except (OSError, ValueError) as exc:
            log.warning("Could not read %s (%s); starting from defaults.", p, exc)

    fields = wizard_fields(base)
    sections = wizard_sections(fields)
    halves = _layout_halves(base)

    dlg = psygui.Dlg(title="Controller validation task - setup")
    if _add_tabbed_fields(dlg, sections):
        _attach_estimate(dlg, fields, base, halves)
    else:
        # No Qt (or PsychoPy moved its layout around): fall back to one long
        # column with headings. Same fields, same answers, less pleasant — and
        # the estimate is a snapshot of the current config rather than live.
        log.debug("Tabbed layout unavailable; falling back to inline sections.")
        dlg = psygui.Dlg(title="Controller validation task - setup")
        _add_inline_sections(dlg, sections)
        dlg.addText(wizard_estimate(base, {}, halves))
    _polish(dlg, ok_label="Save")

    returned = dlg.show()
    if returned is None or not getattr(dlg, "OK", True):
        return None

    answers = read_dialog_values(fields, returned)

    try:
        settings = settings_from_wizard(base, answers)
        settings_mod.save(settings, p)
    except ValueError as exc:
        # Show the validation message and reopen, rather than exiting with a
        # traceback at someone who typed a port wrong.
        psygui.warnDlg(prompt=f"{exc}\n\nPlease correct the settings.", title="Invalid settings")
        return run_config_wizard(p)

    log.info("Wrote %s", p)
    return settings


def pick_subject(output_root: str | os.PathLike[str]) -> tuple[str, str] | None:
    """Ask which subject / session to run. Returns ``(subject, session)``.

    Returns ``None`` if the operator cancelled. Picking an existing subject
    pre-fills their next session number; ``<new subject>`` lets you type one.
    """
    from psychopy import gui as psygui

    fields = subject_fields(output_root)

    dlg = psygui.Dlg(title="Controller validation task - subject")
    _add_fields(dlg, fields)
    _polish(dlg, ok_label="Start session")
    returned = dlg.show()
    if returned is None or not getattr(dlg, "OK", True):
        return None

    answers = read_dialog_values(fields, returned)
    picked = answers.get("picked", "")
    typed = answers.get("typed", "")
    session = answers.get("session", "")

    subject = normalize_subject(str(typed).strip())
    if not subject:
        if picked == NEW_SUBJECT:
            psygui.warnDlg(prompt="Please type a subject id.", title="No subject")
            return pick_subject(output_root)
        subject = str(picked)

    session = str(session).strip()
    if not session:
        session = suggest_session(output_root, subject)
    else:
        from controller_validation_task.paths import normalize_session

        session = normalize_session(session)

    return subject, session
