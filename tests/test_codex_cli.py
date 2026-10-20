"""
Codex CLI Tests
================

Unit tests for Codex discovery, account detection, the app-server session,
and the conversion of rate limits into quota fields.  No test starts a real
``codex`` process.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from usage_monitor_for_claude import codex_cli
from usage_monitor_for_claude.account import PROVIDER_CLAUDE, PROVIDER_CODEX, Account
from usage_monitor_for_claude.codex_cli import (
    _discover_cli_path, _normalize_rate_limits, codex_home, detect_codex_account, fetch_profile, fetch_usage,
    fetch_usage_and_profile,
)
from usage_monitor_for_claude.i18n import LOCALE_DIR

EN = json.loads((LOCALE_DIR / 'en.json').read_text(encoding='utf-8'))

_ACCOUNT = Account(Path('/codex/home'), 'codex', PROVIDER_CODEX)
_CLI = Path('/bin/codex')

_WEEKLY = {'usedPercent': 12, 'windowDurationMins': 10080, 'resetsAt': 1793083730}
_SESSION = {'usedPercent': 40.5, 'windowDurationMins': 300, 'resetsAt': 1793000000}


def _limits_result(primary=None, secondary=None, **extra) -> dict:
    """Build an ``account/rateLimits/read`` result with one ``codex`` limit."""
    limit = {'limitId': 'codex', 'limitName': None, 'primary': primary, 'secondary': secondary, **extra}
    return {'rateLimits': limit, 'rateLimitsByLimitId': {'codex': limit}, 'accountId': 'acc-1'}


def _reply(request_id: int, result=None, error=None) -> str:
    message = {'id': request_id}
    if error is not None:
        message['error'] = error
    else:
        message['result'] = result
    return json.dumps(message) + '\n'


class _FakeProcess:
    """Stands in for ``subprocess.Popen``: stdout yields *lines*, then ends (unless *block* is set)."""

    def __init__(self, lines: list[str], block: threading.Event | None = None) -> None:
        self.written: list[str] = []
        self.stdin = MagicMock()
        self.stdin.write.side_effect = self.written.append
        self._block = block
        self.stdout = self._stream(lines)
        self.waited = False
        self.terminated = False
        self.killed = False

    def _stream(self, lines: list[str]):
        yield from lines
        if self._block is not None:
            self._block.wait(5)

    def wait(self, timeout=None):
        self.waited = True
        return 0

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def _run(lines: list[str], func=fetch_usage, block: threading.Event | None = None):
    """Run *func* against a fake app-server that prints *lines*; return (result, process, popen mock)."""
    process = _FakeProcess(lines, block)
    with patch.object(codex_cli, 'CODEX_CLI_PATH', _CLI), patch.object(codex_cli.subprocess, 'Popen', return_value=process) as popen:
        result = func(_ACCOUNT)
    return result, process, popen


class TestDiscoverCliPath(unittest.TestCase):

    @patch('usage_monitor_for_claude.codex_cli.shutil.which', return_value='/usr/bin/codex')
    def test_uses_which_when_found(self, _which):
        self.assertEqual(_discover_cli_path(), Path('/usr/bin/codex'))

    @patch('usage_monitor_for_claude.codex_cli.shutil.which')
    def test_substitutes_cmd_for_ps1(self, which):
        with TemporaryDirectory() as tmp:
            (Path(tmp) / 'codex.cmd').write_text('')
            which.return_value = str(Path(tmp) / 'codex.ps1')
            self.assertEqual(_discover_cli_path(), Path(tmp) / 'codex.cmd')

    @patch('usage_monitor_for_claude.codex_cli.shutil.which')
    def test_substitutes_exe_for_ps1_when_no_cmd(self, which):
        with TemporaryDirectory() as tmp:
            (Path(tmp) / 'codex.exe').write_text('')
            which.return_value = str(Path(tmp) / 'codex.ps1')
            self.assertEqual(_discover_cli_path(), Path(tmp) / 'codex.exe')

    @patch('usage_monitor_for_claude.codex_cli.shutil.which', return_value=None)
    def test_falls_back_to_appdata_npm(self, _which):
        with TemporaryDirectory() as tmp:
            npm = Path(tmp) / 'npm'
            npm.mkdir()
            (npm / 'codex.cmd').write_text('')
            with patch.dict(os.environ, {'APPDATA': tmp}):
                self.assertEqual(_discover_cli_path(), npm / 'codex.cmd')

    @patch('usage_monitor_for_claude.codex_cli.shutil.which', return_value=None)
    def test_none_when_nothing_found(self, _which):
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {'APPDATA': tmp}):
            self.assertIsNone(_discover_cli_path())


class TestCodexHome(unittest.TestCase):

    def test_uses_codex_home_variable(self):
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {'CODEX_HOME': tmp}):
            self.assertEqual(codex_home(), Path(tmp).resolve())

    def test_defaults_to_dot_codex_in_home(self):
        with TemporaryDirectory() as tmp, patch.dict(os.environ, clear=False), patch.object(Path, 'home', return_value=Path(tmp)):
            os.environ.pop('CODEX_HOME', None)
            self.assertEqual(codex_home(), (Path(tmp) / '.codex').resolve())

    def test_empty_variable_is_ignored(self):
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {'CODEX_HOME': ''}), patch.object(Path, 'home', return_value=Path(tmp)):
            self.assertEqual(codex_home(), (Path(tmp) / '.codex').resolve())


class TestDetectCodexAccount(unittest.TestCase):

    def _detect(self, existing=None, *, setting=True, cli=_CLI, auth_file=True):
        with TemporaryDirectory() as tmp:
            if auth_file:
                (Path(tmp) / 'auth.json').write_text('{}')
            with patch.object(codex_cli, 'MONITOR_CODEX', setting), patch.object(codex_cli, 'CODEX_CLI_PATH', cli), \
                 patch.dict(os.environ, {'CODEX_HOME': tmp}):
                return detect_codex_account(existing or []), Path(tmp).resolve()

    def test_detected_when_signed_in(self):
        account, home = self._detect()
        self.assertEqual(account, Account(home, 'codex', PROVIDER_CODEX))

    def test_setting_off(self):
        self.assertIsNone(self._detect(setting=False)[0])

    def test_cli_missing(self):
        self.assertIsNone(self._detect(cli=None)[0])

    def test_auth_file_present_starts_no_session(self):
        with patch.object(codex_cli, '_run_session') as session:
            self._detect()
        session.assert_not_called()

    def test_auth_file_missing_and_signed_in_reply_is_detected(self):
        reply = {'result': {'account': {'type': 'chatgpt', 'email': 'dev@example.com'}, 'requiresOpenaiAuth': True}}
        with patch.object(codex_cli, '_run_session', return_value={2: reply}) as session:
            account, home = self._detect(auth_file=False)
        self.assertEqual(account, Account(home, 'codex', PROVIDER_CODEX))
        session.assert_called_once()
        self.assertEqual(session.call_args[0][1], [(2, 'account/read', {'refreshToken': False})])

    def test_auth_file_missing_and_null_account_is_not_detected(self):
        for result in ({'account': None, 'requiresOpenaiAuth': True}, {'account': None, 'requiresOpenaiAuth': False}, {'account': {}}, {}, None, []):
            with patch.object(codex_cli, '_run_session', return_value={2: {'result': result}}):
                self.assertIsNone(self._detect(auth_file=False)[0], repr(result))

    def test_auth_file_missing_and_error_reply_is_not_detected(self):
        with patch.object(codex_cli, '_run_session', return_value={2: {'error': {'message': 'boom'}}}):
            self.assertIsNone(self._detect(auth_file=False)[0])

    def test_auth_file_missing_and_session_failure_is_not_detected(self):
        for error in (OSError('exited'), TimeoutError('slow'), codex_cli._CodexUnavailableError('missing')):
            with patch.object(codex_cli, '_run_session', side_effect=error):
                self.assertIsNone(self._detect(auth_file=False)[0], repr(error))

    def test_missing_home_is_not_detected_and_starts_no_session(self):
        with TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / 'nope')
            with patch.object(codex_cli, 'MONITOR_CODEX', True), patch.object(codex_cli, 'CODEX_CLI_PATH', _CLI), \
                 patch.dict(os.environ, {'CODEX_HOME': missing}), patch.object(codex_cli, '_run_session') as session:
                self.assertIsNone(detect_codex_account([]))
        session.assert_not_called()

    def test_keyring_check_does_not_read_auth_file(self):
        with TemporaryDirectory() as tmp:
            with patch.object(codex_cli, 'MONITOR_CODEX', True), patch.object(codex_cli, 'CODEX_CLI_PATH', _CLI), \
                 patch.dict(os.environ, {'CODEX_HOME': tmp}), patch.object(codex_cli, '_run_session', return_value={2: {'result': {'account': {'a': 1}}}}), \
                 patch.object(Path, 'read_text') as read_text, patch('builtins.open') as opened:
                self.assertIsNotNone(detect_codex_account([]))
        read_text.assert_not_called()
        opened.assert_not_called()

    def test_label_clash_gets_suffix(self):
        existing = [Account(Path('/a'), 'codex')]
        self.assertEqual(self._detect(existing)[0].label, 'codex (2)')

    def test_repeated_label_clash_counts_on(self):
        existing = [Account(Path('/a'), 'codex'), Account(Path('/b'), 'codex (2)')]
        self.assertEqual(self._detect(existing)[0].label, 'codex (3)')

    def test_does_not_read_auth_file(self):
        with TemporaryDirectory() as tmp:
            (Path(tmp) / 'auth.json').write_text('{}')
            with patch.object(codex_cli, 'MONITOR_CODEX', True), patch.object(codex_cli, 'CODEX_CLI_PATH', _CLI), \
                 patch.dict(os.environ, {'CODEX_HOME': tmp}), patch.object(Path, 'read_text') as read_text, \
                 patch('builtins.open') as opened:
                detect_codex_account([])
        read_text.assert_not_called()
        opened.assert_not_called()


class TestSession(unittest.TestCase):

    def test_handshake_and_request_are_sent(self):
        _result, process, popen = _run([_reply(3, _limits_result(_WEEKLY))])
        sent = [json.loads(line) for line in process.written]
        self.assertEqual(sent[0]['method'], 'initialize')
        self.assertEqual(sent[0]['id'], 1)
        self.assertEqual(sent[0]['params']['clientInfo']['name'], 'usage-monitor-for-claude')
        self.assertEqual(sent[1], {'method': 'initialized'})
        self.assertEqual(sent[2], {'id': 3, 'method': 'account/rateLimits/read'})
        self.assertTrue(all(line.endswith('\n') for line in process.written))

    def test_popen_arguments(self):
        _result, _process, popen = _run([_reply(3, _limits_result(_WEEKLY))])
        args, kwargs = popen.call_args
        self.assertEqual(args[0], [str(_CLI), 'app-server'])
        self.assertEqual(kwargs['encoding'], 'utf-8')
        self.assertEqual(kwargs['errors'], 'replace')
        self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)
        self.assertEqual(kwargs['env']['CODEX_HOME'], str(_ACCOUNT.config_dir))

    def test_environment_is_a_copy(self):
        with patch.dict(os.environ, {'CODEX_HOME': '/other'}):
            _run([_reply(3, _limits_result(_WEEKLY))])
            self.assertEqual(os.environ['CODEX_HOME'], '/other')

    def test_notifications_and_other_ids_skipped(self):
        lines = [
            json.dumps({'method': 'thread/started', 'params': {}}) + '\n',
            _reply(1, {'userAgent': 'x'}),
            _reply(99, {}),
            _reply(3, _limits_result(_WEEKLY)),
        ]
        result, _process, _popen = _run(lines)
        self.assertEqual(list(result), ['seven_day'])

    def test_bad_json_lines_ignored(self):
        lines = ['not json\n', '\n', '[1, 2]\n', '"text"\n', _reply(3, _limits_result(_WEEKLY))]
        result, _process, _popen = _run(lines)
        self.assertEqual(list(result), ['seven_day'])

    def test_stdin_stays_open_until_reply_then_closes(self):
        _result, process, _popen = _run([_reply(3, _limits_result(_WEEKLY))])
        process.stdin.close.assert_called_once()
        self.assertTrue(process.waited)

    def test_process_kept_alive_while_waiting(self):
        closed_before_reply = []
        process = _FakeProcess([])

        def stream():
            closed_before_reply.append(process.stdin.close.called)
            yield _reply(3, _limits_result(_WEEKLY))

        process.stdout = stream()
        with patch.object(codex_cli, 'CODEX_CLI_PATH', _CLI), patch.object(codex_cli.subprocess, 'Popen', return_value=process):
            fetch_usage(_ACCOUNT)
        self.assertEqual(closed_before_reply, [False])

    def test_process_exits_early_is_error(self):
        result, process, _popen = _run([])
        self.assertEqual(result, {'error': EN['codex_no_response']})
        process.stdin.close.assert_called_once()

    def test_timeout_is_error(self):
        release = threading.Event()
        try:
            with patch.object(codex_cli, '_SESSION_TIMEOUT', 0.2):
                result, process, _popen = _run([], block=release)
        finally:
            release.set()
        self.assertEqual(result, {'error': EN['codex_no_response']})
        process.stdin.close.assert_called_once()

    def test_process_that_does_not_exit_is_terminated_then_killed(self):
        process = _FakeProcess([_reply(3, _limits_result(_WEEKLY))])
        process.wait = MagicMock(side_effect=[subprocess.TimeoutExpired('codex', 1), subprocess.TimeoutExpired('codex', 1), 0])
        with patch.object(codex_cli, 'CODEX_CLI_PATH', _CLI), patch.object(codex_cli.subprocess, 'Popen', return_value=process):
            fetch_usage(_ACCOUNT)
        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)

    def test_broken_pipe_is_error(self):
        process = _FakeProcess([])
        process.stdin.write.side_effect = BrokenPipeError()
        with patch.object(codex_cli, 'CODEX_CLI_PATH', _CLI), patch.object(codex_cli.subprocess, 'Popen', return_value=process):
            self.assertEqual(fetch_usage(_ACCOUNT), {'error': EN['codex_no_response']})

    def test_start_failure_is_error(self):
        with patch.object(codex_cli, 'CODEX_CLI_PATH', _CLI), patch.object(codex_cli.subprocess, 'Popen', side_effect=OSError('nope')):
            self.assertEqual(fetch_usage(_ACCOUNT), {'error': EN['codex_no_response']})

    def test_cli_not_found(self):
        with patch.object(codex_cli, 'CODEX_CLI_PATH', None), patch.object(codex_cli.subprocess, 'Popen') as popen:
            self.assertEqual(fetch_usage(_ACCOUNT), {'error': EN['codex_not_found']})
        popen.assert_not_called()


class TestFetchUsageErrors(unittest.TestCase):

    def test_auth_error(self):
        error = {'code': -32600, 'message': 'codex account authentication required to read rate limits'}
        result, _process, _popen = _run([_reply(3, error=error)])
        self.assertEqual(result, {'error': EN['codex_auth_required']})

    def test_other_error_carries_server_message(self):
        result, _process, _popen = _run([_reply(3, error={'code': -1, 'message': 'backend exploded'})])
        self.assertEqual(result, {'error': EN['codex_no_response'], 'server_message': 'backend exploded'})

    def test_error_without_message(self):
        result, _process, _popen = _run([_reply(3, error={'code': -1})])
        self.assertEqual(result, {'error': EN['codex_no_response']})

    def test_error_not_a_dict(self):
        result, _process, _popen = _run([_reply(3, error='boom')])
        self.assertEqual(result, {'error': EN['codex_no_response']})

    def test_never_sets_claude_only_flags(self):
        for lines in ([_reply(3, error={'message': 'authentication required'})], [_reply(3, error={'message': 'x'})], []):
            result, _process, _popen = _run(lines)
            self.assertNotIn('auth_error', result)
            self.assertNotIn('rate_limited', result)

    def test_window_that_is_not_a_whole_hour_is_named_in_minutes(self):
        result, _process, _popen = _run([_reply(3, _limits_result({'usedPercent': 1, 'windowDurationMins': 7, 'resetsAt': None}))])
        self.assertEqual(result, {'seven_minute': {'utilization': 1.0, 'resets_at': None}})

    def test_unusable_window_length_is_error(self):
        result, _process, _popen = _run([_reply(3, _limits_result({'usedPercent': 1, 'windowDurationMins': 0}))])
        self.assertEqual(result, {'error': EN['codex_no_response']})

    def test_missing_result_is_error(self):
        result, _process, _popen = _run([json.dumps({'id': 3}) + '\n'])
        self.assertEqual(result, {'error': EN['codex_no_response']})


class TestNormalizeRateLimits(unittest.TestCase):

    def test_five_hour_and_weekly(self):
        usage = _normalize_rate_limits(_limits_result(_SESSION, _WEEKLY))
        self.assertEqual(set(usage), {'five_hour', 'seven_day'})
        self.assertEqual(usage['five_hour']['utilization'], 40.5)
        self.assertEqual(usage['seven_day'], {'utilization': 12.0, 'resets_at': '2026-10-27T06:48:50+00:00'})

    def test_utilization_is_float(self):
        self.assertIsInstance(_normalize_rate_limits(_limits_result(_WEEKLY))['seven_day']['utilization'], float)

    def test_weekly_only(self):
        self.assertEqual(list(_normalize_rate_limits(_limits_result(_WEEKLY))), ['seven_day'])

    def test_field_name_comes_from_window_length(self):
        usage = _normalize_rate_limits(_limits_result({'usedPercent': 5, 'windowDurationMins': 1440}, {'usedPercent': 6, 'windowDurationMins': 180}))
        self.assertEqual(set(usage), {'one_day', 'three_hour'})

    def test_extra_limit_id_becomes_variant(self):
        result = _limits_result(_WEEKLY)
        result['rateLimitsByLimitId']['gpt_5_codex'] = {'limitId': 'gpt_5_codex', 'limitName': None, 'primary': _WEEKLY, 'secondary': None}
        usage = _normalize_rate_limits(result)
        self.assertEqual(set(usage), {'seven_day', 'seven_day_gpt_5_codex'})

    def test_limit_name_preferred_for_slug(self):
        result = {'rateLimitsByLimitId': {'x1': {'limitName': 'GPT-5 Codex Spark!', 'primary': _WEEKLY}}}
        self.assertEqual(list(_normalize_rate_limits(result)), ['seven_day_gpt_5_codex_spark'])

    def test_slug_falls_back_to_limit_id(self):
        result = {'rateLimitsByLimitId': {'Other-Limit': {'limitName': None, 'primary': _WEEKLY}}}
        self.assertEqual(list(_normalize_rate_limits(result)), ['seven_day_other_limit'])

    def test_falls_back_to_rate_limits_without_by_id(self):
        result = {'rateLimits': {'primary': _WEEKLY}}
        self.assertEqual(list(_normalize_rate_limits(result)), ['seven_day'])

    def test_empty_by_id_falls_back_to_rate_limits(self):
        result = {'rateLimits': {'primary': _WEEKLY}, 'rateLimitsByLimitId': {}}
        self.assertEqual(list(_normalize_rate_limits(result)), ['seven_day'])

    def test_null_window_skipped(self):
        self.assertEqual(list(_normalize_rate_limits(_limits_result(None, _WEEKLY))), ['seven_day'])

    def test_window_without_whole_hours_gets_a_minute_name(self):
        usage = _normalize_rate_limits(_limits_result({'usedPercent': 1, 'windowDurationMins': 45}, _WEEKLY))
        self.assertEqual(list(usage), ['45_minute', 'seven_day'])

    def test_non_positive_window_skipped(self):
        usage = _normalize_rate_limits(_limits_result({'usedPercent': 1, 'windowDurationMins': 0}, _WEEKLY))
        self.assertEqual(list(usage), ['seven_day'])

    def test_documented_example_with_fifteen_and_sixty_minute_windows(self):
        result = {
            'rateLimits': {'limitId': 'codex', 'primary': {'usedPercent': 25, 'windowDurationMins': 15, 'resetsAt': 1730947200}},
            'rateLimitsByLimitId': {
                'codex': {'limitId': 'codex', 'limitName': None, 'primary': {'usedPercent': 25, 'windowDurationMins': 15, 'resetsAt': 1730947200}},
                'codex_other': {
                    'limitId': 'codex_other', 'limitName': None, 'primary': {'usedPercent': 42, 'windowDurationMins': 60, 'resetsAt': 1730950800},
                },
            },
        }
        usage = _normalize_rate_limits(result)
        self.assertEqual(list(usage), ['15_minute', 'one_hour_codex_other'])
        self.assertEqual(usage['15_minute']['utilization'], 25.0)
        self.assertEqual(usage['one_hour_codex_other']['utilization'], 42.0)

    def test_non_dict_junk_ignored(self):
        for junk in (None, [], 'text', 5, {'rateLimitsByLimitId': 'x'}, {'rateLimitsByLimitId': {'codex': 'x'}}, {'rateLimits': ['a']}):
            self.assertEqual(_normalize_rate_limits(junk), {}, repr(junk))

    def test_junk_window_values_ignored(self):
        windows = [
            'x', {'usedPercent': 1}, {'windowDurationMins': 'abc', 'usedPercent': 1}, {'windowDurationMins': True, 'usedPercent': 1},
            {'windowDurationMins': 300}, {'windowDurationMins': 300, 'usedPercent': 'high'},
        ]
        for window in windows:
            self.assertEqual(_normalize_rate_limits(_limits_result(window)), {}, repr(window))

    def test_float_window_length_accepted(self):
        usage = _normalize_rate_limits(_limits_result({'usedPercent': 1, 'windowDurationMins': 300.0}))
        self.assertEqual(list(usage), ['five_hour'])

    def test_null_resets_at(self):
        usage = _normalize_rate_limits(_limits_result({'usedPercent': 0, 'windowDurationMins': 300, 'resetsAt': None}))
        self.assertEqual(usage['five_hour'], {'utilization': 0.0, 'resets_at': None})

    def test_missing_or_bad_resets_at(self):
        for resets_at in ('soon', True, 10 ** 30):
            usage = _normalize_rate_limits(_limits_result({'usedPercent': 0, 'windowDurationMins': 300, 'resetsAt': resets_at}))
            self.assertIsNone(usage['five_hour']['resets_at'], repr(resets_at))

    def test_does_not_overwrite_existing_field(self):
        usage = _normalize_rate_limits(_limits_result({'usedPercent': 1, 'windowDurationMins': 300}, {'usedPercent': 99, 'windowDurationMins': 300}))
        self.assertEqual(usage['five_hour']['utilization'], 1.0)

    def test_resets_at_is_utc_iso(self):
        usage = _normalize_rate_limits(_limits_result({'usedPercent': 0, 'windowDurationMins': 300, 'resetsAt': 0}))
        self.assertEqual(usage['five_hour']['resets_at'], '1970-01-01T00:00:00+00:00')


class TestFetchUsageAndProfile(unittest.TestCase):

    _ACCOUNT_REPLY = {'account': {'type': 'chatgpt', 'email': 'dev@example.com', 'planType': 'plus'}}

    def test_one_session_carries_both_requests(self):
        (usage, profile), process, popen = _run([_reply(2, self._ACCOUNT_REPLY), _reply(3, _limits_result(_WEEKLY))], fetch_usage_and_profile)
        popen.assert_called_once()
        sent = [json.loads(line) for line in process.written]
        self.assertEqual(sent[2], {'id': 2, 'method': 'account/read', 'params': {'refreshToken': False}})
        self.assertEqual(sent[3], {'id': 3, 'method': 'account/rateLimits/read'})
        self.assertEqual(usage, {'seven_day': {'utilization': 12.0, 'resets_at': '2026-10-27T06:48:50+00:00'}})
        self.assertEqual(profile, {'account': {'email': 'dev@example.com', 'uuid': 'acc-1'}, 'organization': {'organization_type': 'plus'}})

    def test_signed_out_account_gives_no_profile_but_keeps_usage(self):
        (usage, profile), _process, _popen = _run([_reply(2, {'account': None}), _reply(3, _limits_result(_WEEKLY))], fetch_usage_and_profile)
        self.assertIn('seven_day', usage)
        self.assertIsNone(profile)

    def test_account_error_gives_no_profile_but_keeps_usage(self):
        (usage, profile), _process, _popen = _run([_reply(2, error={'message': 'no'}), _reply(3, _limits_result(_WEEKLY))], fetch_usage_and_profile)
        self.assertIn('seven_day', usage)
        self.assertIsNone(profile)

    def test_usage_error_gives_error_and_no_profile(self):
        error = {'message': 'authentication required'}
        (usage, profile), _process, _popen = _run([_reply(2, self._ACCOUNT_REPLY), _reply(3, error=error)], fetch_usage_and_profile)
        self.assertEqual(usage, {'error': EN['codex_auth_required']})
        self.assertIsNone(profile)

    def test_session_failure_gives_error_and_no_profile(self):
        (usage, profile), _process, _popen = _run([], fetch_usage_and_profile)
        self.assertEqual(usage, {'error': EN['codex_no_response']})
        self.assertIsNone(profile)

    def test_cli_missing_gives_error_and_no_profile(self):
        with patch.object(codex_cli, 'CODEX_CLI_PATH', None):
            usage, profile = fetch_usage_and_profile(_ACCOUNT)
        self.assertEqual(usage, {'error': EN['codex_not_found']})
        self.assertIsNone(profile)

    def test_never_sets_claude_only_flags(self):
        (usage, _profile), _process, _popen = _run([], fetch_usage_and_profile)
        self.assertNotIn('auth_error', usage)
        self.assertNotIn('rate_limited', usage)


class TestFetchProfile(unittest.TestCase):

    _ACCOUNT_REPLY = {'account': {'type': 'chatgpt', 'email': 'dev@example.com', 'planType': 'plus'}}

    def test_profile_shape(self):
        profile, process, _popen = _run([_reply(2, self._ACCOUNT_REPLY), _reply(3, _limits_result(_WEEKLY))], fetch_profile)
        self.assertEqual(profile, {'account': {'email': 'dev@example.com', 'uuid': 'acc-1'}, 'organization': {'organization_type': 'plus'}})
        sent = [json.loads(line) for line in process.written]
        self.assertEqual(sent[2], {'id': 2, 'method': 'account/read', 'params': {'refreshToken': False}})
        self.assertEqual(sent[3], {'id': 3, 'method': 'account/rateLimits/read'})

    def test_plan_falls_back_to_rate_limits_plan(self):
        account_reply = {'account': {'email': 'dev@example.com'}}
        limits = _limits_result(_WEEKLY)
        limits['rateLimits']['planType'] = 'pro'
        profile, _process, _popen = _run([_reply(2, account_reply), _reply(3, limits)], fetch_profile)
        self.assertEqual(profile['organization']['organization_type'], 'pro')

    def test_replies_in_any_order(self):
        profile, _process, _popen = _run([_reply(3, _limits_result(_WEEKLY)), _reply(2, self._ACCOUNT_REPLY)], fetch_profile)
        self.assertEqual(profile['account']['email'], 'dev@example.com')

    def test_not_signed_in_is_none(self):
        for account_reply in ({'account': {}}, {}, {'account': None}, {'account': 'x'}):
            profile, _process, _popen = _run([_reply(2, account_reply), _reply(3, _limits_result(_WEEKLY))], fetch_profile)
            self.assertIsNone(profile, repr(account_reply))

    def test_rate_limit_error_is_none(self):
        profile, _process, _popen = _run([_reply(2, self._ACCOUNT_REPLY), _reply(3, error={'message': 'authentication required'})], fetch_profile)
        self.assertIsNone(profile)

    def test_account_error_is_none(self):
        profile, _process, _popen = _run([_reply(2, error={'message': 'no'}), _reply(3, _limits_result(_WEEKLY))], fetch_profile)
        self.assertIsNone(profile)

    def test_session_failure_is_none(self):
        profile, _process, _popen = _run([], fetch_profile)
        self.assertIsNone(profile)

    def test_cli_missing_is_none(self):
        with patch.object(codex_cli, 'CODEX_CLI_PATH', None):
            self.assertIsNone(fetch_profile(_ACCOUNT))


class TestProviderConstants(unittest.TestCase):

    def test_values(self):
        self.assertEqual(PROVIDER_CLAUDE, 'claude')
        self.assertEqual(PROVIDER_CODEX, 'codex')

    def test_account_defaults_to_claude(self):
        self.assertEqual(Account(Path('/x'), 'x').provider, PROVIDER_CLAUDE)


if __name__ == '__main__':
    unittest.main()
