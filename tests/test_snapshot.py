import json
from datetime import datetime, timezone

import pytest

from usage_monitor_for_claude.snapshot import (
    build_snapshot,
    quota_label,
    snapshot_path,
    write_snapshot,
)

WRITTEN_AT = datetime(2026, 9, 21, 14, 3, 11, tzinfo=timezone.utc)

USAGE = {
    'five_hour': {'utilization': 41.2, 'resets_at': '2026-09-21T17:00:00Z'},
    'seven_day': {'utilization': 63.8, 'resets_at': '2026-09-25T09:00:00Z'},
    'seven_day_fable': {'utilization': 12.0, 'resets_at': '2026-09-25T09:00:00Z'},
    'limits': [{'group': 'weekly'}],
    'unrelated': 'ignored',
}


def test_quota_label_known_fields():
    assert quota_label('five_hour') == 'Session'
    assert quota_label('seven_day') == 'Weekly'


def test_quota_label_model_scoped_field():
    assert quota_label('seven_day_fable') == 'Fable'
    assert quota_label('seven_day_claude_opus') == 'Claude Opus'


def test_quota_label_unknown_field():
    assert quota_label('thirty_day') == 'Thirty Day'


def test_build_snapshot_shape():
    result = build_snapshot(USAGE, 'default', WRITTEN_AT)
    assert result['version'] == 1
    assert result['written_at'] == '2026-09-21T14:03:11+00:00'
    assert result['account_label'] == 'default'
    assert result['error'] is None


def test_build_snapshot_picks_only_quota_objects():
    result = build_snapshot(USAGE, 'default', WRITTEN_AT)
    fields = [quota['field'] for quota in result['quotas']]
    assert fields == ['five_hour', 'seven_day', 'seven_day_fable']


def test_build_snapshot_quota_values():
    result = build_snapshot(USAGE, 'default', WRITTEN_AT)
    first = result['quotas'][0]
    assert first == {
        'field': 'five_hour',
        'label': 'Session',
        'utilization': 41.2,
        'resets_at': '2026-09-21T17:00:00Z',
    }


def test_build_snapshot_carries_the_error():
    result = build_snapshot({'error': 'rate limited'}, 'default', WRITTEN_AT)
    assert result['error'] == 'rate limited'
    assert result['quotas'] == []


def test_write_snapshot_creates_valid_json(tmp_path):
    target = tmp_path / 'sub' / 'snapshot.json'
    write_snapshot(USAGE, 'default', path=target)
    loaded = json.loads(target.read_text(encoding='utf-8'))
    assert loaded['version'] == 1
    assert len(loaded['quotas']) == 3


def test_write_snapshot_leaves_no_temporary_file(tmp_path):
    target = tmp_path / 'snapshot.json'
    write_snapshot(USAGE, 'default', path=target)
    assert [p.name for p in tmp_path.iterdir()] == ['snapshot.json']


def test_write_snapshot_replaces_an_existing_file(tmp_path):
    target = tmp_path / 'snapshot.json'
    target.write_text('stale', encoding='utf-8')
    write_snapshot(USAGE, 'default', path=target)
    assert json.loads(target.read_text(encoding='utf-8'))['version'] == 1


def test_write_snapshot_never_raises(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError('disk full')

    monkeypatch.setattr('usage_monitor_for_claude.snapshot.os.replace', boom)
    write_snapshot(USAGE, 'default', path=tmp_path / 'snapshot.json')


def test_snapshot_path_uses_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    assert snapshot_path() == tmp_path / 'usage-monitor-for-claude' / 'snapshot.json'


import inspect


def test_update_writes_a_snapshot_for_the_default_account_only():
    """The call site exists, and it is behind the default account gate."""
    from usage_monitor_for_claude import app

    source = inspect.getsource(app.AccountMonitor.update)
    assert 'write_snapshot(result.data, self.account.label)' in source
    assert 'is_default_config_dir(self.account.config_dir)' in source
    assert app.write_snapshot is not None
