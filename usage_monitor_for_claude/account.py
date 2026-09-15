"""
Account
========

Identifies one monitored Claude account by its config directory.  Every
credential read, API call and token refresh names the account it acts
for, so several accounts can be monitored by one process without any
module holding a "current" directory.

This module imports nothing from ``api``, ``settings`` or ``i18n`` - it is
used before any of those load.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

__all__ = ['DEFAULT_LABEL', 'Account', 'accounts_from_config_dirs', 'default_config_dir', 'is_default_config_dir']

DEFAULT_LABEL = 'default'


@dataclass(frozen=True)
class Account:
    """One monitored Claude account.

    Attributes
    ----------
    config_dir : Path
        Resolved Claude config directory holding this account's login.
    label : str
        Short display name: the directory name, ``'default'`` for
        ``~/.claude``, numbered when two directories share a name.
    """

    config_dir: Path
    label: str

    @property
    def credentials_path(self) -> Path:
        return self.config_dir / '.credentials.json'


def default_config_dir() -> Path:
    """Return the resolved default Claude config directory ``~/.claude``."""
    return (Path.home() / '.claude').resolve()


def is_default_config_dir(config_dir: Path) -> bool:
    """Return True when *config_dir* is the default ``~/.claude`` (case-insensitive on Windows)."""
    return os.path.normcase(str(config_dir.resolve())) == os.path.normcase(str(default_config_dir()))


def accounts_from_config_dirs(config_dirs: list[str]) -> list[Account]:
    """Build the account list for a launch, in the order given.

    Parameters
    ----------
    config_dirs : list[str]
        Cleaned config directories (see ``instance_id.split_config_dir_list``).
        Empty means the default directory only.

    Returns
    -------
    list[Account]
        One account per directory.  A directory name used twice gets a
        ``(2)``, ``(3)`` ... suffix so labels stay unique.
    """
    if not config_dirs:
        return [Account(default_config_dir(), DEFAULT_LABEL)]

    accounts: list[Account] = []
    label_counts: dict[str, int] = {}
    for config_dir in config_dirs:
        resolved = Path(config_dir).resolve()
        label = DEFAULT_LABEL if is_default_config_dir(resolved) else resolved.name
        seen = label_counts.get(label, 0)
        label_counts[label] = seen + 1
        if seen:
            label = f'{label} ({seen + 1})'
        accounts.append(Account(resolved, label))

    return accounts
