"""
Process Launching Tests
========================

Unit tests for restarting the app.
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from usage_monitor_for_claude import launch


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


class TestLaunchModuleIsolation(unittest.TestCase):
    """The module stays free of platform-specific imports."""

    def test_no_direct_platform_imports(self):
        source = Path(launch.__file__).read_text(encoding='utf-8')
        for forbidden in ('ctypes.windll', 'winreg', 'msvcrt', 'fcntl', 'CREATE_NO_WINDOW'):
            self.assertNotIn(forbidden, source)


if __name__ == '__main__':
    unittest.main()
