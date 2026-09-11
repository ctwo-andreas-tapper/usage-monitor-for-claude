"""
Process Launching
==================

Starts further copies of this application: the restart requested from the
tray menu, and the per-account instances of a launch that names several
Claude config directories.  This is the only module that spawns the app
itself.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .instance_id import CONFIG_DIR_SEPARATOR, LAUNCH_CONFIG_DIRS_ENV
from .platforms import no_window_kwargs

__all__ = ['launch_account_instances', 'spawn_app_instance']


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


def launch_account_instances(config_dirs: list[str], verbose: bool = False) -> list[Path]:
    """Start one monitor instance per Claude config directory.

    Every instance receives its own directory as ``--config-dir`` and as
    ``CLAUDE_CONFIG_DIR`` (replacing an inherited list value), plus the
    whole resolved set in ``LAUNCH_CONFIG_DIRS_ENV`` - so the autostart
    entry any of them writes starts the whole set again.  A directory that
    already has a running instance is left to that instance's
    single-instance guard, which makes the new process exit quietly.

    Parameters
    ----------
    config_dirs : list[str]
        Existing config directories, one per account, in launch order.
    verbose : bool
        Pass ``--verbose`` on to every instance.

    Returns
    -------
    list[Path]
        The resolved directories, in the order the instances were started.
    """
    assert len(config_dirs) > 1, 'a single directory is monitored in-process'

    resolved = [Path(config_dir).resolve() for config_dir in config_dirs]
    launch_set = CONFIG_DIR_SEPARATOR.join(str(config_dir) for config_dir in resolved)

    for config_dir in resolved:
        arguments = [f'--config-dir={config_dir}']
        if verbose:
            arguments.append('--verbose')
        spawn_app_instance(arguments, {'CLAUDE_CONFIG_DIR': str(config_dir), LAUNCH_CONFIG_DIRS_ENV: launch_set})

    return resolved
