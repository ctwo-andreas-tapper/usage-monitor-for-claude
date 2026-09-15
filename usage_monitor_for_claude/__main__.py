"""Entry point for ``python -m usage_monitor_for_claude``."""
from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path

from usage_monitor_for_claude.account import accounts_from_config_dirs
from usage_monitor_for_claude.instance_id import autostart_config_dir_argument, resolve_config_dirs
from usage_monitor_for_claude.platforms import prepare_gui_environment, set_dpi_awareness, show_error_box

_verbose = '--verbose' in sys.argv

# In frozen builds (console=False), stdout/stderr go nowhere.
# --verbose attaches a console so diagnostics are visible.
if _verbose and getattr(sys, 'frozen', False):
    from usage_monitor_for_claude.verbose import setup_console
    setup_console()

# --config-dir names the Claude account(s) to monitor; without it the
# CLAUDE_CONFIG_DIR variable does, and without either the default ~/.claude.
# One process monitors every directory: one tray icon per account, one popup.
#
# platforms, instance_id and account are imported early because this block
# needs them; none reads CLAUDE_CONFIG_DIR at import time (settings imports
# platforms, so a platforms->env dependency would be a cycle). Keep every
# other package import below this block.
_config_dirs = resolve_config_dirs()
_missing_config_dirs = [config_dir for config_dir in _config_dirs if not Path(config_dir).is_dir()]
if _missing_config_dirs:
    show_error_box(
        'Claude config directory does not exist:\n' + '\n'.join(_missing_config_dirs),
        'Usage Monitor for Claude - Error',
    )
    sys.exit(1)

_accounts = accounts_from_config_dirs(_config_dirs)

# Both must be settled before pywebview creates any window: DPI awareness
# cannot be changed once a window exists, and the GUI toolkit reads its
# backend from the environment as it loads.
set_dpi_awareness()
prepare_gui_environment()

if _verbose:
    from usage_monitor_for_claude.verbose import print_startup_diagnostics
    print_startup_diagnostics()

import webview  # type: ignore[import-untyped]  # no type stubs available

from usage_monitor_for_claude.app import UsageMonitorForClaude, crash_log
from usage_monitor_for_claude.launch import spawn_app_instance
from usage_monitor_for_claude.platforms import register_notification_identity
from usage_monitor_for_claude.platforms.instance import ensure_single_instance, release_instance_lock

if _verbose:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)-5s %(name)s: %(message)s',
        datefmt='%H:%M:%S',
    )

_result: dict = {}


def _verbose_step(label: str) -> None:
    """Print a startup progress step in verbose mode."""
    if _verbose:
        print(f'  [startup] {label}', flush=True)


def _run_app() -> None:
    """Run the tray application in a background thread (called by webview)."""
    try:
        if _verbose:
            from usage_monitor_for_claude.verbose import print_runtime_diagnostics
            print_runtime_diagnostics()

        _verbose_step('UsageMonitorForClaude()...')
        app = UsageMonitorForClaude(_accounts)
        _verbose_step('UsageMonitorForClaude()... OK')

        _verbose_step('app.run...')
        app.run()
        _result['app'] = app
    except Exception:
        _verbose_step(f'CRASH: {traceback.format_exc()}')
        crash_log(traceback.format_exc())
    finally:
        # Destroy all webview windows (keeper + any open popups) so
        # webview.start() on the main thread returns.
        for win in list(webview.windows):
            try:
                win.destroy()
            except Exception:
                pass


try:
    _verbose_step('ensure_single_instance...')
    if not ensure_single_instance():
        _verbose_step('another instance is running, exiting')
        sys.exit(0)
    _verbose_step('ensure_single_instance... OK')

    # Give notifications a fixed logo instead of the live tray icon.
    # Must run before any window is created (AppUserModelID requirement).
    _verbose_step('register_notification_identity...')
    register_notification_identity()

    # pywebview requires the main thread for its GUI event loop.
    # A persistent hidden window keeps the loop alive while the
    # tray app and popup windows are managed in background threads.
    _verbose_step('webview.create_window...')
    webview.create_window('', html='', hidden=True)
    _verbose_step('webview.create_window... OK')

    _verbose_step('webview.start...')
    webview.start(func=_run_app)
    _verbose_step('webview.start returned')

    app = _result.get('app')
    if app and app.restart_requested:
        release_instance_lock()

        passthrough_args = []
        config_dir_argument = autostart_config_dir_argument()
        if config_dir_argument is not None:
            passthrough_args.append(f'--config-dir={config_dir_argument}')
        if _verbose:
            passthrough_args.append('--verbose')

        spawn_app_instance(passthrough_args)
except Exception:
    crash_log(traceback.format_exc())
