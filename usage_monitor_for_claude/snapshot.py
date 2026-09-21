"""
Snapshot file
=============

Write the current usage state to a JSON file so that other local
programs can read it without a second call to the usage endpoint.

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

__all__ = ['build_snapshot', 'quota_label', 'snapshot_path', 'write_snapshot']

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_PERIOD_LABELS = {
    'five_hour': 'Session',
    'seven_day': 'Weekly',
}


def quota_label(field: str) -> str:
    """Return a short display label for a quota field name.

    ``'five_hour'`` becomes ``'Session'``. A model-scoped field such as
    ``'seven_day_fable'`` becomes the model name, ``'Fable'``. Any other
    field becomes its own name in title case.
    """
    for prefix, label in _PERIOD_LABELS.items():
        if field == prefix:
            return label
        if field.startswith(prefix + '_'):
            return field[len(prefix) + 1:].replace('_', ' ').title()
    return field.replace('_', ' ').title()


def build_snapshot(data: dict[str, Any], account_label: str | None, written_at: datetime) -> dict[str, Any]:
    """Return the snapshot dictionary for one usage response.

    Every top-level field whose value is an object with a ``utilization``
    key becomes one quota entry. No quota name is hardcoded, so a new
    quota type appears without a code change.
    """
    quotas = []
    for field, value in data.items():
        if not isinstance(value, dict) or value.get('utilization') is None:
            continue
        quotas.append({
            'field': field,
            'label': quota_label(field),
            'utilization': float(value['utilization']),
            'resets_at': value.get('resets_at'),
        })

    return {
        'version': SCHEMA_VERSION,
        'written_at': written_at.isoformat(),
        'account_label': account_label,
        'error': data.get('error'),
        'quotas': quotas,
    }


def snapshot_path() -> Path:
    """Return the default snapshot file path."""
    base = os.environ.get('LOCALAPPDATA') or str(Path.home() / '.cache')
    return Path(base) / 'usage-monitor-for-claude' / 'snapshot.json'


def write_snapshot(data: dict[str, Any], account_label: str | None, path: Path | None = None) -> None:
    """Write the snapshot atomically. Log and swallow every failure."""
    target = path or snapshot_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            build_snapshot(data, account_label, datetime.now(timezone.utc)),
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
