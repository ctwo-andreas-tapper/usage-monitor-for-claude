# tests/test_snapshot_v2.py
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from usage_monitor_for_claude import snapshot


class Acc:
    def __init__(self, label, email=None, plan=None):
        self.label = label
        self.email = email
        self.plan = plan


class SnapshotV2(unittest.TestCase):
    def test_safe_name(self):
        self.assertEqual(snapshot.safe_account_name('.claude-2'), '.claude-2')
        self.assertEqual(snapshot.safe_account_name('a/b'), 'a_b')

    def test_build_is_version_2_with_full_data(self):
        data = {'five_hour': {'utilization': 58.0, 'resets_at': None},
                'error': None, 'limits': [1, 2]}
        obj = snapshot.build_snapshot(data, Acc('default', 'me@x', 'Claude Team'),
                                      datetime(2026, 9, 21, tzinfo=timezone.utc))
        self.assertEqual(obj['version'], 2)
        self.assertEqual(obj['account'], {'label': 'default', 'email': 'me@x', 'plan': 'Claude Team'})
        self.assertEqual(obj['data'], data)

    def test_error_is_lifted(self):
        data = {'error': 'HTTP 429', 'rate_limited': True}
        obj = snapshot.build_snapshot(data, Acc('default'), datetime.now(timezone.utc))
        self.assertEqual(obj['error'], 'HTTP 429')

    def test_write_uses_per_account_path(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            snapshot.write_snapshot({'five_hour': {'utilization': 1.0}}, Acc('one', 'a@x'),
                                    path=base / 'one.json')
            snapshot.write_snapshot({'five_hour': {'utilization': 2.0}}, Acc('two', 'b@x'),
                                    path=base / 'two.json')
            self.assertTrue((base / 'one.json').exists())
            self.assertTrue((base / 'two.json').exists())
            one = json.loads((base / 'one.json').read_text('utf-8'))
            self.assertEqual(one['account']['label'], 'one')


if __name__ == '__main__':
    unittest.main()
