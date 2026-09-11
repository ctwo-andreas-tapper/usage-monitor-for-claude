"""
Instance Identity Tests
========================

Unit tests for --config-dir parsing, per-instance name suffixes and the
launch-set helpers behind the autostart entry.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from usage_monitor_for_claude.instance_id import (
    CONFIG_DIR_SEPARATOR, LAUNCH_CONFIG_DIRS_ENV, autostart_config_dir_argument, autostart_suffix, config_dir_suffix,
    effective_config_dir, is_default_config_dir, launch_config_dirs, parse_config_dirs, split_config_dir_list,
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


class TestConfigDirSuffix(unittest.TestCase):
    """Tests for config_dir_suffix() and is_default_config_dir()."""

    def test_default_when_env_unset(self):
        with patch.dict('os.environ', {}, clear=False):
            os.environ.pop('CLAUDE_CONFIG_DIR', None)
            self.assertTrue(is_default_config_dir())
            self.assertEqual(config_dir_suffix(), '')

    def test_default_when_env_points_to_home_claude(self):
        with TemporaryDirectory() as home_tmp:
            claude_dir = Path(home_tmp) / '.claude'
            claude_dir.mkdir()
            with patch.object(Path, 'home', return_value=Path(home_tmp)), \
                 patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': str(claude_dir)}):
                self.assertTrue(is_default_config_dir())
                self.assertEqual(config_dir_suffix(), '')

    def test_custom_dir_produces_suffix(self):
        with TemporaryDirectory() as config_tmp:
            with patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': config_tmp}):
                self.assertFalse(is_default_config_dir())
                suffix = config_dir_suffix()
        self.assertTrue(suffix.startswith('_'))
        self.assertEqual(len(suffix), 13)

    @unittest.skipUnless(sys.platform == 'win32', 'only Windows paths ignore casing')
    def test_suffix_stable_across_casing_and_trailing_slash(self):
        with TemporaryDirectory() as config_tmp:
            with patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': config_tmp}):
                suffix_plain = config_dir_suffix()
            with patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': config_tmp.upper() + '\\'}):
                suffix_variant = config_dir_suffix()
        self.assertEqual(suffix_plain, suffix_variant)

    def test_suffix_stable_across_trailing_separator(self):
        """A trailing separator names the same directory and must not split instances."""
        with TemporaryDirectory() as config_tmp:
            with patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': config_tmp}):
                suffix_plain = config_dir_suffix()
            with patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': config_tmp + os.sep}):
                suffix_variant = config_dir_suffix()
        self.assertEqual(suffix_plain, suffix_variant)

    @unittest.skipIf(sys.platform == 'win32', 'POSIX paths are case-sensitive')
    def test_suffix_differs_by_casing_on_posix(self):
        """Different casing names a different directory, so it is a different instance."""
        with TemporaryDirectory() as config_tmp:
            with patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': config_tmp}):
                suffix_plain = config_dir_suffix()
            with patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': config_tmp.upper()}):
                suffix_variant = config_dir_suffix()
        self.assertNotEqual(suffix_plain, suffix_variant)

    def test_different_dirs_produce_different_suffixes(self):
        with TemporaryDirectory() as dir_a, TemporaryDirectory() as dir_b:
            with patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': dir_a}):
                suffix_a = config_dir_suffix()
            with patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': dir_b}):
                suffix_b = config_dir_suffix()
        self.assertNotEqual(suffix_a, suffix_b)

    def test_effective_config_dir_resolves_env_value(self):
        with TemporaryDirectory() as config_tmp:
            with patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': config_tmp}):
                self.assertEqual(effective_config_dir(), Path(config_tmp).resolve())

    def test_legacy_suffix_unchanged(self):
        """Lock and registry names written by earlier versions must still match."""
        with patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': r'C:\Users\test\.claude-second'}):
            normalized = os.path.normcase(str(Path(r'C:\Users\test\.claude-second').resolve()))
            import hashlib
            expected = '_' + hashlib.sha1(normalized.encode('utf-8')).hexdigest()[:12]
            self.assertEqual(config_dir_suffix(), expected)


class TestLaunchSet(unittest.TestCase):
    """Tests for launch_config_dirs(), autostart_suffix() and autostart_config_dir_argument()."""

    def setUp(self):
        self._dir_a = TemporaryDirectory()
        self._dir_b = TemporaryDirectory()
        self.addCleanup(self._dir_a.cleanup)
        self.addCleanup(self._dir_b.cleanup)
        self.dir_a = Path(self._dir_a.name).resolve()
        self.dir_b = Path(self._dir_b.name).resolve()

    def _environment(self, config_dir: str, launch_set: str | None):
        env = {'CLAUDE_CONFIG_DIR': config_dir}
        if launch_set is not None:
            env[LAUNCH_CONFIG_DIRS_ENV] = launch_set
        patcher = patch.dict('os.environ', env)
        patcher.start()
        if launch_set is None:
            os.environ.pop(LAUNCH_CONFIG_DIRS_ENV, None)
        self.addCleanup(patcher.stop)

    def test_single_launch_is_the_effective_dir(self):
        self._environment(str(self.dir_a), None)
        self.assertEqual(launch_config_dirs(), [self.dir_a])

    def test_launch_set_lists_every_dir_resolved(self):
        self._environment(str(self.dir_a), f'{self.dir_a}{CONFIG_DIR_SEPARATOR}{self.dir_b}{os.sep}')
        self.assertEqual(launch_config_dirs(), [self.dir_a, self.dir_b])

    def test_single_launch_suffix_matches_instance_suffix(self):
        """An entry written by an earlier version keeps toggling from the menu."""
        self._environment(str(self.dir_a), None)
        self.assertEqual(autostart_suffix(), config_dir_suffix())

    def test_default_only_launch_has_no_suffix(self):
        with TemporaryDirectory() as home_tmp:
            claude_dir = Path(home_tmp) / '.claude'
            claude_dir.mkdir()
            with patch.object(Path, 'home', return_value=Path(home_tmp)):
                self._environment(str(claude_dir), None)
                self.assertEqual(autostart_suffix(), '')
                self.assertIsNone(autostart_config_dir_argument())

    def test_set_suffix_differs_from_member_suffixes(self):
        self._environment(str(self.dir_a), f'{self.dir_a}{CONFIG_DIR_SEPARATOR}{self.dir_b}')
        set_suffix = autostart_suffix()
        self.assertNotEqual(set_suffix, config_dir_suffix())
        self.assertTrue(set_suffix.startswith('_'))
        self.assertEqual(len(set_suffix), 13)

    def test_set_suffix_is_order_independent(self):
        """Every instance of the set toggles one entry, whichever dir it monitors."""
        self._environment(str(self.dir_a), f'{self.dir_a}{CONFIG_DIR_SEPARATOR}{self.dir_b}')
        suffix_from_a = autostart_suffix()
        self._environment(str(self.dir_b), f'{self.dir_b}{CONFIG_DIR_SEPARATOR}{self.dir_a}')
        suffix_from_b = autostart_suffix()
        self.assertEqual(suffix_from_a, suffix_from_b)

    def test_set_including_default_dir_still_gets_suffix(self):
        """Adding a second account to ~/.claude must not reuse the legacy entry."""
        with TemporaryDirectory() as home_tmp:
            claude_dir = Path(home_tmp) / '.claude'
            claude_dir.mkdir()
            with patch.object(Path, 'home', return_value=Path(home_tmp)):
                self._environment(str(claude_dir), f'{claude_dir}{CONFIG_DIR_SEPARATOR}{self.dir_b}')
                self.assertNotEqual(autostart_suffix(), '')
                self.assertIsNotNone(autostart_config_dir_argument())

    def test_single_custom_dir_argument(self):
        self._environment(str(self.dir_a), None)
        self.assertEqual(autostart_config_dir_argument(), str(self.dir_a))

    def test_set_argument_joins_resolved_dirs_in_launch_order(self):
        self._environment(str(self.dir_b), f'{self.dir_b}{CONFIG_DIR_SEPARATOR}{self.dir_a}')
        self.assertEqual(autostart_config_dir_argument(), f'{self.dir_b}{CONFIG_DIR_SEPARATOR}{self.dir_a}')


if __name__ == '__main__':
    unittest.main()
