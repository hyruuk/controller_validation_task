"""Entry point for ``python -m controller_validation_task``.

Delegates to :func:`controller_validation_task.cli.main`, which returns a
process exit code (0 clean / 2 missing assets / 130 operator quit).
"""

from __future__ import annotations

from controller_validation_task.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
