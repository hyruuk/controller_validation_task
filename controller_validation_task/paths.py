"""BIDS path resolution and asset location.

This module owns *every* filesystem path the task reads from or writes to.
Centralising them buys a single source of truth for the BIDS conventions, an
easy way for tests to redirect everything into a ``tmp_path``, and one place
to validate that the controller assets are actually on disk.

Output layout::

    output_root/
    └── sourcedata/
        └── sub-<subject>/
            ├── sub-<subject>_ses-<session>_run-<NN>_design.tsv
            └── ses-<session>/
                ├── sub-<subject>_ses-<session>_<timestamp>.log
                └── sub-<subject>_ses-<session>_<timestamp>_task-gamepad_run-<NN>_events.tsv

Designs live at the *subject* level (not inside the session dir) so that
re-running a session re-uses the same trial sequence instead of silently
generating a new one, while deleting ``sub-<subject>/`` still wipes every
trace of that subject.

Pure stdlib. No psychopy imports — safe to use from tests.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

# BIDS allows alphanumerics only in subject and session labels. We accept the
# strict-but-friendly superset {alnum, dash, underscore} and reject everything
# else — most importantly path separators and shell metacharacters, since
# these labels flow straight into filenames.
_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

_SESSION_DIR_RE = re.compile(r"^ses-(\d+)$")


def _validate_label(name: str, label: str) -> None:
    if not _LABEL_RE.fullmatch(label):
        raise ValueError(
            f"Invalid {name} label {label!r}: must match {_LABEL_RE.pattern} "
            f"(alphanumeric, dash, underscore; cannot start with dash/underscore)."
        )


def _validate_task_name(name: str) -> None:
    # task_name flows into a filename; reject anything that would let it
    # escape the session directory.
    if "/" in name or "\\" in name or ".." in name or name.startswith("."):
        raise ValueError(f"invalid task_name {name!r}")


def make_timestamp() -> str:
    """Return a BIDS-friendly current timestamp string: ``YYYYMMDD-HHMMSS``."""
    return time.strftime("%Y%m%d-%H%M%S", time.localtime())


def normalize_subject(raw: str) -> str:
    """Strip a BIDS ``sub-`` prefix if the operator typed one.

    >>> normalize_subject("sub-01")
    '01'
    >>> normalize_subject("01")
    '01'
    """
    return raw[4:] if raw.startswith("sub-") else raw


def normalize_session(raw: str) -> str:
    """Strip a BIDS ``ses-`` prefix, and zero-pad a bare number to 3 digits.

    >>> normalize_session("ses-001")
    '001'
    >>> normalize_session("1")
    '001'
    >>> normalize_session("pilot")
    'pilot'
    """
    label = raw[4:] if raw.startswith("ses-") else raw
    return f"{int(label):03d}" if label.isdigit() else label


def infer_next_session(output_root: str | Path, subject: str) -> str:
    """Return the next zero-padded session number for ``subject``.

    Scans ``output_root/sourcedata/sub-<subject>/ses-*/`` for numeric session
    labels and returns ``max(existing) + 1`` as a 3-digit string. Returns
    ``"001"`` when the subject has no sessions yet. Non-numeric labels (e.g.
    ``ses-pilot``) are ignored when picking the next number.
    """
    subj_dir = Path(output_root) / "sourcedata" / f"sub-{subject}"
    if not subj_dir.is_dir():
        return "001"
    nums: list[int] = []
    for child in subj_dir.iterdir():
        if child.is_dir():
            m = _SESSION_DIR_RE.match(child.name)
            if m:
                nums.append(int(m.group(1)))
    return f"{(max(nums) + 1) if nums else 1:03d}"


def list_subjects(output_root: str | Path) -> list[str]:
    """Return the sorted subject labels that already have output on disk."""
    src = Path(output_root) / "sourcedata"
    if not src.is_dir():
        return []
    return sorted(
        child.name[4:]
        for child in src.iterdir()
        if child.is_dir() and child.name.startswith("sub-")
    )


# ---------------------------------------------------------------------------
# BIDS paths
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BidsPaths:
    """Resolved filesystem paths for one subject / session.

    None of the directories are created automatically — the session runner
    ``mkdir``s what it needs, so tests can point this at a tmp dir with no
    side effects.

    Attributes:
        subject:     Subject label, e.g. ``"01"``. No ``sub-`` prefix.
        session:     Session label, e.g. ``"001"``. No ``ses-`` prefix.
        output_root: Root of the BIDS tree (typically ``./output``).
        timestamp:   Run timestamp ``YYYYMMDD-HHMMSS``; defaults to now.
    """

    subject: str
    session: str
    output_root: Path
    timestamp: str = field(default_factory=make_timestamp)

    def __post_init__(self) -> None:
        _validate_label("subject", self.subject)
        _validate_label("session", self.session)
        # frozen=True + object.__setattr__ lets us coerce without giving up
        # immutability for everyone else.
        if not isinstance(self.output_root, Path):
            object.__setattr__(self, "output_root", Path(self.output_root))

    # ----- directories -----

    @property
    def sourcedata_subject_dir(self) -> Path:
        return self.output_root / "sourcedata" / f"sub-{self.subject}"

    @property
    def sourcedata_session_dir(self) -> Path:
        return self.sourcedata_subject_dir / f"ses-{self.session}"

    # ----- files -----

    @property
    def session_prefix(self) -> str:
        """Filename prefix shared by every per-task artifact in this session."""
        return f"sub-{self.subject}_ses-{self.session}_{self.timestamp}"

    @property
    def log_path(self) -> Path:
        return self.sourcedata_session_dir / f"{self.session_prefix}.log"

    def design_tsv(self, run: int) -> Path:
        """Path to one run's trial design.

        Lives at the subject level so that re-running a session re-uses the
        same sequence rather than regenerating it.
        """
        return (
            self.sourcedata_subject_dir
            / f"sub-{self.subject}_ses-{self.session}_run-{run:02d}_design.tsv"
        )

    def events_tsv(self, task_name: str) -> Path:
        """Path for a task's BIDS events TSV.

        Args:
            task_name: e.g. ``"task-gamepad_run-01"``. No path separators.
        """
        _validate_task_name(task_name)
        return self.sourcedata_session_dir / f"{self.session_prefix}_{task_name}_events.tsv"


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------


def default_assets_dir() -> Path:
    """Directory holding the packaged controller image, layout and pad profile.

    Resolved relative to this module rather than the working directory, so the
    task runs correctly from anywhere (upstream hard-coded the relative path
    ``data/gamepad/ctrlr.png`` and could only be launched from the repo root).
    """
    return Path(__file__).resolve().parent / "assets"


def resolve_assets_dir(assets_dir: str | Path | None) -> Path:
    """Return ``assets_dir`` as a Path, falling back to the packaged assets."""
    return Path(assets_dir) if assets_dir else default_assets_dir()


def resolve_layout_path(
    layout_file: str | Path | None, assets_dir: str | Path | None = None
) -> Path:
    """Locate the controller layout JSON.

    ``layout_file`` wins if given; otherwise ``layout.json`` inside the
    resolved assets directory.
    """
    if layout_file:
        return Path(layout_file)
    return resolve_assets_dir(assets_dir) / "layout.json"


def check_assets(layout_file: str | Path | None, assets_dir: str | Path | None = None) -> str | None:
    """Return ``None`` if the assets are usable, else an actionable message.

    The string is printed to the operator, so it names exactly what is missing
    and how to fix it — it is deliberately not an exception.
    """
    layout_path = resolve_layout_path(layout_file, assets_dir)
    if not layout_path.is_file():
        return (
            f"Controller layout not found: {layout_path}\n"
            f"Point `paths.layout_file` in config.json at a layout JSON, or restore the "
            f"packaged default at {default_assets_dir() / 'layout.json'}."
        )

    import json

    try:
        data = json.loads(layout_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"Controller layout at {layout_path} is not readable JSON: {exc}"

    image_name = data.get("image")
    if not image_name:
        return f"Controller layout at {layout_path} has no `image` key."

    # The image is resolved relative to the layout file, so a user can keep a
    # custom PNG and layout.json together in one directory.
    image_path = (layout_path.parent / image_name).resolve()
    if not image_path.is_file() or image_path.stat().st_size == 0:
        return (
            f"Controller image missing or empty: {image_path}\n"
            f"It is referenced as `image: {image_name!r}` by {layout_path} and is "
            f"resolved relative to that file."
        )
    return None
