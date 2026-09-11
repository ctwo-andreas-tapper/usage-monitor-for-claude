"""
Instance Identity
==================

Derives a per-instance identifier from the effective Claude config
directory so multiple monitor instances (one per Claude account) can
coexist, each guarding its own single-instance mutex and autostart
registry entry.

Several accounts can be named in one launch: ``--config-dir`` may be
repeated or hold an ``os.pathsep``-separated list, and so may
``CLAUDE_CONFIG_DIR``.  The launching process starts one instance per
directory and hands every instance the whole set via
``LAUNCH_CONFIG_DIRS_ENV``, so the autostart entry any of them writes
starts the whole set again.

This module must stay free of imports from ``api`` or ``settings`` -
it is used before ``CLAUDE_CONFIG_DIR`` is finalized in ``__main__``.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

__all__ = [
    'CONFIG_DIR_SEPARATOR', 'LAUNCH_CONFIG_DIRS_ENV', 'autostart_config_dir_argument', 'autostart_suffix',
    'config_dir_suffix', 'effective_config_dir', 'is_default_config_dir', 'launch_config_dirs', 'parse_config_dirs',
    'split_config_dir_list',
]

# Same separator as PATH: ';' on Windows, ':' elsewhere.
CONFIG_DIR_SEPARATOR = os.pathsep

# Set by the launching process on every per-account instance it starts:
# the resolved config directories of the whole launch, joined with
# CONFIG_DIR_SEPARATOR.  Unset when a single directory was launched directly.
LAUNCH_CONFIG_DIRS_ENV = 'USAGE_MONITOR_CONFIG_DIRS'


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


def effective_config_dir() -> Path:
    """Return the resolved Claude config directory currently in effect."""
    custom = os.environ.get('CLAUDE_CONFIG_DIR')
    base = Path(custom) if custom else Path.home() / '.claude'
    return base.resolve()


def is_default_config_dir() -> bool:
    """Return True when the effective config dir is the default ``~/.claude``."""
    return _is_default(effective_config_dir())


def config_dir_suffix() -> str:
    """Return a per-instance suffix for kernel object names.

    Empty for the default ``~/.claude`` directory (preserving the legacy
    names so older versions are still detected), otherwise an underscore
    plus a short hash of the resolved, case-normalized directory path.
    Hashing keeps the names free of characters that are invalid in Win32
    kernel object names (e.g. backslashes).
    """
    if is_default_config_dir():
        return ''

    return _hash_suffix([effective_config_dir()])


def launch_config_dirs() -> list[Path]:
    """Return the resolved config directories this launch monitors.

    The whole set when this instance was started as one of several
    (``LAUNCH_CONFIG_DIRS_ENV``), otherwise just the effective directory.
    """
    launch_set = os.environ.get(LAUNCH_CONFIG_DIRS_ENV, '')
    if not launch_set:
        return [effective_config_dir()]

    return [Path(entry).resolve() for entry in split_config_dir_list(launch_set)]


def autostart_suffix() -> str:
    """Return the suffix for the autostart entry name.

    Identical to ``config_dir_suffix()`` for a single directory, so entries
    written by earlier versions keep working.  A launch set hashes all of
    its directories together, order-independent, so every instance of the
    set toggles the same entry.
    """
    config_dirs = launch_config_dirs()
    if len(config_dirs) == 1 and _is_default(config_dirs[0]):
        return ''

    return _hash_suffix(config_dirs)


def autostart_config_dir_argument() -> str | None:
    """Return the ``--config-dir`` value the autostart entry must carry.

    None when only the default ``~/.claude`` is monitored, otherwise the
    resolved directories of the launch joined with ``CONFIG_DIR_SEPARATOR``.
    """
    config_dirs = launch_config_dirs()
    if len(config_dirs) == 1 and _is_default(config_dirs[0]):
        return None

    return CONFIG_DIR_SEPARATOR.join(str(config_dir) for config_dir in config_dirs)


def _is_default(config_dir: Path) -> bool:
    """Return True when the resolved path is the default ``~/.claude``."""
    default = (Path.home() / '.claude').resolve()
    return os.path.normcase(str(config_dir)) == os.path.normcase(str(default))


def _hash_suffix(config_dirs: list[Path]) -> str:
    """Return an underscore plus a short hash of the case-normalized paths, sorted."""
    normalized = sorted(os.path.normcase(str(config_dir)) for config_dir in config_dirs)
    return '_' + hashlib.sha1(CONFIG_DIR_SEPARATOR.join(normalized).encode('utf-8')).hexdigest()[:12]
