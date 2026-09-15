"""
Popup Window
=============

Dark-themed HTML popup window showing account info and usage bars.

This module owns the data flow: what the popup shows, how a cache snapshot
becomes the payload for the JavaScript side, and when an update is pushed.
Everything about the window itself - styles, transparency while measuring,
anchoring, dismissal and the pinned drag - belongs to the platform host in
:mod:`usage_monitor_for_claude.platforms.popup`.
"""
from __future__ import annotations

import json
import threading
import time
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Any

import webview  # type: ignore[import-untyped]  # no type stubs available

from . import __version__
from .claude_cli import CHANGELOG_URL, find_installations
from .formatting import divider_positions, elapsed_pct, expand_popup_fields, field_period, format_credits, popup_label, time_until
from .i18n import T
from .platforms.popup import WINDOW_KWARGS, PopupHost, popup_url
from .settings import BAR_BG, BAR_DIVIDER, BAR_FG, BAR_FG_WARN, BAR_MARKER, BG, COMPACT_HIDE, FG, FG_DIM, FG_HEADING, FG_LINK, POPUP_FIELDS

_POPUP_DIR = Path(__file__).parent / 'popup'

__all__ = ['UsagePopup']

if TYPE_CHECKING:
    from .app import UsageMonitorForClaude
    from .cache import CacheSnapshot


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _usage_entries(usage: dict[str, Any]) -> list[tuple[str, dict[str, Any] | None, int | None, str]]:
    """Return ``(label, data, period, field)`` tuples from the given usage data.

    The raw *field* name is included so the popup can hide individual bars
    by field name when the pinned compact view is configured.
    """
    fields = expand_popup_fields(POPUP_FIELDS, usage)
    return [(popup_label(key), usage.get(key), field_period(key), key) for key in fields]


def _prepaid_balance_text(prepaid: dict[str, Any] | None) -> str:
    """Return the rendered prepaid-credit balance line, or '' when unavailable."""
    if not prepaid:
        return ''

    amount = prepaid.get('amount_minor')
    if amount is None:
        return ''

    balance = format_credits(amount, prepaid.get('currency'), prepaid.get('decimal_places'))

    return T['extra_usage_balance'].format(balance=balance)


def _bar(field: str, label: str, entry: dict[str, Any] | None, period: int | None, account_index: int) -> dict[str, Any] | None:
    """Return one bar dict for *entry*, or None when the field has no utilization."""
    if not entry or entry.get('utilization') is None:
        return None
    pct = entry.get('utilization', 0) or 0
    resets_at = entry.get('resets_at', '')
    time_pct = elapsed_pct(resets_at, period) if period else None
    warn = pct >= 100 or (time_pct is not None and pct > time_pct)
    marker_rel = max(0.0, min(1.0, time_pct / 100)) if time_pct is not None else None
    return {
        'key': field, 'label': label, 'account_index': account_index,
        'pct_text': f'{pct:.0f}%', 'fill_pct': max(0.0, min(1.0, pct / 100)), 'warn': warn,
        'reset_text': time_until(resets_at) if resets_at else '',
        'dividers': divider_positions(resets_at, period) if period else [],
        'marker_rel': marker_rel,
    }


def _extra_entry(snap: CacheSnapshot, account_index: int) -> dict[str, Any] | None:
    """Return the extra-usage dict for one account, or None when not enabled."""
    if not snap.usage:
        return None

    extra_data = snap.usage.get('extra_usage')
    if not (extra_data and extra_data.get('is_enabled')):
        return None

    used = extra_data.get('used_credits')
    if used is None:
        return None

    limit = extra_data.get('monthly_limit', 0) or 0
    currency = extra_data.get('currency')
    decimal_places = extra_data.get('decimal_places')
    balance_text = _prepaid_balance_text(snap.prepaid)

    if limit > 0:
        pct = used / limit * 100
        return {
            'account_index': account_index,
            'has_limit': True,
            'pct_text': f'{pct:.0f}%',
            'fill_pct': max(0.0, min(1.0, pct / 100)),
            'spent_text': T['extra_usage_spent'].format(
                used=format_credits(used, currency, decimal_places),
                limit=format_credits(limit, currency, decimal_places),
            ),
            'balance_text': balance_text,
        }

    # No monthly cap (e.g. uncapped pay-as-you-go credits) - show what has been
    # spent without a percentage bar to imply a limit.
    return {
        'account_index': account_index,
        'has_limit': False,
        'pct_text': '',
        'fill_pct': 0.0,
        'spent_text': T['extra_usage_spent_no_limit'].format(
            used=format_credits(used, currency, decimal_places),
        ),
        'balance_text': balance_text,
    }


def _account_entry(index: int, monitor: Any, snap: CacheSnapshot) -> dict[str, Any]:
    """Build one account row: label, email, plan and any account-level error."""
    account = (snap.profile or {}).get('account') or {} if snap.profile else {}
    org = (snap.profile or {}).get('organization') or {} if snap.profile else {}
    error = snap.last_error[:120] if (not snap.usage and snap.last_error) else None
    return {
        'index': index, 'label': monitor.account.label,
        'email': account.get('email', ''), 'plan': org.get('organization_type', '').replace('_', ' ').title(),
        'error': error, 'refreshing': not snap.usage and not error,
    }


def _combined_usage(snaps: list[CacheSnapshot]) -> dict[str, Any]:
    """Merge every account's usage so the field order can be derived once (first non-null wins)."""
    combined: dict[str, Any] = {}
    for snap in snaps:
        for key, value in snap.usage.items():
            if key not in combined or combined[key] is None:
                combined[key] = value
    return combined


def _popup_data(monitors: list[Any], installations: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """Build the JSON payload for popup.js from every account's snapshot."""
    snaps = [monitor.cache.snapshot for monitor in monitors]
    accounts = [_account_entry(index, monitor, snap) for index, (monitor, snap) in enumerate(zip(monitors, snaps))]

    usage_groups = []
    for field in expand_popup_fields(POPUP_FIELDS, _combined_usage(snaps)):
        period = field_period(field)
        label = popup_label(field)
        bars = []
        for index, snap in enumerate(snaps):
            bar = _bar(field, label, snap.usage.get(field) if snap.usage else None, period, index)
            if bar is not None:
                bars.append(bar)
        if bars:
            usage_groups.append({'key': field, 'label': label, 'bars': bars})

    extra = [entry for entry in (_extra_entry(snap, index) for index, snap in enumerate(snaps)) if entry is not None]

    if installations is None:
        installations = [{'name': i.name, 'version': i.version} for i in find_installations()]

    with_data = [snap for snap in snaps if snap.usage]
    if not with_data:
        first_error = next((snap.last_error for snap in snaps if snap.last_error), None)
        if first_error:
            status: dict[str, Any] = {'text': first_error[:120], 'is_error': True}
        else:
            status = {'text': T['status_refreshing'], 'is_error': False, 'refreshing': True}
    else:
        success_times = [snap.last_success_time for snap in with_data if snap.last_success_time is not None]
        poll_times = [monitor._next_poll_time for monitor in monitors if monitor._next_poll_time is not None]
        first_error = next((snap.last_error for snap in snaps if snap.last_error), None)
        status = {
            'last_success_time': min(success_times) if success_times else None,
            'next_poll_time': min(poll_times) if poll_times else None,
            'refreshing': any(snap.refreshing for snap in snaps),
            'error': first_error[:120] if first_error else None,
        }

    return {'accounts': accounts, 'usage_groups': usage_groups, 'extra': extra, 'installations': installations, 'status': status}


def _init_config(monitors: list[Any]) -> dict[str, Any]:
    """Build the config object passed to JS ``init()`` after the page loads."""
    return {
        'colors': {
            'bg': BG, 'fg': FG, 'fg_dim': FG_DIM, 'fg_heading': FG_HEADING, 'fg_link': FG_LINK,
            'bar_bg': BAR_BG, 'bar_fg': BAR_FG, 'bar_fg_warn': BAR_FG_WARN, 'bar_divider': BAR_DIVIDER, 'bar_marker': BAR_MARKER,
        },
        't': {
            'title': T['popup_title'], 'account': T['account'], 'accounts': T['accounts'], 'email': T['email'], 'plan': T['plan'],
            'usage': T['usage'], 'extra_usage': T['extra_usage'],
            'claude_code': T['claude_code'], 'changelog': T['changelog'],
            'pin_popup': T['pin_popup'], 'unpin_popup': T['unpin_popup'],
            'status_updated_s': T['status_updated_s'], 'status_updated': T['status_updated'],
            'status_next_update': T['status_next_update'], 'status_refreshing': T['status_refreshing'],
            'duration_hm': T['duration_hm'], 'duration_m': T['duration_m'], 'duration_s': T['duration_s'],
        },
        'app_version': __version__,
        'compact_hide': COMPACT_HIDE,
        'data': _popup_data(monitors),
    }


# ---------------------------------------------------------------------------
# JS-callable API
# ---------------------------------------------------------------------------

class _PopupApi:
    """Methods exposed to JavaScript via pywebview's JS bridge."""

    def __init__(self, popup: UsagePopup) -> None:
        self._popup = popup

    def close(self) -> None:
        self._popup._close()

    def open_url(self) -> None:
        webbrowser.open(CHANGELOG_URL)

    def set_pinned(self, pinned: bool) -> bool:
        return self._popup._set_pinned(pinned)

    def begin_drag(self) -> bool:
        return self._popup._begin_drag()

    def drag(self) -> bool:
        return self._popup._drag()

    def end_drag(self) -> None:
        self._popup._end_drag()

    def report_height(self, height: int) -> None:
        """Called by JS ResizeObserver when content height changes."""
        if not height:
            return

        self._popup._apply_height(height)


# ---------------------------------------------------------------------------
# Popup window
# ---------------------------------------------------------------------------

class UsagePopup:
    """Dark-themed HTML popup window showing every monitored account and its usage bars."""

    WIDTH = 340
    _CHECK_MS = 2000
    _INITIAL_HEIGHT = 400

    def __init__(self, app: UsageMonitorForClaude) -> None:
        """Create and display a popup window with usage details.

        Blocks the calling thread until the window is closed.
        Requires ``webview.start()`` to be running on the main thread.

        Parameters
        ----------
        app : UsageMonitorForClaude
            Parent application providing ``cache`` for data access.
        """
        self.app = app
        self._running = True
        self._pinned = False
        self._moved_while_pinned = False
        self._shown = False
        self._closed = threading.Event()
        # Serializes the resize/show geometry path across pywebview's
        # per-call bridge threads.
        self._geometry_lock = threading.Lock()
        # 0 means "no height reported yet": the first report must always count
        # as a change so the window gets resized, positioned and shown even
        # when the content is exactly _INITIAL_HEIGHT tall.
        self._last_height = 0
        self._last_versions = self._versions()

        self._window = webview.create_window(
            '', url=popup_url(_POPUP_DIR / 'popup.html'),
            width=self.WIDTH, height=self._INITIAL_HEIGHT,
            frameless=True, easy_drag=False,
            on_top=True, hidden=True,
            background_color=BG,
            js_api=_PopupApi(self),
            **WINDOW_KWARGS,
        )
        self._host = PopupHost(self._window, self.WIDTH)
        self._window.events.loaded += self._on_loaded
        self._window.events.closed += self._on_window_closed
        threading.Thread(target=self._dismiss_watch, daemon=True).start()
        self._closed.wait()

    def _on_loaded(self) -> None:
        """Hand initialisation to a worker thread.

        The GTK backend delivers this event on its main loop, where every
        pywebview call blocks waiting for that same loop.  Initialising
        inline would deadlock the session.
        """
        threading.Thread(target=self._initialise, daemon=True).start()

    def _versions(self) -> tuple[Any, ...]:
        return tuple((monitor.cache.snapshot.version, monitor._next_poll_time) for monitor in self.app.monitors)

    def _initialise(self) -> None:
        """Inject the config, then size and show the window."""
        config = _init_config(self.app.monitors)
        self._window.evaluate_js(f'init({json.dumps(config)})')

        self._host.prepare()

        # The height is read here rather than waited for: popup.js reports it
        # through a ResizeObserver guarded by the pywebview bridge, and that
        # observer's single firing can precede the bridge being ready.  A
        # later report of the same height is a no-op.
        height = int(self._window.evaluate_js('document.body.scrollHeight') or 0)
        if height:
            self._apply_height(height)

    def _apply_height(self, height: int) -> None:
        """Resize and position for *height*, revealing the window the first time.

        pywebview dispatches every bridge call on a fresh thread, so two rapid
        reports could interleave and apply the earlier resize after the later
        one, or both start the reveal path.  The geometry lock serializes the
        whole check-resize-reveal sequence.
        """
        with self._geometry_lock:
            if height == self._last_height:
                return

            self._last_height = height
            self._host.apply_geometry(height, keep_position=self._pinned and self._moved_while_pinned)

            if self._shown:
                return

            self._shown = True
            self._host.reveal()
            threading.Thread(target=self._update_loop, daemon=True).start()

    def _dismiss_watch(self) -> None:
        """Close the popup once the platform host reports a dismissal."""
        self._host.watch_dismiss(self._should_dismiss, lambda: self._running)
        self._close()

    def _should_dismiss(self) -> bool:
        """Whether a dismissal gesture should close the popup right now."""
        return self._shown and not self._pinned

    def _on_window_closed(self) -> None:
        self._running = False
        self._host.stop_watch()
        self._closed.set()

    def _close(self) -> None:
        self._running = False
        self._host.stop_watch()
        try:
            self._window.destroy()
        except Exception:
            pass
        self._closed.set()

    def _set_pinned(self, pinned: bool) -> bool:
        """Apply the pin state and report what was applied.

        popup.js assigns the returned value back to its own state, so this
        must report reality rather than a constant.
        """
        self._pinned = bool(pinned)
        if not self._pinned:
            self._moved_while_pinned = False

        return self._pinned

    def _begin_drag(self) -> bool:
        if not self._pinned:
            return False

        return self._host.begin_drag()

    def _drag(self) -> bool:
        if not self._pinned:
            return False

        moved = self._host.drag()
        if moved:
            self._moved_while_pinned = True

        return moved

    def _end_drag(self) -> None:
        with self._geometry_lock:
            self._host.end_drag(self._last_height)

    def _update_loop(self) -> None:
        """Poll for data changes and push updates to the popup."""
        cached_installations = [{'name': i.name, 'version': i.version} for i in find_installations()]
        while self._running:
            time.sleep(self._CHECK_MS / 1000)
            if not self._running:
                break
            try:
                versions = self._versions()
                if versions == self._last_versions:
                    continue
                snapshot_versions = tuple(version for version, _ in versions)
                if snapshot_versions != tuple(version for version, _ in self._last_versions):
                    cached_installations = [{'name': i.name, 'version': i.version} for i in find_installations()]
                data = _popup_data(self.app.monitors, installations=cached_installations)
                self._window.evaluate_js(f'updateData({json.dumps(data)})')
                # Commit the markers only after a successful push, so a failed
                # update is retried on the next tick instead of being skipped
                # by the dedup check until the next data change.
                self._last_versions = versions
            except Exception:
                # A transient failure (snapshot conversion, filesystem scan,
                # one-off evaluate_js hiccup) must not end the update stream -
                # a pinned popup can live for days.  The destroyed-window
                # case exits via the _running flag on the next iteration.
                continue
