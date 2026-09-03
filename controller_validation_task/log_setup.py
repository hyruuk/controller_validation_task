"""PsychoPy session log lifetime.

One ``.log`` per session, holding the timestamped record of everything the
task did: flip messages, marker sends, scanner TTLs, key releases, and the
operator's aborts.

Two details that matter:

* The :class:`psychopy.logging.LogFile` object must be **held for the whole
  session**. PsychoPy keeps only a weak reference; if it is garbage collected
  the log silently stops being written mid-run.
* A process-global ``MonotonicClock(0)`` is installed as the default log
  clock, so every line in the file shares one time base across all runs of the
  session (rather than restarting at each run's first flip).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_log_file: Any = None


def create_session_log(path: str | os.PathLike[str], *, level: int | None = None) -> Any:
    """Open the session log at ``path`` and return the LogFile.

    The caller must keep the returned object alive for the session — bind it to
    a local in the run function, not a throwaway expression.
    """
    from psychopy import core, logging

    global _log_file
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    # Shared time base for every line in the file, across all runs.
    logging.setDefaultClock(core.MonotonicClock(0))
    _log_file = logging.LogFile(
        str(p),
        level=logging.INFO if level is None else level,
        filemode="w",
    )
    logging.info(f"session log opened: {p}")
    return _log_file


def flush() -> None:
    """Write buffered log lines to disk.

    Called about once a second by the frame loop, so a crash or a power cut
    loses at most a second of records rather than the whole run.
    """
    from psychopy import logging

    logging.flush()


def close() -> None:
    """Flush and release the log file."""
    global _log_file
    try:
        flush()
    finally:
        _log_file = None
