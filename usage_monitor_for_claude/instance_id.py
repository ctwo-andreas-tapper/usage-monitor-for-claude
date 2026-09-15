"""
Instance Identity
==================

Derives the identity of one monitor process from the Claude config
directories it was launched to watch, so a launch guards its own
single-instance mutex and writes its own autostart entry.

One process now monitors the whole set: ``--config-dir`` may be repeated
or hold an ``os.pathsep``-separated list, and so may ``CLAUDE_CONFIG_DIR``.
No child processes are started; ``launch_suffix()`` names the mutex and the
autostart entry for the resolved set as a whole, order-independent, so the
same set always maps to one singleton and one **Start at login** entry.

This module must stay free of imports from ``api`` or ``settings`` -
it is used before the config directories are finalized in ``__main__``.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path

from .account import default_config_dir, is_default_config_dir

__all__ = [
    'CONFIG_DIR_SEPARATOR', 'autostart_config_dir_argument', 'launch_config_dirs', 'launch_suffix',
    'parse_config_dirs', 'resolve_config_dirs', 'split_config_dir_list',
]

# Same separator as PATH: ';' on Windows, ':' elsewhere.
CONFIG_DIR_SEPARATOR = os.pathsep


def parse_config_dirs(argv: list[str]) -> list[str]:
    """Extract every ``--config-dir`` value from command-line arguments.

    Supports both ``--config-dir=PATH`` and ``--config-dir PATH`` forms.
    The flag may be repeated, and each value may hold several paths
    separated by ``os.pathsep``; the result lists them in the order given,
    without duplicates.  Surrounding quotes and a stray trailing quote
    (left by cmd.exe when the path ends with a backslash, e.g.
    ``--config-dir="C:\\dir\\"``) are stripped.  Environment variables
    (``%USERPROFILE%``) and a leading ``~`` are expanded, so the flag works
    the same from cmd.exe, PowerShell, and shortcut targets.

    Parameters
    ----------
    argv : list[str]
        Argument list, typically ``sys.argv``.

    Returns
    -------
    list[str]
        The cleaned path values, empty if the flag is absent or has no
        value.
    """
    values: list[str] = []
    for index, arg in enumerate(argv):
        if arg.startswith('--config-dir='):
            values.append(arg.split('=', 1)[1])
        elif arg == '--config-dir' and index + 1 < len(argv):
            values.append(argv[index + 1])

    return split_config_dir_list(CONFIG_DIR_SEPARATOR.join(values))


def split_config_dir_list(value: str) -> list[str]:
    """Split an ``os.pathsep``-separated list of config directories.

    Each entry is cleaned like a single ``--config-dir`` value; empty
    entries and duplicates (compared case-insensitively on Windows) are
    dropped.

    Parameters
    ----------
    value : str
        The raw list, e.g. the ``CLAUDE_CONFIG_DIR`` environment value.

    Returns
    -------
    list[str]
        The cleaned paths in the order given.
    """
    config_dirs: list[str] = []
    seen: set[str] = set()
    for entry in value.split(CONFIG_DIR_SEPARATOR):
        cleaned = _clean_config_dir(entry)
        if cleaned is None:
            continue

        key = os.path.normcase(str(Path(cleaned).resolve()))
        if key in seen:
            continue

        seen.add(key)
        config_dirs.append(cleaned)

    return config_dirs


def _clean_config_dir(value: str) -> str | None:
    """Normalize one raw config directory value, or return None when empty."""
    value = value.strip().strip('"').rstrip('\\/')
    if not value:
        return None

    # A bare drive letter left by the rstrip ('D:') is a drive-relative path
    # (the current directory on that drive) - restore the root separator.
    if re.fullmatch(r'[A-Za-z]:', value):
        value += '\\'

    return str(Path(os.path.expandvars(value)).expanduser())


def resolve_config_dirs(argv: list[str] | None = None, environ: Mapping[str, str] | None = None) -> list[str]:
    """Return the config directories this launch names, cleaned, in order.

    ``--config-dir`` values win; without any, ``CLAUDE_CONFIG_DIR`` is split
    the same way.  Empty means only the default ``~/.claude``.

    Parameters
    ----------
    argv : list[str] | None
        Argument list, defaulting to ``sys.argv``.
    environ : Mapping[str, str] | None
        Environment mapping, defaulting to ``os.environ``.

    Returns
    -------
    list[str]
        The cleaned config directories, empty when none were named.
    """
    argv = sys.argv if argv is None else argv
    environ = os.environ if environ is None else environ

    return parse_config_dirs(argv) or split_config_dir_list(environ.get('CLAUDE_CONFIG_DIR', ''))


def launch_config_dirs() -> list[Path]:
    """Return the resolved config directories monitored by this process."""
    config_dirs = resolve_config_dirs()
    if not config_dirs:
        return [default_config_dir()]

    return [Path(config_dir).resolve() for config_dir in config_dirs]


def launch_suffix() -> str:
    """Return the suffix naming this launch's kernel objects and autostart entry.

    Empty for the single default ``~/.claude`` directory (legacy names stay
    valid), otherwise an underscore plus a short hash of the resolved,
    case-normalized directories, order-independent so the same set always
    maps to one mutex and one autostart entry.
    """
    config_dirs = launch_config_dirs()
    if len(config_dirs) == 1 and is_default_config_dir(config_dirs[0]):
        return ''

    return _hash_suffix(config_dirs)


def autostart_config_dir_argument() -> str | None:
    """Return the ``--config-dir`` value the autostart entry must carry, or None for the plain default."""
    config_dirs = launch_config_dirs()
    if len(config_dirs) == 1 and is_default_config_dir(config_dirs[0]):
        return None

    return CONFIG_DIR_SEPARATOR.join(str(config_dir) for config_dir in config_dirs)


def _hash_suffix(config_dirs: list[Path]) -> str:
    """Return an underscore plus a short hash of the case-normalized paths, sorted."""
    normalized = sorted(os.path.normcase(str(config_dir)) for config_dir in config_dirs)
    return '_' + hashlib.sha1(CONFIG_DIR_SEPARATOR.join(normalized).encode('utf-8')).hexdigest()[:12]
