"""
Instance Identity Tests
========================

Unit tests for --config-dir parsing, the resolved config-directory set and
the single launch suffix behind the mutex and autostart entry.
"""
from __future__ import annotations

import hashlib
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from usage_monitor_for_claude.instance_id import (
    CONFIG_DIR_SEPARATOR, autostart_config_dir_argument, launch_config_dirs, launch_suffix, parse_config_dirs,
    resolve_config_dirs, split_config_dir_list,
)


def _parse_single(argv: list[str]) -> str | None:
    """Return the only parsed directory, or None when the flag yields nothing."""
    config_dirs = parse_config_dirs(argv)
    if not config_dirs:
        return None

    assert len(config_dirs) == 1, config_dirs
    return config_dirs[0]


class TestParseConfigDirs(unittest.TestCase):
    """Tests for parse_config_dirs() with a single value."""

    def test_equals_form(self):
        self.assertEqual(_parse_single(['app.exe', r'--config-dir=C:\dir']), r'C:\dir')

    def test_space_form(self):
        self.assertEqual(_parse_single(['app.exe', '--config-dir', r'C:\dir']), r'C:\dir')

    def test_absent_flag_returns_empty(self):
        self.assertEqual(parse_config_dirs(['app.exe', '--verbose']), [])

    def test_flag_without_value_returns_empty(self):
        self.assertEqual(parse_config_dirs(['app.exe', '--config-dir']), [])

    def test_empty_value_returns_empty(self):
        self.assertEqual(parse_config_dirs(['app.exe', '--config-dir=']), [])

    def test_strips_surrounding_quotes(self):
        self.assertEqual(_parse_single(['app.exe', '--config-dir="C:\\dir"']), r'C:\dir')

    def test_strips_trailing_quote_from_cmd_quoting(self):
        """cmd.exe turns --config-dir="C:\\dir\\" into a value with a trailing quote."""
        self.assertEqual(_parse_single(['app.exe', '--config-dir=C:\\dir\\"']), r'C:\dir')

    def test_strips_trailing_backslash(self):
        self.assertEqual(_parse_single(['app.exe', '--config-dir=C:\\dir\\']), r'C:\dir')

    @unittest.skipUnless(sys.platform == 'win32', '%VAR% is only expanded on Windows')
    def test_expands_environment_variables(self):
        """%VAR% syntax works even from shells that do not expand it (PowerShell)."""
        with patch.dict('os.environ', {'USERPROFILE': r'C:\Users\test'}):
            result = _parse_single(['app.exe', '--config-dir=%USERPROFILE%\\.claude-second'])
        self.assertEqual(result, r'C:\Users\test\.claude-second')

    @unittest.skipIf(sys.platform == 'win32', '$VAR is the POSIX form')
    def test_expands_posix_environment_variables(self):
        """$VAR syntax is expanded so the flag behaves the same from any shell."""
        result = _parse_single(['app', '--config-dir=$HOME/.claude-second'])
        self.assertEqual(result, str(Path.home() / '.claude-second'))

    def test_expands_tilde(self):
        """A leading ~ is expanded, so shortcut targets work unchanged."""
        result = _parse_single(['app', '--config-dir=~/.claude-second'])
        self.assertEqual(result, str(Path.home() / '.claude-second'))

    def test_drive_root_keeps_separator(self):
        """A drive root must stay a root - a bare 'D:' is drive-relative
        (the current directory on that drive), silently pointing the
        instance at a different directory."""
        self.assertEqual(_parse_single(['app.exe', '--config-dir=D:\\']), 'D:\\')

    def test_drive_root_with_cmd_trailing_quote(self):
        """cmd.exe turns --config-dir="D:\\" into a value with a trailing quote."""
        self.assertEqual(_parse_single(['app.exe', '--config-dir=D:\\"']), 'D:\\')

    def test_drive_root_forward_slash(self):
        self.assertEqual(_parse_single(['app.exe', '--config-dir=D:/']), 'D:\\')


class TestParseMultipleConfigDirs(unittest.TestCase):
    """Tests for parse_config_dirs() naming several accounts."""

    def test_repeated_flag_collects_all_in_order(self):
        argv = ['app.exe', '--config-dir=C:\\first', '--config-dir', 'C:\\second']
        self.assertEqual(parse_config_dirs(argv), ['C:\\first', 'C:\\second'])

    def test_separated_list_in_one_value(self):
        argv = ['app.exe', f'--config-dir=C:\\first{CONFIG_DIR_SEPARATOR}C:\\second']
        self.assertEqual(parse_config_dirs(argv), ['C:\\first', 'C:\\second'])

    def test_quoted_list_from_cmd(self):
        """The whole list may arrive quoted, with cmd.exe's trailing quote."""
        argv = ['app.exe', f'--config-dir="C:\\first{CONFIG_DIR_SEPARATOR}C:\\second\\"']
        self.assertEqual(parse_config_dirs(argv), ['C:\\first', 'C:\\second'])

    def test_repeated_flag_and_list_combine(self):
        argv = ['app.exe', f'--config-dir=C:\\a{CONFIG_DIR_SEPARATOR}C:\\b', '--config-dir=C:\\c']
        self.assertEqual(parse_config_dirs(argv), ['C:\\a', 'C:\\b', 'C:\\c'])

    def test_empty_entries_are_dropped(self):
        separator = CONFIG_DIR_SEPARATOR
        argv = ['app.exe', f'--config-dir={separator}C:\\first{separator}{separator} {separator}C:\\second{separator}']
        self.assertEqual(parse_config_dirs(argv), ['C:\\first', 'C:\\second'])

    def test_duplicate_directory_listed_once(self):
        """The same directory twice would start two instances of one account."""
        argv = ['app.exe', '--config-dir=C:\\first', '--config-dir=C:\\first\\']
        self.assertEqual(parse_config_dirs(argv), ['C:\\first'])

    @unittest.skipUnless(sys.platform == 'win32', 'only Windows paths ignore casing')
    def test_duplicate_differing_in_case_listed_once(self):
        argv = ['app.exe', '--config-dir=C:\\First', '--config-dir=C:\\FIRST']
        self.assertEqual(parse_config_dirs(argv), ['C:\\First'])


class TestSplitConfigDirList(unittest.TestCase):
    """Tests for split_config_dir_list() on an environment value."""

    def test_empty_value(self):
        self.assertEqual(split_config_dir_list(''), [])

    def test_single_value(self):
        self.assertEqual(split_config_dir_list('C:\\dir'), ['C:\\dir'])

    def test_list_value(self):
        self.assertEqual(split_config_dir_list(f'C:\\a{CONFIG_DIR_SEPARATOR}C:\\b'), ['C:\\a', 'C:\\b'])

    def test_entries_are_cleaned_like_the_flag(self):
        value = f'"C:\\a\\"{CONFIG_DIR_SEPARATOR}~/.claude-second'
        self.assertEqual(split_config_dir_list(value), ['C:\\a', str(Path.home() / '.claude-second')])

    def test_separator_matches_path_variable(self):
        """CLAUDE_CONFIG_DIR is split like PATH, so the same shell habits apply."""
        self.assertEqual(CONFIG_DIR_SEPARATOR, os.pathsep)


class TestResolveConfigDirs(unittest.TestCase):

    def test_flag_wins_over_environment(self):
        result = resolve_config_dirs(['app', '--config-dir=C:\\flag'], {'CLAUDE_CONFIG_DIR': 'C:\\env'})
        self.assertEqual(result, ['C:\\flag'])

    def test_environment_used_without_flag(self):
        result = resolve_config_dirs(['app'], {'CLAUDE_CONFIG_DIR': f'C:\\a{CONFIG_DIR_SEPARATOR}C:\\b'})
        self.assertEqual(result, ['C:\\a', 'C:\\b'])

    def test_nothing_yields_empty(self):
        self.assertEqual(resolve_config_dirs(['app'], {}), [])


class TestLaunchIdentity(unittest.TestCase):

    def setUp(self):
        self._dir_a = TemporaryDirectory()
        self._dir_b = TemporaryDirectory()
        self.addCleanup(self._dir_a.cleanup)
        self.addCleanup(self._dir_b.cleanup)
        self.dir_a = Path(self._dir_a.name).resolve()
        self.dir_b = Path(self._dir_b.name).resolve()

    def _launch(self, *config_dirs: Path):
        argv = ['app'] + [f'--config-dir={config_dir}' for config_dir in config_dirs]
        patcher = patch.object(sys, 'argv', argv)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = patch.dict('os.environ', {}, clear=False)
        env.start()
        os.environ.pop('CLAUDE_CONFIG_DIR', None)
        self.addCleanup(env.stop)

    def test_no_arguments_means_default_dir(self):
        with TemporaryDirectory() as home_tmp, patch.object(Path, 'home', return_value=Path(home_tmp)):
            self._launch()
            self.assertEqual(launch_config_dirs(), [(Path(home_tmp) / '.claude').resolve()])
            self.assertEqual(launch_suffix(), '')
            self.assertIsNone(autostart_config_dir_argument())

    def test_single_custom_dir(self):
        self._launch(self.dir_a)
        self.assertEqual(launch_config_dirs(), [self.dir_a])
        self.assertTrue(launch_suffix().startswith('_'))
        self.assertEqual(len(launch_suffix()), 13)
        self.assertEqual(autostart_config_dir_argument(), str(self.dir_a))

    def test_legacy_single_dir_suffix_unchanged(self):
        """Entries written by earlier versions (hash of the one directory) must keep matching."""
        self._launch(self.dir_a)
        expected = '_' + hashlib.sha1(os.path.normcase(str(self.dir_a)).encode('utf-8')).hexdigest()[:12]
        self.assertEqual(launch_suffix(), expected)

    def test_set_suffix_is_order_independent(self):
        self._launch(self.dir_a, self.dir_b)
        suffix_ab = launch_suffix()
        self._launch(self.dir_b, self.dir_a)
        self.assertEqual(launch_suffix(), suffix_ab)

    def test_set_suffix_differs_from_member_suffix(self):
        self._launch(self.dir_a)
        single = launch_suffix()
        self._launch(self.dir_a, self.dir_b)
        self.assertNotEqual(launch_suffix(), single)

    def test_set_including_default_dir_still_gets_suffix(self):
        with TemporaryDirectory() as home_tmp, patch.object(Path, 'home', return_value=Path(home_tmp)):
            claude_dir = Path(home_tmp) / '.claude'
            claude_dir.mkdir()
            self._launch(claude_dir, self.dir_b)
            self.assertNotEqual(launch_suffix(), '')
            self.assertIsNotNone(autostart_config_dir_argument())

    def test_argument_joins_resolved_dirs_in_launch_order(self):
        self._launch(self.dir_b, self.dir_a)
        self.assertEqual(autostart_config_dir_argument(), f'{self.dir_b}{CONFIG_DIR_SEPARATOR}{self.dir_a}')


if __name__ == '__main__':
    unittest.main()
