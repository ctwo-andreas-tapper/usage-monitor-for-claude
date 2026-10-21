"""
Settings Writer
=================

Writes the ``hidden_accounts`` key of ``usage-monitor-settings.json``.  This
is the only place the app writes that file, and it touches no other key.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .settings import SETTINGS_FILENAME

__all__ = ['SettingsWriteError', 'default_settings_path', 'save_account_visibility', 'save_hidden_accounts']

_HIDDEN_ACCOUNTS_KEY = 'hidden_accounts'


class SettingsWriteError(Exception):
    """The settings file could not be changed; the message names the reason."""


def default_settings_path() -> Path:
    """Return ``~/.claude/usage-monitor-settings.json``, where a new settings file is created."""
    return Path.home() / '.claude' / SETTINGS_FILENAME


def save_hidden_accounts(path: Path, labels: list[str]) -> None:
    """Store the hidden account labels in the settings file at *path*.

    The file is read again from disk, so edits made since the app started are
    kept.  Only the ``hidden_accounts`` key changes: every other key keeps its
    value and position.  An empty *labels* list removes the key.  A missing or
    empty file starts from ``{}``; a file that is not a JSON object is never
    overwritten.

    Parameters
    ----------
    path : Path
        The settings file to change.  Its folder is created when missing.
    labels : list[str]
        Labels of the accounts to hide, including labels of accounts that are
        not part of this launch.

    Raises
    ------
    SettingsWriteError
        When the file cannot be read, is not a JSON object, or cannot be written.
    """
    settings = _read_settings_object(path)

    if labels:
        settings[_HIDDEN_ACCOUNTS_KEY] = list(labels)
    else:
        settings.pop(_HIDDEN_ACCOUNTS_KEY, None)

    _write_atomically(path, json.dumps(settings, indent=2, ensure_ascii=False) + '\n')


def save_account_visibility(path: Path, known_labels: list[str], hidden_labels: list[str]) -> list[str]:
    """Store which of this launch's accounts are hidden, keeping every other label in the file.

    The list is read from disk in the same step as the write, so labels
    that another app instance with other accounts, or a hand edit, put
    there since this one started are kept in their place.  Only labels in
    *known_labels* change: those in *hidden_labels* are kept or appended,
    the rest are removed.  A stored value that is not a list counts as
    empty, and entries that are not strings or repeat are dropped.

    Parameters
    ----------
    path : Path
        The settings file to change.
    known_labels : list[str]
        Labels of every account in this launch.
    hidden_labels : list[str]
        Labels of this launch's accounts that are hidden now.

    Returns
    -------
    list[str]
        The list as stored.

    Raises
    ------
    SettingsWriteError
        When the file cannot be read, is not a JSON object, or cannot be written.
    """
    settings = _read_settings_object(path)
    stored = settings.get(_HIDDEN_ACCOUNTS_KEY)
    known = set(known_labels)
    hidden = set(hidden_labels)

    labels: list[str] = []
    for label in stored if isinstance(stored, list) else []:
        if not isinstance(label, str) or label in labels:
            continue
        if label in known and label not in hidden:
            continue
        labels.append(label)
    for label in hidden_labels:
        if label not in labels:
            labels.append(label)

    if labels:
        settings[_HIDDEN_ACCOUNTS_KEY] = labels
    else:
        settings.pop(_HIDDEN_ACCOUNTS_KEY, None)

    _write_atomically(path, json.dumps(settings, indent=2, ensure_ascii=False) + '\n')

    return labels


def _read_settings_object(path: Path) -> dict:
    """Return the settings stored at *path*, or ``{}`` for a missing or empty file."""
    try:
        # utf-8-sig accepts a file written with a BOM, like the loader does
        text = path.read_text(encoding='utf-8-sig').strip()
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as exc:
        raise SettingsWriteError(str(exc)) from exc

    if not text:
        return {}

    try:
        settings = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SettingsWriteError(f'The file is not valid JSON and was left unchanged: {exc}') from exc

    if not isinstance(settings, dict):
        raise SettingsWriteError(f'The file does not hold a JSON object ({type(settings).__name__}) and was left unchanged')

    return settings


def _write_atomically(path: Path, text: str) -> None:
    """Write *text* to *path* through a temporary file in the same folder."""
    temp_name = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f'{path.name}.', suffix='.tmp')
        with os.fdopen(descriptor, 'wb') as temp_file:
            temp_file.write(text.encode('utf-8'))
        os.replace(temp_name, path)
    except OSError as exc:
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
        raise SettingsWriteError(str(exc)) from exc
