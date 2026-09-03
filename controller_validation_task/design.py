"""Deterministic per-run trial design.

One run is a sequence of *blocks*; each block has a condition (``short`` or
``long``) and contains every button exactly once, in a random order. With the
defaults — 8 buttons, 5 blocks per condition, 2 conditions — a run is
10 blocks x 8 trials = **80 trials**, about 5 minutes.

Timing, per upstream ``ses-gamepad.py``:

* the first trial starts at ``initial_wait`` seconds,
* consecutive trials are separated by their press duration plus an ISI drawn
  uniformly from ``isi_range``,
* an extra ``block_isi`` gap is inserted at every block boundary,
* the run ends ``final_wait`` seconds after the last press finishes.

``isi_range`` and ``block_isi`` default to ``[TR, 2*TR]`` and ``2*TR``, so the
jitter stays locked to the fMRI repetition time unless the operator overrides
them with absolute seconds.

Determinism
-----------
The seed comes from ``sha1(f"{subject}_{session}_{run}")`` unless
``params.seed`` is set, so re-running a session regenerates a byte-identical
design on any machine or Python version. This is a clean reimplementation of
the upstream generator rather than a bit-for-bit copy: the structure, counts
and distributions match, but the specific draws differ.

Pure module: stdlib + numpy + pandas. No psychopy import, so it is directly
unit-testable headless.
"""

from __future__ import annotations

import hashlib
import os
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:  # pragma: no cover - typing only
    from controller_validation_task.settings import DesignSettings

#: Button names cued by default, matching the packaged controller layout and
#: the keystrokes the AntiMicroX profile emits.
DEFAULT_KEYS: tuple[str, ...] = ("r", "l", "u", "d", "a", "b", "x", "y")

#: The two press durations the task contrasts.
DEFAULT_CONDITIONS: tuple[str, ...] = ("short", "long")

#: Columns of a design TSV, without the optional ``lr_condition``.
DESIGN_COLUMNS: tuple[str, ...] = ("block", "condition", "key", "duration", "onset")


def seed_for(subject: str, session: str, run: int, override: int | None = None) -> int:
    """Return the 32-bit RNG seed for one run.

    ``override`` (from ``design.seed`` in config.json) wins when set, in which
    case *every* run shares it — useful for reproducing a specific pilot, but
    it means all runs of a session get the same sequence, so it is off by
    default.

    >>> seed_for("01", "001", 1) == seed_for("01", "001", 1)
    True
    >>> seed_for("01", "001", 1) == seed_for("01", "001", 2)
    False
    """
    if override is not None:
        return int(override) % (2**32 - 1)
    digest = hashlib.sha1(f"{subject}_{session}_{run}".encode()).hexdigest()
    return int(digest, 16) % (2**32 - 1)


def resolve_isi(
    isi_range: Sequence[float] | None, block_isi: float | None, tr: float
) -> tuple[tuple[float, float], float]:
    """Resolve the TR-derived timing defaults.

    ``isi_range=None`` becomes ``(tr, 2*tr)`` and ``block_isi=None`` becomes
    ``2*tr``; explicit values pass through untouched.

    >>> resolve_isi(None, None, 1.49)
    ((1.49, 2.98), 2.98)
    >>> resolve_isi((0.5, 1.5), 3.0, 1.49)
    ((0.5, 1.5), 3.0)
    """
    rng = (float(tr), 2.0 * float(tr)) if isi_range is None else (
        float(isi_range[0]),
        float(isi_range[1]),
    )
    gap = 2.0 * float(tr) if block_isi is None else float(block_isi)
    return rng, gap


def generate_design(
    *,
    subject: str,
    session: str,
    run: int,
    params: DesignSettings,
    halves: dict[str, Sequence[str]] | None = None,
) -> pd.DataFrame:
    """Build one run's trial sequence.

    Args:
        subject:  Subject label (no ``sub-`` prefix), used for the seed.
        session:  Session label (no ``ses-`` prefix), used for the seed.
        run:      1-indexed run number, used for the seed.
        params:   The ``design`` section of the settings.
        halves:   Hand label -> buttons, from the controller layout. Required
                  when ``params.lr_mode`` is on, ignored otherwise.

    Returns:
        A DataFrame with columns ``block, condition, key, duration, onset``
        (plus ``lr_condition`` in left/right mode), one row per trial, ordered
        by ``onset``.

    Raises:
        ValueError: left/right mode is on but ``halves`` is missing or does
            not cover the configured keys.
    """
    keys = list(params.keys)
    conditions = list(params.conditions)
    seed = seed_for(subject, session, run, params.seed)
    rng = np.random.default_rng(seed)
    pyrng = random.Random(seed)

    isi_range, block_isi = resolve_isi(params.isi_range, params.block_isi, params.tr)

    # --- block order: n_blocks_per_condition of each condition, shuffled ---
    blocks = conditions * params.n_blocks_per_condition
    pyrng.shuffle(blocks)

    # --- left/right mode: one hand per block ------------------------------
    lr_labels: list[str] | None = None
    if params.lr_mode:
        if not halves:
            raise ValueError(
                "design.lr_mode is on but the controller layout defines no `halves` "
                "mapping. Add one to layout.json, or turn lr_mode off."
            )
        hands = sorted(halves)
        # Alternate hands in shuffled pairs so each hand gets an equal number
        # of blocks (upstream drew a shuffled ['l','r'] pair per block pair).
        lr_labels = []
        while len(lr_labels) < len(blocks):
            pair = list(hands)
            pyrng.shuffle(pair)
            lr_labels.extend(pair)
        lr_labels = lr_labels[: len(blocks)]

    # --- rows -------------------------------------------------------------
    rows: list[dict] = []
    for block_idx, condition in enumerate(blocks):
        if lr_labels is not None:
            hand = lr_labels[block_idx]
            block_keys = [k for k in keys if k in set(halves[hand])]
            if not block_keys:
                raise ValueError(
                    f"left/right mode: hand {hand!r} owns none of design.keys={keys}. "
                    f"Check the `halves` mapping in the controller layout."
                )
        else:
            hand = None
            block_keys = list(keys)

        order = pyrng.sample(block_keys, len(block_keys))
        for key in order:
            row = {"block": block_idx, "condition": condition, "key": key}
            if hand is not None:
                row["lr_condition"] = hand
            rows.append(row)

    df = pd.DataFrame(rows)

    # --- durations: fixed for short, drawn per-trial for long -------------
    lo, hi = float(params.long_duration_range[0]), float(params.long_duration_range[1])
    df["duration"] = float(params.short_press_duration)
    is_long = df["condition"] == "long"
    n_long = int(is_long.sum())
    if n_long:
        df.loc[is_long, "duration"] = rng.uniform(lo, hi, size=n_long)

    # --- onsets ------------------------------------------------------------
    # onset[i] = initial_wait
    #            + sum(duration[:i])          time spent pressing
    #            + sum(isi[:i])               jitter between trials
    #            + n_block_changes * block_isi extra gap at block boundaries
    n = len(df)
    isis = rng.uniform(isi_range[0], isi_range[1], size=max(n - 1, 0))
    block_changes = np.diff(df["block"].to_numpy(), prepend=df["block"].iloc[0]) != 0

    onsets = np.empty(n, dtype=float)
    onsets[0] = float(params.initial_wait)
    for i in range(1, n):
        onsets[i] = (
            onsets[i - 1]
            + float(df["duration"].iloc[i - 1])
            + float(isis[i - 1])
            + (block_isi if block_changes[i] else 0.0)
        )
    df["onset"] = onsets

    columns = ["block", "condition", "key"]
    if lr_labels is not None:
        columns.append("lr_condition")
    columns += ["duration", "onset"]
    return df[columns]


def run_duration(design: pd.DataFrame, final_wait: float) -> float:
    """Total run length in seconds: last press end + ``final_wait``."""
    if design.empty:
        return float(final_wait)
    return float(design["onset"].iloc[-1] + design["duration"].iloc[-1] + final_wait)


# ---------------------------------------------------------------------------
# Duration estimate (before any design exists)
# ---------------------------------------------------------------------------


def trials_per_block(params: DesignSettings, halves: Mapping[str, Sequence[str]] | None) -> float:
    """How many trials one block holds, on average.

    Every block cues each button once, so this is ``len(keys)`` — except in
    left/right mode, where a block cues only the lit hand's buttons and the
    hands alternate evenly, so it is the mean number of cued buttons per hand.
    Without a layout to read ``halves`` from, left/right mode assumes the pad
    splits evenly.

    >>> from controller_validation_task.settings import DesignSettings
    >>> trials_per_block(DesignSettings(), None)
    8.0
    >>> trials_per_block(DesignSettings(lr_mode=True), {"l": "lrud", "r": "abxy"})
    4.0
    """
    keys = set(params.keys)
    if not params.lr_mode:
        return float(len(keys))
    if not halves:
        return len(keys) / 2.0
    per_hand = [len(keys & set(hand_keys)) for hand_keys in halves.values()]
    return float(np.mean(per_hand)) if per_hand else 0.0


@dataclass(frozen=True)
class DurationEstimate:
    """Expected length of a run and a session, in seconds.

    ``run_seconds`` is the *expected* value of :func:`run_duration` over the
    random ISI and long-press draws; a real run lands within a few seconds of
    it. ``session_seconds`` adds the instruction screens of every run. Neither
    includes time spent waiting for a scanner trigger, which is unbounded.
    """

    n_blocks: int
    n_trials: int
    run_seconds: float
    n_runs: int
    session_seconds: float


def estimate_duration(
    params: DesignSettings,
    *,
    halves: Mapping[str, Sequence[str]] | None = None,
    instruction_duration: float = 0.0,
) -> DurationEstimate:
    """Predict how long a run and the whole session will take.

    Mirrors the onset arithmetic in :func:`generate_design`, with every random
    draw replaced by its mean::

        run = initial_wait
              + sum of press durations           short: fixed; long: mean of range
              + (n_trials - 1) * mean ISI
              + (n_blocks - 1) * block_isi
              + final_wait

    Run 1 shows three instruction screens, later runs one (see
    ``ButtonPressTask._instructions``); pass ``instruction_duration`` to
    count them in ``session_seconds``.
    """
    isi_range, block_isi = resolve_isi(params.isi_range, params.block_isi, params.tr)
    per_block = trials_per_block(params, halves)
    n_blocks = len(params.conditions) * params.n_blocks_per_condition
    n_trials = n_blocks * per_block

    long_mean = (params.long_duration_range[0] + params.long_duration_range[1]) / 2.0
    press_seconds = sum(
        per_block * params.n_blocks_per_condition
        * (long_mean if condition == "long" else params.short_press_duration)
        for condition in params.conditions
    )
    isi_seconds = max(n_trials - 1, 0) * (isi_range[0] + isi_range[1]) / 2.0
    gap_seconds = max(n_blocks - 1, 0) * block_isi

    run_seconds = params.initial_wait + press_seconds + isi_seconds + gap_seconds
    if n_trials:
        run_seconds += params.final_wait
    else:  # an empty design is just the final wait, as in run_duration()
        run_seconds = float(params.final_wait)

    n_runs = params.n_runs
    screens = 3 + max(n_runs - 1, 0) if n_runs else 0
    session_seconds = n_runs * run_seconds + screens * instruction_duration

    return DurationEstimate(
        n_blocks=int(n_blocks),
        n_trials=int(round(n_trials)),
        run_seconds=float(run_seconds),
        n_runs=int(n_runs),
        session_seconds=float(session_seconds),
    )


def format_seconds(seconds: float) -> str:
    """``292.4`` -> ``"4 min 52 s"``; under a minute, just ``"52 s"``.

    >>> format_seconds(292.4)
    '4 min 52 s'
    >>> format_seconds(45)
    '45 s'
    """
    total = int(round(seconds))
    minutes, secs = divmod(total, 60)
    return f"{minutes} min {secs} s" if minutes else f"{secs} s"


def describe_estimate(est: DurationEstimate) -> str:
    """One operator-facing line, for the wizard and the log.

    >>> describe_estimate(DurationEstimate(10, 80, 292.4, 2, 599.8))
    'Per run: 80 trials in 10 blocks, ~292 s (4 min 52 s).  Session: 2 runs, ~600 s (10 min 0 s) incl. instructions, excl. any scanner wait.'
    """
    run = f"~{est.run_seconds:.0f} s ({format_seconds(est.run_seconds)})"
    session = f"~{est.session_seconds:.0f} s ({format_seconds(est.session_seconds)})"
    runs = "run" if est.n_runs == 1 else "runs"
    return (
        f"Per run: {est.n_trials} trials in {est.n_blocks} blocks, {run}.  "
        f"Session: {est.n_runs} {runs}, {session} incl. instructions, excl. any scanner wait."
    )


def ensure_design(
    path: str | os.PathLike[str],
    *,
    subject: str,
    session: str,
    run: int,
    params: DesignSettings,
    halves: dict[str, Sequence[str]] | None = None,
    overwrite: bool = False,
) -> pd.DataFrame:
    """Load the design TSV at ``path``, generating it first if missing.

    Writing is atomic (``<path>.tmp`` then ``os.replace``), so a Ctrl+C
    mid-write cannot leave a truncated design behind.

    An existing file is re-used as-is: changing ``design.*`` in config.json
    does NOT silently regenerate a subject's design mid-study. Pass
    ``overwrite=True`` (or delete the file) to refresh it.
    """
    p = Path(path)
    if p.is_file() and not overwrite:
        return pd.read_csv(p, sep="\t")

    df = generate_design(
        subject=subject, session=session, run=run, params=params, halves=halves
    )
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    df.to_csv(tmp, sep="\t", index=False)
    os.replace(tmp, p)
    return df
