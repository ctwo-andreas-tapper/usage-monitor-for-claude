let els;
let statusState = {};
let translations = {};
let textTimerId = null;
let popupPinned = false;
let compactHide = [];
let lastData = null;
let accountsById = {};

/**
 * Set CSS custom properties for theme colors and inject translation strings.
 *
 * Called once by Python after the page loads.  Translations are set as
 * textContent on heading elements so the HTML file stays language-neutral.
 *
 * @param {object} config - { colors, t (translations), app_version, data (initial snapshot) }
 */
function init(config) {
    const s = document.documentElement.style;
    for (const [key, value] of Object.entries(config.colors)) {
        s.setProperty(`--${key.replaceAll('_', '-')}`, value);
    }

    translations = config.t;
    compactHide = config.compact_hide || [];
    document.getElementById('title').textContent = translations.title;
    document.getElementById('headingUsage').textContent = translations.usage;
    document.getElementById('headingExtraUsage').textContent = translations.extra_usage;
    document.getElementById('headingClaudeCode').textContent = translations.claude_code;

    const changelogLink = document.getElementById('changelogLink');
    changelogLink.textContent = translations.changelog;
    changelogLink.addEventListener('click', () => pywebview.api.open_url());
    document.getElementById('closeBtn').addEventListener('click', () => pywebview.api.close());
    setupPinButton();
    setupPinnedDrag();

    document.getElementById('appVersion').textContent = config.app_version;

    els = {
        accountSection: document.getElementById('accountSection'),
        headingAccount: document.getElementById('headingAccount'),
        accountRows: document.getElementById('accountRows'),
        usageSection: document.getElementById('usageSection'),
        headingUsage: document.getElementById('headingUsage'),
        usageBars: document.getElementById('usageBars'),
        extraSection: document.getElementById('extraSection'),
        extraRows: document.getElementById('extraRows'),
        installSection: document.getElementById('installSection'),
        installRows: document.getElementById('installRows'),
        statusSection: document.getElementById('statusSection'),
        statusText: document.getElementById('statusText'),
    };

    updateData(config.data);
    requestAnimationFrame(() => document.body.classList.add('open'));
}

function setupPinButton() {
    const pinBtn = document.getElementById('pinBtn');

    function render() {
        document.body.classList.toggle('pinned', popupPinned);
        pinBtn.classList.toggle('pinned', popupPinned);
        pinBtn.setAttribute('aria-pressed', popupPinned ? 'true' : 'false');
        pinBtn.setAttribute('aria-label', popupPinned ? translations.unpin_popup : translations.pin_popup);
        pinBtn.title = popupPinned ? translations.unpin_popup : translations.pin_popup;
    }

    pinBtn.addEventListener('click', () => {
        const nextPinned = !popupPinned;
        popupPinned = nextPinned;
        render();
        reapplyData();
        pywebview.api.set_pinned(nextPinned).then((applied) => {
            popupPinned = !!applied;
            render();
            reapplyData();
        }).catch(() => {
            popupPinned = !nextPinned;
            render();
            reapplyData();
        });
    });

    render();
}

/**
 * Return true if a section or usage bar is hidden by the pinned compact view.
 *
 * Hiding only applies while the popup is pinned; unpinned it always shows
 * everything.  `key` is a section key (account, extra_usage, claude_code,
 * status) or a usage field name (e.g. seven_day_opus).
 */
function compactHidden(key) {
    return popupPinned && compactHide.includes(key);
}

// Re-render the last snapshot so compact hiding takes effect on pin toggle.
function reapplyData() {
    if (lastData) {
        updateData(lastData);
    }
}

function setupPinnedDrag() {
    const header = document.querySelector('header');
    let dragging = false;

    function setDragging(active) {
        dragging = active;
        header.classList.toggle('dragging', active);
    }

    header.addEventListener('mousedown', (event) => {
        if (!popupPinned || event.button !== 0 || event.target.closest('button')) {
            return;
        }
        event.preventDefault();
        setDragging(true);
        pywebview.api.begin_drag().then((started) => {
            setDragging(!!started);
        }).catch(() => {
            setDragging(false);
        });
    });

    document.addEventListener('mousemove', (event) => {
        if (!dragging) {
            return;
        }
        // No button held (e.g. released outside the window): stop dragging.
        if (event.buttons === 0) {
            setDragging(false);
            pywebview.api.end_drag();
            return;
        }
        pywebview.api.drag().catch(() => {});
    });

    document.addEventListener('mouseup', () => {
        if (!dragging) {
            return;
        }
        setDragging(false);
        pywebview.api.end_drag();
    });
}

function barKey(entry) { return `${entry.key}:${entry.account_index}`; }

function badge(index) {
    const span = document.createElement('span');
    span.className = 'badge';
    span.textContent = String(index + 1);
    return span;
}

function updateAccounts(accounts, multi) {
    els.headingAccount.textContent = multi ? translations.accounts : translations.account;
    els.accountRows.replaceChildren(...accounts.map((account) => {
        const row = document.createElement('div');
        row.className = 'account-row';
        const name = document.createElement('span');
        name.className = 'account-name';
        if (multi) name.appendChild(badge(account.index));
        const text = document.createElement('span');
        text.textContent = account.email || account.label;
        name.appendChild(text);
        const plan = document.createElement('span');
        plan.className = 'account-plan';
        plan.textContent = account.plan || '';
        row.append(name, plan);
        if (account.error) {
            const error = document.createElement('div');
            error.className = 'account-error';
            error.textContent = account.error;
            row.appendChild(error);
        }
        return row;
    }));
}

/**
 * Update all popup sections with fresh data from Python.
 *
 * @param {object} data - Pre-formatted payload from _popup_data().
 */
function updateData(data) {
    lastData = data;
    const accounts = data.accounts || [];
    const multi = accounts.length > 1;
    accountsById = Object.fromEntries(accounts.map((account) => [account.index, account]));

    const accountVisible = accounts.length > 0 && !compactHidden('account');
    els.accountSection.classList.toggle('visible', accountVisible);
    if (accountVisible) updateAccounts(accounts, multi);

    const groups = (data.usage_groups || []).filter((group) => !compactHidden(group.key));
    const hasUsage = !!groups.length;
    els.usageSection.classList.toggle('visible', hasUsage);
    if (hasUsage) updateUsageGroups(groups, multi);

    const extra = data.extra || [];
    const extraVisible = !!extra.length && !compactHidden('extra_usage');
    els.extraSection.classList.toggle('visible', extraVisible);
    if (extraVisible) updateExtra(extra, multi);

    const hasInstalls = !!data.installations?.length;
    const installsVisible = hasInstalls && !compactHidden('claude_code');
    els.installSection.classList.toggle('visible', installsVisible);

    // The "Usage" heading only labels the bars against the other sections;
    // when the usage bars stand alone, drop the now-redundant heading.
    els.headingUsage.style.display = (hasUsage && !accountVisible && !extraVisible && !installsVisible) ? 'none' : '';

    if (hasInstalls) {
        els.installRows.replaceChildren(...data.installations.map((inst) => {
            const row = document.createElement('div');
            const dt = document.createElement('dt');
            dt.textContent = inst.name;
            const dd = document.createElement('dd');
            dd.textContent = inst.version;
            row.append(dt, dd);
            return row;
        }));
    }

    updateStatus(data.status);
}

/**
 * Update the status footer with live timer data or static text.
 *
 * Live mode (has last_success_time): starts a 1-second interval for
 * the text counter.  Static mode (has text): shows plain text.
 */
function updateStatus(status) {
    if (textTimerId) {
        clearInterval(textTimerId);
        textTimerId = null;
    }

    if (!status) {
        els.statusSection.classList.remove('visible');
        return;
    }

    // Keep the live timer running even when the footer is hidden in compact
    // view, so the stale-dimming of the usage bars still updates.
    els.statusSection.classList.toggle('visible', !compactHidden('status'));

    if (status.last_success_time !== undefined) {
        statusState = {
            lastSuccessTime: status.last_success_time,
            nextPollTime: status.next_poll_time,
            refreshing: status.refreshing,
            error: status.error,
        };
        els.statusSection.classList.toggle('error', !!status.error);
        tickStatusText();
        textTimerId = setInterval(tickStatusText, 1000);
    } else {
        statusState = {};
        els.statusText.textContent = status.text || '';
        els.statusText.title = status.is_error ? (status.text || '') : '';
        els.statusSection.classList.toggle('error', !!status.is_error);
    }
}

/**
 * Build and display the status text from current state.
 *
 * < 60s:  "Updated Xs ago"
 * >= 60s: "Updated Xm ago · Next update in Ym"
 * + refreshing or error appended with · separator
 */
function tickStatusText() {
    if (!statusState.lastSuccessTime) return;

    const now = Date.now() / 1000;
    const secondsAgo = Math.max(0, Math.floor(now - statusState.lastSuccessTime));
    const isStale = !!statusState.nextPollTime && (now > statusState.nextPollTime + 30);
    els.usageSection.classList.toggle('stale', isStale);
    els.extraSection.classList.toggle('stale', isStale);

    const parts = [formatDuration(secondsAgo)];

    if (statusState.refreshing) {
        parts.push(translations.status_refreshing);
    } else if (statusState.error) {
        parts.push(statusState.error);
    } else if (secondsAgo >= 60 && statusState.nextPollTime) {
        const secondsUntil = Math.max(0, Math.floor(statusState.nextPollTime - now));
        if (secondsUntil > 0) {
            parts.push(translations.status_next_update.replace('{duration}', formatCountdown(secondsUntil)));
        }
    }

    els.statusText.textContent = parts.join(' \u00b7 ');
    // Errors are raw API messages that can overflow; reveal the full text on hover.
    els.statusText.title = statusState.error ? els.statusText.textContent : '';
}

/**
 * Format seconds into a localized "Updated Xs ago" / "Updated Xm ago" string.
 */
function formatDuration(totalSeconds) {
    if (totalSeconds < 60) {
        return translations.status_updated_s.replace('{s}', totalSeconds);
    }

    const totalMin = Math.floor(totalSeconds / 60);
    const hours = Math.floor(totalMin / 60);
    const mins = totalMin % 60;

    let duration;
    if (hours > 0) {
        duration = translations.duration_hm.replace('{h}', hours).replace('{m}', mins);
    } else {
        duration = translations.duration_m.replace('{m}', totalMin);
    }
    return translations.status_updated.replace('{duration}', duration);
}

/**
 * Format a countdown in seconds into a localized duration string.
 */
function formatCountdown(totalSeconds) {
    if (totalSeconds < 60) {
        return translations.duration_s.replace('{s}', totalSeconds);
    }

    const totalMin = Math.ceil(totalSeconds / 60);
    const hours = Math.floor(totalMin / 60);
    const mins = totalMin % 60;

    if (hours > 0) {
        return translations.duration_hm.replace('{h}', hours).replace('{m}', mins);
    }
    return translations.duration_m.replace('{m}', totalMin);
}

function updateUsageGroups(groups, multi) {
    // Rebuild the group scaffold only when the set of quota models changes;
    // otherwise the per-account bars inside each group update in place.
    const existing = els.usageBars.children;
    const sameGroups = groups.length === existing.length && groups.every((group, i) => existing[i].dataset.key === group.key);
    if (!sameGroups) {
        els.usageBars.replaceChildren(...groups.map((group) => {
            const div = document.createElement('div');
            div.className = 'usage-group';
            div.dataset.key = group.key;
            if (multi) {
                const label = document.createElement('div');
                label.className = 'group-label';
                label.textContent = group.label;
                div.appendChild(label);
            }
            const bars = document.createElement('div');
            bars.className = 'group-bars';
            div.appendChild(bars);
            return div;
        }));
    }
    for (let i = 0; i < groups.length; i++) {
        renderBars(els.usageBars.children[i].querySelector('.group-bars'), groups[i].bars, multi);
    }
}

function renderBars(container, entries, multi) {
    // Rebuild whenever the (field, account) set changes, not only the count -
    // after an account switch the same number of bars can carry different
    // quotas, and an in-place update would show the new values under the old
    // labels.
    const bars = container.children;
    const sameFields = entries.length === bars.length && entries.every((entry, i) => bars[i].dataset.key === barKey(entry));
    if (!sameFields) {
        container.replaceChildren(...entries.map((entry) => createBarElement(entry, multi)));
        requestAnimationFrame(() => {
            for (let i = 0; i < entries.length; i++) {
                container.children[i].querySelector('.bar-fill').style.width = `${entries[i].fill_pct * 100}%`;
            }
        });
    } else {
        for (let i = 0; i < entries.length; i++) updateBarElement(container.children[i], entries[i]);
    }
}

function createBarElement(entry, multi) {
    const div = document.createElement('div');
    div.className = 'usage-entry';
    div.dataset.key = barKey(entry);

    const pct = document.createElement('span');
    pct.className = 'bar-pct';
    pct.textContent = entry.pct_text;

    const container = document.createElement('div');
    container.className = 'bar-container';
    const fill = document.createElement('div');
    fill.className = 'bar-fill';
    fill.classList.toggle('warn', entry.warn);
    fill.style.width = '0%';
    container.appendChild(fill);

    for (const pos of entry.dividers) {
        const d = document.createElement('div');
        d.className = 'bar-divider';
        d.style.left = `calc(${pos * 100}% - 1px)`;
        container.appendChild(d);
    }

    if (entry.marker_rel !== null) {
        const marker = document.createElement('div');
        marker.className = 'bar-marker';
        marker.style.left = `calc(${entry.marker_rel * 100}% - 1px)`;
        container.appendChild(marker);
    }

    if (multi) {
        // The account is identified by a badge in front of the bar; its name
        // lives once in the ACCOUNTS list and the quota name in the group
        // heading, so neither is repeated on the bar.
        const row = document.createElement('div');
        row.className = 'bar-row';
        row.append(badge(entry.account_index), container, pct);
        div.appendChild(row);
    } else {
        const header = document.createElement('div');
        header.className = 'bar-header';
        const label = document.createElement('span');
        label.className = 'bar-label';
        label.textContent = entry.label;
        header.append(label, pct);
        div.append(header, container);
    }

    if (entry.reset_text) {
        const reset = document.createElement('div');
        reset.className = 'reset-text';
        reset.textContent = entry.reset_text;
        div.appendChild(reset);
    }

    return div;
}

function updateBarElement(div, entry) {
    div.querySelector('.bar-pct').textContent = entry.pct_text;

    const fill = div.querySelector('.bar-fill');
    fill.style.width = `${entry.fill_pct * 100}%`;
    fill.classList.toggle('warn', entry.warn);

    const container = div.querySelector('.bar-container');
    let marker = container.querySelector('.bar-marker');
    if (entry.marker_rel !== null) {
        if (!marker) {
            marker = document.createElement('div');
            marker.className = 'bar-marker';
            container.appendChild(marker);
        }
        marker.style.left = `calc(${entry.marker_rel * 100}% - 1px)`;
    } else if (marker) {
        marker.remove();
    }

    for (const d of container.querySelectorAll('.bar-divider')) d.remove();
    for (const pos of entry.dividers) {
        const d = document.createElement('div');
        d.className = 'bar-divider';
        d.style.left = `calc(${pos * 100}% - 1px)`;
        container.appendChild(d);
    }

    let resetEl = div.querySelector('.reset-text');
    if (entry.reset_text) {
        if (!resetEl) {
            resetEl = document.createElement('div');
            resetEl.className = 'reset-text';
            div.appendChild(resetEl);
        }
        resetEl.textContent = entry.reset_text;
    } else if (resetEl) {
        resetEl.remove();
    }
}

function updateExtra(entries, multi) {
    // The count changes only when an account gains or loses extra usage, which
    // is rare; each entry then rebuilds, otherwise the row updates in place.
    // A change in badge visibility (single <-> multi) also forces a rebuild,
    // since the in-place path never touches the account badge.
    const rows = els.extraRows.children;
    const badgeMismatch = rows.length > 0 && multi !== !!rows[0].querySelector('.badge');
    if (entries.length !== rows.length || badgeMismatch) {
        els.extraRows.replaceChildren(...entries.map((entry) => createExtraElement(entry, multi)));
    } else {
        for (let i = 0; i < entries.length; i++) updateExtraElement(rows[i], entries[i]);
    }
}

function createExtraElement(entry, multi) {
    const div = document.createElement('div');
    div.className = 'usage-entry';

    const header = document.createElement('div');
    header.className = 'bar-header';
    const spent = document.createElement('span');
    spent.className = 'extra-spent';
    if (multi) spent.appendChild(badge(entry.account_index));
    const spentValue = document.createElement('span');
    spentValue.className = 'extra-spent-value';
    spentValue.textContent = entry.spent_text;
    spent.appendChild(spentValue);
    const pct = document.createElement('span');
    pct.className = 'bar-pct';
    pct.textContent = entry.pct_text;
    pct.style.display = entry.has_limit ? '' : 'none';
    header.append(spent, pct);

    const container = document.createElement('div');
    container.className = 'bar-container';
    container.style.display = entry.has_limit ? '' : 'none';
    const fill = document.createElement('div');
    fill.className = 'bar-fill';
    fill.style.width = `${entry.fill_pct * 100}%`;
    container.appendChild(fill);

    const balance = document.createElement('span');
    balance.className = 'extra-balance';
    balance.textContent = entry.balance_text || '';
    balance.style.display = entry.balance_text ? '' : 'none';

    div.append(header, container, balance);
    return div;
}

function updateExtraElement(div, entry) {
    div.querySelector('.extra-spent-value').textContent = entry.spent_text;

    const pct = div.querySelector('.bar-pct');
    pct.textContent = entry.pct_text;
    pct.style.display = entry.has_limit ? '' : 'none';

    const container = div.querySelector('.bar-container');
    container.style.display = entry.has_limit ? '' : 'none';
    container.querySelector('.bar-fill').style.width = `${entry.fill_pct * 100}%`;

    const balance = div.querySelector('.extra-balance');
    balance.textContent = entry.balance_text || '';
    balance.style.display = entry.balance_text ? '' : 'none';
}

// Report content height changes to the host (pywebview or dev.html iframe parent).
new ResizeObserver(() => {
    const height = document.body.scrollHeight;
    if (window.pywebview?.api?.report_height) {
        pywebview.api.report_height(height);
    }
}).observe(document.body);
