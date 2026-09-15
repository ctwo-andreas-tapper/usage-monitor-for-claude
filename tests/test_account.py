"""
Account Tests
==============

Unit tests for the Account value and the label rule.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from usage_monitor_for_claude.account import DEFAULT_LABEL, Account, accounts_from_config_dirs, default_config_dir, is_default_config_dir


class TestAccount(unittest.TestCase):

    def test_credentials_path_is_inside_config_dir(self):
        account = Account(Path('/tmp/claude-work'), 'claude-work')
        self.assertEqual(account.credentials_path, Path('/tmp/claude-work') / '.credentials.json')

    def test_is_frozen(self):
        account = Account(Path('/tmp/claude-work'), 'claude-work')
        with self.assertRaises(Exception):
            account.label = 'other'  # type: ignore[misc]


class TestAccountsFromConfigDirs(unittest.TestCase):

    def test_empty_list_yields_default_account(self):
        with TemporaryDirectory() as home_tmp, patch.object(Path, 'home', return_value=Path(home_tmp)):
            accounts = accounts_from_config_dirs([])
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0].label, DEFAULT_LABEL)
        self.assertEqual(accounts[0].config_dir, (Path(home_tmp) / '.claude').resolve())

    def test_label_is_directory_name(self):
        with TemporaryDirectory() as tmp:
            work = Path(tmp) / 'claude-work'
            work.mkdir()
            accounts = accounts_from_config_dirs([str(work)])
        self.assertEqual(accounts[0].label, 'claude-work')
        self.assertEqual(accounts[0].config_dir, work.resolve())

    def test_default_directory_gets_default_label(self):
        with TemporaryDirectory() as home_tmp, patch.object(Path, 'home', return_value=Path(home_tmp)):
            claude_dir = Path(home_tmp) / '.claude'
            claude_dir.mkdir()
            accounts = accounts_from_config_dirs([str(claude_dir)])
        self.assertEqual(accounts[0].label, DEFAULT_LABEL)

    def test_order_is_preserved(self):
        with TemporaryDirectory() as tmp:
            first = Path(tmp) / 'b-account'
            second = Path(tmp) / 'a-account'
            first.mkdir()
            second.mkdir()
            accounts = accounts_from_config_dirs([str(first), str(second)])
        self.assertEqual([account.label for account in accounts], ['b-account', 'a-account'])

    def test_same_directory_name_in_two_parents_gets_numbered(self):
        with TemporaryDirectory() as tmp:
            one = Path(tmp) / 'one' / 'claude'
            two = Path(tmp) / 'two' / 'claude'
            one.mkdir(parents=True)
            two.mkdir(parents=True)
            accounts = accounts_from_config_dirs([str(one), str(two)])
        self.assertEqual([account.label for account in accounts], ['claude', 'claude (2)'])

    def test_paths_are_resolved(self):
        with TemporaryDirectory() as tmp:
            work = Path(tmp) / 'claude-work'
            work.mkdir()
            accounts = accounts_from_config_dirs([str(work / '..' / 'claude-work')])
        self.assertEqual(accounts[0].config_dir, work.resolve())

    def test_nonexistent_directory_still_yields_account(self):
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / 'typo-account'
            accounts = accounts_from_config_dirs([str(missing)])
        self.assertEqual(accounts[0].label, 'typo-account')
        self.assertEqual(accounts[0].config_dir, missing.resolve())


class TestDefaultConfigDir(unittest.TestCase):

    def test_default_config_dir_is_home_claude(self):
        with TemporaryDirectory() as home_tmp, patch.object(Path, 'home', return_value=Path(home_tmp)):
            self.assertEqual(default_config_dir(), (Path(home_tmp) / '.claude').resolve())

    def test_is_default_ignores_case_on_windows(self):
        with TemporaryDirectory() as home_tmp, patch.object(Path, 'home', return_value=Path(home_tmp)):
            claude_dir = Path(home_tmp) / '.claude'
            self.assertTrue(is_default_config_dir(claude_dir))
            self.assertFalse(is_default_config_dir(Path(home_tmp) / 'other'))
            if sys.platform == 'win32':
                self.assertTrue(is_default_config_dir(Path(str(claude_dir).upper())))


if __name__ == '__main__':
    unittest.main()
