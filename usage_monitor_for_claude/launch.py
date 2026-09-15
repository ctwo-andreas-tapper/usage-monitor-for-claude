"""
Process Launching
==================

Starts the restart requested from the tray menu.  This is the only module
that spawns the app itself.
"""
from __future__ import annotations

import os
import subprocess
import sys

from .platforms import no_window_kwargs

__all__ = ['spawn_app_instance']


def spawn_app_instance(arguments: list[str], extra_env: dict[str, str] | None = None) -> None:
    """Start a detached copy of this application with the given arguments.

    A frozen build is started as its own executable, a source checkout as
    ``python -m usage_monitor_for_claude``.  PyInstaller's internal
    variables are dropped from the environment so the new process extracts
    its bundle to a fresh temporary directory instead of reusing the
    current one, which is deleted when this process exits.

    Parameters
    ----------
    arguments : list[str]
        Command-line arguments for the new instance.
    extra_env : dict[str, str] | None
        Environment variables to set on top of the inherited environment.
    """
    env = {key: value for key, value in os.environ.items() if not key.startswith(('_PYI_', '_MEI'))}
    env.update(extra_env or {})

    if getattr(sys, 'frozen', False):
        command = [sys.executable, *arguments]
    else:
        command = [sys.executable, '-m', 'usage_monitor_for_claude', *arguments]

    subprocess.Popen(command, env=env, **no_window_kwargs())
