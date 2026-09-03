"""Events TSV schema and writer.

Upstream wrote its events file with PsychoPy's
``TrialHandler.saveAsWideText``, which is **broken on pandas >= 2** (it calls
the removed ``DataFrame.append``). This module replaces it with a plain
pandas writer that produces a *column-identical* file, so existing analysis
code keeps working unchanged.

Two conventions inherited from the upstream output and deliberately kept:

* Column order is fixed (see :func:`event_columns`) — it is NOT the insertion
  order of the row dicts. Upstream's order came from the design TSV's columns
  followed by the result keys seeded on trial 0.
* Missing responses are written as **empty strings**, not ``NaN``. A trial
  where the participant never pressed the cued button has blank
  ``key_press_time`` / ``key_press_rt`` / etc.

``all_keypresses`` and ``all_keyreleases`` hold the ``repr`` of a list of
``(key, time)`` tuples, e.g. ``[('a', 12.345678)]`` — parse them downstream
with :func:`ast.literal_eval`. They exist so that trials confounded by a
stray press of another button can be excluded offline.

Pure module: stdlib + pandas. No psychopy import, so it is unit-testable
headless.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from typing import Any

import pandas as pd

#: Columns coming from the trial design, before the optional lr column.
BASE_COLUMNS: tuple[str, ...] = ("TrialNumber", "block", "condition", "key")

#: Design timing columns plus every measured/response column.
TAIL_COLUMNS: tuple[str, ...] = (
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
)

#: Columns the task fills in at runtime (everything not read from the design).
RESPONSE_COLUMNS: tuple[str, ...] = (
    "onset_flip",
    "offset_flip",
    "key_press_time",
    "key_press_rt",
    "key_release_time",
    "key_release_rt",
    "key_duration",
    "all_keypresses",
    "all_keyreleases",
)


def event_columns(*, lr_mode: bool = False) -> list[str]:
    """Return the events TSV header, in the exact upstream order.

    ``lr_condition`` is inserted as the 5th column (right after ``key``) when
    left/right-hand mode is on, matching where the design TSV put it upstream.

    >>> event_columns()[:5]
    ['TrialNumber', 'block', 'condition', 'key', 'duration']
    >>> event_columns(lr_mode=True)[:5]
    ['TrialNumber', 'block', 'condition', 'key', 'lr_condition']
    """
    lr = ["lr_condition"] if lr_mode else []
    return [*BASE_COLUMNS, *lr, *TAIL_COLUMNS]


def write_events_tsv(
    rows: Iterable[Mapping[str, Any]],
    path: str | os.PathLike[str],
    *,
    lr_mode: bool = False,
) -> int:
    """Write ``rows`` as a BIDS-style events TSV and return the row count.

    Rows are reindexed onto :func:`event_columns`, so a missing key becomes an
    empty cell and an unexpected key is dropped rather than shifting the
    header. ``TrialNumber`` is filled in 1-indexed if absent (PsychoPy
    generated it that way).

    Writing zero rows still produces a header-only file — an aborted run
    should leave evidence that it happened, not an absent file.
    """
    columns = event_columns(lr_mode=lr_mode)
    records = [dict(r) for r in rows]
    for i, rec in enumerate(records, start=1):
        rec.setdefault("TrialNumber", i)

    df = pd.DataFrame(records, columns=columns)
    # Empty strings, not NaN: matches what saveAsWideText produced, and keeps
    # "no response" visually distinct from a genuine zero.
    df = df.where(pd.notna(df), "")
    df.to_csv(path, sep="\t", index=False)
    return len(records)
