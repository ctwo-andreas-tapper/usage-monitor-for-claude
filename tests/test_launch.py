"""
Process Launching Tests
========================

Unit tests for restarting the app and fanning a multi-account launch out
into one instance per Claude config directory.
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from usage_monitor_for_claude import launch
from usage_monitor_for_claude.instance_id import CONFIG_DIR_SEPARATOR, LAUNCH_CONFIG_DIRS_ENV


class TestSpawnAppInstance(unittest.TestCase):
    """Tests for spawn_app_instance()."""

    def setUp(self):
        patcher = patch.object(launch.subprocess, 'Popen')
        self.popen = patcher.start()
        self.addCleanup(patcher.stop)

        no_window = patch.object(launch, 'no_window_kwargs', return_value={'creationflags': 7})
        no_window.start()
        self.addCleanup(no_window.stop)

    def test_source_run_uses_module(self):
        """A source checkout is restarted through the interpreter and the module."""
        with patch.object(launch.sys, 'executable', '/usr/bin/python3'):
            launch.spawn_app_instance(['--verbose'])

        command = self.popen.call_args[0][0]
        self.assertEqual(command, ['/usr/bin/python3', '-m', 'usage_monitor_for_claude', '--verbose'])

    def test_frozen_build_runs_executable(self):
        """A frozen build is its own entry point."""
        with patch.object(launch.sys, 'frozen', True, create=True), \
             patch.object(launch.sys, 'executable', r'C:\apps\UsageMonitorForClaude.exe'):
            launch.spawn_app_instance([r'--config-dir=C:\dir'])

        command = self.popen.call_args[0][0]
        self.assertEqual(command, [r'C:\apps\UsageMonitorForClaude.exe', r'--config-dir=C:\dir'])

    def test_window_suppression_applied(self):
        """The platform's console-window suppression reaches Popen."""
        launch.spawn_app_instance([])
        self.assertEqual(self.popen.call_args[1]['creationflags'], 7)

    def test_pyinstaller_variables_dropped(self):
        """The new process must extract its own bundle, not reuse the one about to be deleted."""
        with patch.dict('os.environ', {'_PYI_APPLICATION_HOME_DIR': 'x', '_MEIPASS2': 'y', 'KEEP_ME': 'z'}):
            launch.spawn_app_instance([])

        env = self.popen.call_args[1]['env']
        self.assertNotIn('_PYI_APPLICATION_HOME_DIR', env)
        self.assertNotIn('_MEIPASS2', env)
        self.assertEqual(env['KEEP_ME'], 'z')

    def test_extra_env_added_without_touching_own_environment(self):
        with patch.dict('os.environ', {}, clear=False):
            os.environ.pop('EXTRA', None)
            launch.spawn_app_instance([], {'EXTRA': 'value'})
            self.assertNotIn('EXTRA', os.environ)

        self.assertEqual(self.popen.call_args[1]['env']['EXTRA'], 'value')

    def test_extra_env_overrides_inherited_value(self):
        with patch.dict('os.environ', {'EXTRA': 'inherited'}):
            launch.spawn_app_instance([], {'EXTRA': 'override'})

        self.assertEqual(self.popen.call_args[1]['env']['EXTRA'], 'override')


class TestLaunchAccountInstances(unittest.TestCase):
    """Tests for launch_account_instances()."""

    def setUp(self):
        patcher = patch.object(launch, 'spawn_app_instance')
        self.spawn = patcher.start()
        self.addCleanup(patcher.stop)

        self._dir_a = TemporaryDirectory()
        self._dir_b = TemporaryDirectory()
        self.addCleanup(self._dir_a.cleanup)
        self.addCleanup(self._dir_b.cleanup)
        self.dir_a = Path(self._dir_a.name).resolve()
        self.dir_b = Path(self._dir_b.name).resolve()

    def test_one_instance_per_directory_in_order(self):
        started = launch.launch_account_instances([str(self.dir_a), str(self.dir_b)])

        self.assertEqual(started, [self.dir_a, self.dir_b])
        arguments = [call.args[0] for call in self.spawn.call_args_list]
        self.assertEqual(arguments, [[f'--config-dir={self.dir_a}'], [f'--config-dir={self.dir_b}']])

    def test_every_instance_receives_the_whole_set(self):
        """The autostart entry any instance writes must start the whole set again."""
        launch.launch_account_instances([str(self.dir_a), str(self.dir_b)])

        expected = f'{self.dir_a}{CONFIG_DIR_SEPARATOR}{self.dir_b}'
        for call in self.spawn.call_args_list:
            self.assertEqual(call.args[1][LAUNCH_CONFIG_DIRS_ENV], expected)

    def test_every_instance_receives_its_own_dir_as_environment(self):
        """An inherited CLAUDE_CONFIG_DIR list must not reach the instance or the CLI it runs."""
        launch.launch_account_instances([str(self.dir_a), str(self.dir_b)])

        own_dirs = [call.args[1]['CLAUDE_CONFIG_DIR'] for call in self.spawn.call_args_list]
        self.assertEqual(own_dirs, [str(self.dir_a), str(self.dir_b)])

    def test_directories_are_resolved_before_use(self):
        """A relative or unnormalized path must not produce a second identity for the same account."""
        unresolved = str(self.dir_a) + os.sep + '.' + os.sep
        launch.launch_account_instances([unresolved, str(self.dir_b)])

        self.assertEqual(self.spawn.call_args_list[0].args[0], [f'--config-dir={self.dir_a}'])
        self.assertTrue(self.spawn.call_args_list[0].args[1][LAUNCH_CONFIG_DIRS_ENV].startswith(str(self.dir_a)))

    def test_verbose_passed_through(self):
        launch.launch_account_instances([str(self.dir_a), str(self.dir_b)], verbose=True)

        for call in self.spawn.call_args_list:
            self.assertIn('--verbose', call.args[0])

    def test_verbose_omitted_by_default(self):
        launch.launch_account_instances([str(self.dir_a), str(self.dir_b)])

        for call in self.spawn.call_args_list:
            self.assertNotIn('--verbose', call.args[0])

    def test_single_directory_is_rejected(self):
        """One account is monitored in-process; spawning a copy would only trip its own guard."""
        with self.assertRaises(AssertionError):
            launch.launch_account_instances([str(self.dir_a)])
        self.spawn.assert_not_called()


class TestLaunchModuleIsolation(unittest.TestCase):
    """The module stays free of platform-specific imports."""

    def test_no_direct_platform_imports(self):
        source = Path(launch.__file__).read_text(encoding='utf-8')
        for forbidden in ('ctypes.windll', 'winreg', 'msvcrt', 'fcntl', 'CREATE_NO_WINDOW'):
            self.assertNotIn(forbidden, source)


if __name__ == '__main__':
    unittest.main()
