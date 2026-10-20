"""
Codex CLI
==========

Reads Codex usage by delegating to the Codex CLI's ``app-server``.  The
app never reads Codex credentials and makes no network call for Codex
itself: the Codex CLI keeps its own login in its home directory and
contacts OpenAI on its own.  Everything handed back is converted into the
same quota-field shape the Claude usage response has, so the display
and alert code needs no Codex-specific branch.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .account import PROVIDER_CODEX, Account
from .formatting import field_name_for_window
from .i18n import T
from .platforms import no_window_kwargs
from .settings import MONITOR_CODEX

__all__ = ['CODEX_CLI_PATH', 'codex_home', 'detect_codex_account', 'fetch_profile', 'fetch_usage', 'fetch_usage_and_profile']

log = logging.getLogger(__name__)

CODEX_ACCOUNT_LABEL = 'codex'

_SESSION_TIMEOUT = 30.0
_EXIT_TIMEOUT = 5.0
_BASE_LIMIT_ID = 'codex'
_WINDOW_KEYS = ('primary', 'secondary')

_ID_INITIALIZE = 1
_ID_ACCOUNT = 2
_ID_RATE_LIMITS = 3


def _discover_cli_path() -> Path | None:
    """Discover the Codex CLI binary path, or None when it is not installed.

    Strategy
    --------
    1. ``shutil.which('codex')`` - respects PATH and PATHEXT.  A typical npm
       install resolves to a shim next to ``codex.cmd``.
    2. A ``.ps1`` shim is replaced by its sibling ``.cmd`` or ``.exe``,
       because ``subprocess`` cannot execute PowerShell scripts.
    3. Fall back to the standard npm location at ``%APPDATA%\\npm``.
    """
    found = shutil.which('codex')
    if found:
        path = Path(found)
        if path.suffix.lower() == '.ps1':
            for extension in ('.cmd', '.exe'):
                sibling = path.with_suffix(extension)
                if sibling.is_file():
                    return sibling
        return path

    appdata = os.environ.get('APPDATA')
    if appdata:
        for name in ('codex.cmd', 'codex.exe'):
            candidate = Path(appdata) / 'npm' / name
            if candidate.is_file():
                return candidate

    return None


# Resolved at import time. The CLI path doesn't move during runtime.
CODEX_CLI_PATH = _discover_cli_path()


def codex_home() -> Path:
    """Return the resolved Codex home: ``CODEX_HOME`` when set, else ``~/.codex``."""
    configured = os.environ.get('CODEX_HOME')
    if configured:
        return Path(configured).expanduser().resolve()

    return (Path.home() / '.codex').resolve()


def detect_codex_account(existing: list[Account]) -> Account | None:
    """Return the Codex account to monitor, or None.

    Parameters
    ----------
    existing : list[Account]
        Accounts already monitored; the Codex label gets a ``(2)``, ``(3)``
        ... suffix when it clashes with one of them.

    Returns
    -------
    Account or None
        An account for the Codex home when ``monitor_codex`` is on, the
        CLI is installed and the CLI is signed in.  An ``auth.json`` in the
        home counts as signed in without any process being started; the
        file is only checked for existence, never read.  Without the file
        the login may sit in the OS keyring, so one app-server session asks
        ``account/read``.  A missing home folder is never asked: the
        app-server exits with an error for it.
    """
    if not MONITOR_CODEX or CODEX_CLI_PATH is None:
        return None

    home = codex_home()
    if not (home / 'auth.json').is_file() and not (home.is_dir() and _is_signed_in(Account(home, CODEX_ACCOUNT_LABEL, PROVIDER_CODEX))):
        return None

    taken = {account.label for account in existing}
    label = CODEX_ACCOUNT_LABEL
    number = 1
    while label in taken:
        number += 1
        label = f'{CODEX_ACCOUNT_LABEL} ({number})'

    return Account(home, label, PROVIDER_CODEX)


def _is_signed_in(account: Account) -> bool:
    """Return whether the CLI reports a signed-in account; any failure counts as not signed in."""
    try:
        replies = _run_session(account, [(_ID_ACCOUNT, 'account/read', {'refreshToken': False})])
    except (_CodexUnavailableError, OSError, TimeoutError) as error:
        log.info('codex sign-in check failed: %s', error)
        return False

    result = replies[_ID_ACCOUNT].get('result')
    codex_account = result.get('account') if isinstance(result, dict) else None

    return isinstance(codex_account, dict) and bool(codex_account)


def fetch_usage(account: Account) -> dict[str, Any]:
    """Fetch *account*'s Codex limits as quota fields.

    Parameters
    ----------
    account : Account
        The Codex account; its ``config_dir`` is the Codex home.

    Returns
    -------
    dict
        Quota fields (``{'utilization': ..., 'resets_at': ...}`` per
        limit window), or ``{'error': message}`` - with ``server_message``
        when the CLI reported one.  Never carries ``auth_error`` or
        ``rate_limited``: those drive Claude-only recovery paths.
    """
    try:
        replies = _run_session(account, [(_ID_RATE_LIMITS, 'account/rateLimits/read', None)])
    except _CodexUnavailableError as error:
        return {'error': str(error)}
    except (OSError, TimeoutError) as error:
        log.info('codex app-server session failed: %s', error)
        return {'error': T['codex_no_response']}

    return _rate_limits_to_usage(replies[_ID_RATE_LIMITS])


def fetch_usage_and_profile(account: Account) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Fetch *account*'s limits and identity in one app-server session.

    Returns
    -------
    tuple[dict, dict or None]
        The usage as ``fetch_usage()`` returns it, and the profile as
        ``fetch_profile()`` returns it.  The profile is None when the
        identity could not be read, also when the usage was returned.
    """
    try:
        replies = _run_session(account, [
            (_ID_ACCOUNT, 'account/read', {'refreshToken': False}),
            (_ID_RATE_LIMITS, 'account/rateLimits/read', None),
        ])
    except _CodexUnavailableError as error:
        return {'error': str(error)}, None
    except (OSError, TimeoutError) as error:
        log.info('codex app-server session failed: %s', error)
        return {'error': T['codex_no_response']}, None

    return _rate_limits_to_usage(replies[_ID_RATE_LIMITS]), _replies_to_profile(replies)


def fetch_profile(account: Account) -> dict[str, Any] | None:
    """Fetch *account*'s identity in the shape the Claude profile has.

    Returns
    -------
    dict or None
        ``{'account': {'email', 'uuid'}, 'organization': {'organization_type'}}``
        with the Codex email, account id and plan type, or None when the
        CLI is missing, not signed in, or any part of the lookup failed.
    """
    try:
        replies = _run_session(account, [
            (_ID_ACCOUNT, 'account/read', {'refreshToken': False}),
            (_ID_RATE_LIMITS, 'account/rateLimits/read', None),
        ])
    except (_CodexUnavailableError, OSError, TimeoutError) as error:
        log.info('codex profile lookup failed: %s', error)
        return None

    return _replies_to_profile(replies)


def _replies_to_profile(replies: dict[int, dict[str, Any]]) -> dict[str, Any] | None:
    """Build the profile from the replies to ``account/read`` and ``account/rateLimits/read``, or None."""
    account_result = replies[_ID_ACCOUNT].get('result')
    limits_result = replies[_ID_RATE_LIMITS].get('result')
    if not isinstance(account_result, dict) or not isinstance(limits_result, dict):
        return None

    codex_account = account_result.get('account')
    if not isinstance(codex_account, dict) or not codex_account:
        return None

    limits = limits_result.get('rateLimits')
    plan_type = codex_account.get('planType') or (limits.get('planType') if isinstance(limits, dict) else None)

    return {
        'account': {'email': codex_account.get('email'), 'uuid': limits_result.get('accountId')},
        'organization': {'organization_type': plan_type},
    }


class _CodexUnavailableError(Exception):
    """The Codex CLI is not installed; the message is the user-facing text."""


def _run_session(account: Account, requests: list[tuple[int, str, dict[str, Any] | None]]) -> dict[int, dict[str, Any]]:
    """Run one ``codex app-server`` session and collect the replies to *requests*.

    The server exits on end of input before it answers pending requests,
    so stdin stays open until every reply has arrived.

    Parameters
    ----------
    account : Account
        Account whose Codex home is handed to the server as ``CODEX_HOME``.
    requests : list[tuple[int, str, dict | None]]
        Request id, method and params of each request.

    Returns
    -------
    dict[int, dict]
        The JSON-RPC reply for every request id.

    Raises
    ------
    _CodexUnavailableError
        The CLI is not installed.
    OSError
        The server could not start, or exited before answering.
    TimeoutError
        Not every reply arrived in time.
    """
    if CODEX_CLI_PATH is None:
        raise _CodexUnavailableError(T['codex_not_found'])

    messages: list[dict[str, Any]] = [
        {'id': _ID_INITIALIZE, 'method': 'initialize', 'params': {
            'clientInfo': {'name': 'usage-monitor-for-claude', 'title': 'Usage Monitor for Claude', 'version': __version__},
        }},
        {'method': 'initialized'},
    ]
    for request_id, method, params in requests:
        message: dict[str, Any] = {'id': request_id, 'method': method}
        if params is not None:
            message['params'] = params
        messages.append(message)

    environment = {**os.environ, 'CODEX_HOME': str(account.config_dir)}
    process = subprocess.Popen(
        [str(CODEX_CLI_PATH), 'app-server'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding='utf-8', errors='replace', env=environment, **no_window_kwargs(),
    )
    try:
        assert process.stdin is not None and process.stdout is not None
        lines: queue.Queue[str | None] = queue.Queue()
        threading.Thread(target=_pump_lines, args=(process.stdout, lines), daemon=True).start()

        for message in messages:
            process.stdin.write(json.dumps(message) + '\n')
        process.stdin.flush()

        return _collect_replies(lines, {request_id for request_id, _, _ in requests})
    except BrokenPipeError as error:
        raise OSError('codex app-server closed its input') from error
    finally:
        _stop_process(process)


def _pump_lines(stream: Any, lines: queue.Queue[str | None]) -> None:
    """Move every line of *stream* into *lines*, then a ``None`` marker at end of stream."""
    try:
        for line in stream:
            lines.put(line)
    except (OSError, ValueError):
        pass
    finally:
        lines.put(None)


def _collect_replies(lines: queue.Queue[str | None], expected_ids: set[int]) -> dict[int, dict[str, Any]]:
    """Read server output until every id in *expected_ids* has a reply.

    Notifications (no ``id``), replies to other ids and lines that are not
    JSON objects are skipped.
    """
    deadline = time.monotonic() + _SESSION_TIMEOUT
    replies: dict[int, dict[str, Any]] = {}

    while len(replies) < len(expected_ids):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('codex app-server did not answer in time')

        try:
            line = lines.get(timeout=remaining)
        except queue.Empty:
            raise TimeoutError('codex app-server did not answer in time') from None

        if line is None:
            raise OSError('codex app-server exited before answering')

        try:
            reply = json.loads(line)
        except ValueError:
            continue

        if not isinstance(reply, dict):
            continue

        reply_id = reply.get('id')
        if isinstance(reply_id, int) and reply_id in expected_ids:
            replies[reply_id] = reply

    return replies


def _stop_process(process: subprocess.Popen[str]) -> None:
    """Close the server's input and make sure it ends, killing it when it does not exit."""
    try:
        if process.stdin is not None:
            process.stdin.close()
    except OSError:
        pass

    try:
        process.wait(timeout=_EXIT_TIMEOUT)
        return
    except subprocess.TimeoutExpired:
        pass

    process.terminate()
    try:
        process.wait(timeout=_EXIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _rate_limits_to_usage(reply: dict[str, Any]) -> dict[str, Any]:
    """Turn the reply to ``account/rateLimits/read`` into quota fields or an error dict."""
    error = reply.get('error')
    if error is not None:
        message = error.get('message') if isinstance(error, dict) else None
        message = message if isinstance(message, str) else ''
        if 'authenticat' in message.lower():
            return {'error': T['codex_auth_required']}

        failure: dict[str, Any] = {'error': T['codex_no_response']}
        if message:
            failure['server_message'] = message
        return failure

    usage = _normalize_rate_limits(reply.get('result'))
    if not usage:
        return {'error': T['codex_no_response']}

    return usage


def _normalize_rate_limits(result: Any) -> dict[str, Any]:
    """Convert an ``account/rateLimits/read`` result into quota fields.

    Each window becomes one field whose name is derived from its length
    (see ``field_name_for_window``).  A limit id other than ``codex`` adds
    a slug of its name, giving a variant like ``seven_day_gpt_5_codex``.
    A field that is already set is never overwritten.

    Returns
    -------
    dict
        Quota fields; empty when no window could be named.
    """
    if not isinstance(result, dict):
        return {}

    limits_by_id = result.get('rateLimitsByLimitId')
    if not isinstance(limits_by_id, dict) or not limits_by_id:
        limits_by_id = {_BASE_LIMIT_ID: result.get('rateLimits')}

    usage: dict[str, Any] = {}
    for limit_id, limit in limits_by_id.items():
        if not isinstance(limit, dict):
            continue

        suffix = ''
        if limit_id != _BASE_LIMIT_ID:
            slug = _slug(limit.get('limitName') if isinstance(limit.get('limitName'), str) and limit.get('limitName') else limit_id)
            suffix = f'_{slug}' if slug else ''

        for window_key in _WINDOW_KEYS:
            window = limit.get(window_key)
            if not isinstance(window, dict):
                continue

            entry = _window_entry(window)
            if entry is None:
                continue

            field_name, quota = entry
            field_name += suffix
            if field_name in usage:
                continue

            usage[field_name] = quota

    return usage


def _window_entry(window: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """Return ``(field name, quota)`` for one rate-limit window, or None when it cannot be used."""
    minutes = window.get('windowDurationMins')
    if isinstance(minutes, float) and minutes.is_integer():
        minutes = int(minutes)
    if not isinstance(minutes, int) or isinstance(minutes, bool):
        return None

    base_name = field_name_for_window(minutes)
    if base_name is None:
        return None

    used = window.get('usedPercent')
    if not isinstance(used, (int, float)) or isinstance(used, bool):
        return None

    return base_name, {'utilization': float(used), 'resets_at': _iso_from_epoch(window.get('resetsAt'))}


def _iso_from_epoch(value: Any) -> str | None:
    """Convert epoch seconds to an ISO 8601 UTC string, or None when *value* is not a usable timestamp."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None

    try:
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _slug(text: str) -> str:
    """Convert a limit name into a field-name suffix (e.g. ``'GPT-5-Codex'`` -> ``'gpt_5_codex'``)."""
    cleaned = ''.join(char if char.isalnum() else ' ' for char in text.lower())
    return '_'.join(cleaned.split())
