"""Display-bound smoke tests.

These need PsychoPy importable (and, for the window test, a real DISPLAY), so
they are excluded from CI with ``-k "not integration"`` and run locally with
``just test-integration``.

They deliberately do not open a window or run trials — that is verified by
actually running a session on the rig. What they catch is the class of error
that only shows up once psychopy is imported: a bad import, a typo in a
psychopy API name, or a module-level call that no longer exists.
"""

from __future__ import annotations

import pytest
from conftest import import_or_skip

psychopy = import_or_skip("psychopy", reason="psychopy not importable here")

pytestmark = pytest.mark.integration


def test_display_bound_modules_import():
    """The modules that pull in psychopy must import cleanly."""
    import controller_validation_task.cli  # noqa: F401
    import controller_validation_task.gui  # noqa: F401
    import controller_validation_task.input  # noqa: F401
    import controller_validation_task.log_setup  # noqa: F401
    import controller_validation_task.session  # noqa: F401
    import controller_validation_task.task  # noqa: F401


def test_cli_parser_accepts_the_documented_flags():
    from controller_validation_task.cli import _build_parser

    args = _build_parser().parse_args(
        [
            "01",
            "001",
            "--sync-mode",
            "send",
            "--sync-backend",
            "serial",
            "--sync-port",
            "/dev/ttyUSB0",
            "--trigger-backend",
            "lsl",
            "--lr",
            "--no-fullscreen",
        ]
    )
    assert args.subject == "01"
    assert args.session == "001"
    assert args.sync_mode == "send"
    assert args.sync_port == "/dev/ttyUSB0"
    assert args.trigger_backend == "lsl"
    assert args.lr_mode is True
    assert args.fullscreen is False


def test_cli_omitted_flags_are_none():
    """Unsupplied flags must be None so lower-precedence layers win."""
    from controller_validation_task.cli import _build_parser

    args = _build_parser().parse_args([])
    assert args.subject is None
    assert args.fullscreen is None
    assert args.lr_mode is None
    assert args.n_runs is None


def test_task_module_exposes_the_upstream_instruction_text():
    from controller_validation_task import task

    # The participant-facing wording is part of the port's fidelity.
    assert "short or long durations" in task.DEFAULT_INSTRUCTION
    assert "long bar" in task.LONG_INSTRUCTION
    assert "dot" in task.SHORT_INSTRUCTION
