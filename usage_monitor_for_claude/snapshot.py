"""
Snapshot file
=============

Write the current usage state to a JSON file so that other local
programs can read it without a second call to the usage endpoint.

The tray writes one file per account. A separate panel program reads
these files, so the schema is a contract. The file holds the version
number, the write time, the account identity, an optional error line,
and the raw usage response.

The write is atomic: the content goes to a temporary file in the same
directory, then ``os.replace`` moves it into place. A reader therefore
never sees a half-written file.

A failure here must never break the tray application, so every error is
logged and swallowed.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = [
    'account_snapshot_path',
    'build_snapshot',
    'safe_account_name',
    'snapshots_dir',
    'write_snapshot',
]

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2


def _account_fields(account: Any) -> tuple[str, str | None, str | None]:
    """Return the label, the email, and the plan for one account.

    The account is the tray ``Account`` object, or a plain label string.
    The tray ``Account`` has no email and no plan, so a missing value
    becomes ``None``.
    """
    if isinstance(account, str):
        return account, None, None
    return (
        account.label,
        getattr(account, 'email', None),
        getattr(account, 'plan', None),
    )


def snapshots_dir() -> Path:
    """Return the folder that holds the per-account snapshot files."""
    base = os.environ.get('LOCALAPPDATA') or str(Path.home() / '.cache')
    return Path(base) / 'usage-monitor-for-claude' / 'snapshots'


def safe_account_name(label: str) -> str:
    """Return a file-safe name for one account label.

    Keep letters, digits, dash, underscore, and dot. Replace every
    other character with an underscore. A leading dot stays.
    """
    return ''.join(
        c if (c.isascii() and c.isalnum()) or c in '-_.' else '_'
        for c in label
    )


def account_snapshot_path(label: str) -> Path:
    """Return the snapshot file path for one account label."""
    return snapshots_dir() / f'{safe_account_name(label)}.json'


def build_snapshot(data: dict[str, Any], account: Any, written_at: datetime) -> dict[str, Any]:
    """Return the version 2 snapshot dictionary for one usage response.

    The raw usage response goes into ``data`` unchanged. The error line,
    if any, is lifted from ``data['error']`` into the top-level
    ``error`` field.
    """
    label, email, plan = _account_fields(account)
    return {
        'version': SCHEMA_VERSION,
        'written_at': written_at.isoformat(),
        'account': {'label': label, 'email': email, 'plan': plan},
        'error': data.get('error'),
        'data': data,
    }


def write_snapshot(data: dict[str, Any], account: Any, path: Path | None = None) -> None:
    """Write the account snapshot atomically. Log and swallow every failure.

    The default path is ``account_snapshot_path(account.label)``. The
    account is the tray ``Account`` object, or a plain label string.
    """
    label, _, _ = _account_fields(account)
    target = path or account_snapshot_path(label)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            build_snapshot(data, account, datetime.now(timezone.utc)),
            ensure_ascii=False,
        )
        handle = tempfile.NamedTemporaryFile(
            'w', encoding='utf-8', dir=target.parent, prefix='.snapshot-', suffix='.tmp', delete=False
        )
        temporary = Path(handle.name)
        try:
            with handle:
                handle.write(payload)
            os.replace(temporary, target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    except Exception:  # noqa: BLE001 - the tray must keep running
        log.exception('Cannot write the usage snapshot to %s', target)
