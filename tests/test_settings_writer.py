"""
Settings Writer Tests
=======================

Unit tests for writing the ``hidden_accounts`` key to the settings file.
"""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from usage_monitor_for_claude.settings_writer import SettingsWriteError, default_settings_path, save_hidden_accounts


class TestDefaultSettingsPath(unittest.TestCase):
    """Tests for the location used when no settings file was loaded."""

    def test_points_into_home_claude_folder(self):
        with TemporaryDirectory() as home_tmp, patch.object(Path, 'home', return_value=Path(home_tmp)):
            path = default_settings_path()
        self.assertEqual(path, Path(home_tmp) / '.claude' / 'usage-monitor-settings.json')


class TestSaveHiddenAccounts(unittest.TestCase):
    """Tests for save_hidden_accounts()."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / 'sub' / 'usage-monitor-settings.json'

    def _write(self, content: str | bytes) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            self.path.write_bytes(content)
        else:
            self.path.write_text(content, encoding='utf-8')

    def _read(self) -> dict:
        return json.loads(self.path.read_text(encoding='utf-8'))

    def _folder_entries(self) -> list[str]:
        return [entry.name for entry in self.path.parent.iterdir()]

    def test_creates_file_and_missing_folder(self):
        save_hidden_accounts(self.path, ['work'])
        self.assertEqual(self._read(), {'hidden_accounts': ['work']})

    def test_output_is_indented_utf8_without_bom_with_trailing_newline(self):
        labels = ['ärger', 'b']
        save_hidden_accounts(self.path, labels)
        raw = self.path.read_bytes()
        self.assertFalse(raw.startswith(b'\xef\xbb\xbf'))
        self.assertEqual(raw.decode('utf-8'), json.dumps({'hidden_accounts': labels}, indent=2, ensure_ascii=False) + '\n')

    def test_keeps_other_keys_and_their_order(self):
        self._write('{"zeta": 1, "hidden_accounts": ["old"], "alpha": {"x": [1, 2]}}')
        save_hidden_accounts(self.path, ['new'])
        content = self._read()
        self.assertEqual(list(content), ['zeta', 'hidden_accounts', 'alpha'])
        self.assertEqual(content, {'zeta': 1, 'hidden_accounts': ['new'], 'alpha': {'x': [1, 2]}})

    def test_new_key_is_appended_after_existing_keys(self):
        self._write('{"poll_interval": 60, "bg": "#000"}')
        save_hidden_accounts(self.path, ['a'])
        self.assertEqual(list(self._read()), ['poll_interval', 'bg', 'hidden_accounts'])

    def test_empty_list_removes_the_key(self):
        self._write('{"hidden_accounts": ["a"], "bg": "#000"}')
        save_hidden_accounts(self.path, [])
        self.assertEqual(self._read(), {'bg': '#000'})

    def test_empty_list_without_existing_key_keeps_other_keys(self):
        self._write('{"bg": "#000"}')
        save_hidden_accounts(self.path, [])
        self.assertEqual(self._read(), {'bg': '#000'})

    def test_unknown_labels_are_written_as_given(self):
        save_hidden_accounts(self.path, ['codex', 'not-here-today'])
        self.assertEqual(self._read()['hidden_accounts'], ['codex', 'not-here-today'])

    def test_empty_file_starts_from_empty_object(self):
        self._write('  \n')
        save_hidden_accounts(self.path, ['a'])
        self.assertEqual(self._read(), {'hidden_accounts': ['a']})

    def test_file_with_bom_is_read_and_written_without_it(self):
        self._write(b'\xef\xbb\xbf{"bg": "#111"}')
        save_hidden_accounts(self.path, ['a'])
        self.assertEqual(self._read(), {'bg': '#111', 'hidden_accounts': ['a']})
        self.assertFalse(self.path.read_bytes().startswith(b'\xef\xbb\xbf'))

    def test_invalid_json_is_refused_and_left_untouched(self):
        self._write('{"bg": ')
        with self.assertRaises(SettingsWriteError):
            save_hidden_accounts(self.path, ['a'])
        self.assertEqual(self.path.read_text(encoding='utf-8'), '{"bg": ')
        self.assertEqual(self._folder_entries(), [self.path.name])

    def test_non_object_json_is_refused_and_left_untouched(self):
        self._write('[1, 2]')
        with self.assertRaises(SettingsWriteError):
            save_hidden_accounts(self.path, ['a'])
        self.assertEqual(self.path.read_text(encoding='utf-8'), '[1, 2]')

    def test_no_temp_file_is_left_behind(self):
        save_hidden_accounts(self.path, ['a'])
        save_hidden_accounts(self.path, ['a', 'b'])
        self.assertEqual(self._folder_entries(), [self.path.name])

    def test_failed_replace_removes_temp_file_and_keeps_old_content(self):
        self._write('{"bg": "#000"}')
        with patch('usage_monitor_for_claude.settings_writer.os.replace', side_effect=PermissionError('locked')):
            with self.assertRaises(SettingsWriteError) as raised:
                save_hidden_accounts(self.path, ['a'])
        self.assertIn('locked', str(raised.exception))
        self.assertEqual(self._folder_entries(), [self.path.name])
        self.assertEqual(self._read(), {'bg': '#000'})

    def test_write_replaces_atomically_from_the_same_folder(self):
        with patch('usage_monitor_for_claude.settings_writer.os.replace', wraps=os.replace) as replace:
            save_hidden_accounts(self.path, ['a'])
        replace.assert_called_once()
        self.assertEqual(Path(replace.call_args.args[1]), self.path)
        self.assertEqual(Path(replace.call_args.args[0]).parent, self.path.parent)

    def test_unwritable_location_raises_settings_write_error(self):
        blocker = Path(self._tmp.name) / 'blocker'
        blocker.write_text('a file where a folder should be', encoding='utf-8')
        with self.assertRaises(SettingsWriteError):
            save_hidden_accounts(blocker / 'usage-monitor-settings.json', ['a'])


if __name__ == '__main__':
    unittest.main()
