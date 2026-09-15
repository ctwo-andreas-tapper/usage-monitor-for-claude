"""
Popup JS Tests
===============

Behavior tests for popup.js DOM update logic, executed with Node.js
against a minimal DOM stub.  Skipped when Node.js is not installed -
the app itself never needs Node; it is only used as a test runner
for the popup's JavaScript.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_POPUP_JS = Path(__file__).parent.parent / 'usage_monitor_for_claude' / 'popup' / 'popup.js'

_NODE = shutil.which('node')

# Minimal DOM stub covering exactly the APIs the bar create/update path uses.
_DOM_STUB = r'''
class StubElement {
    constructor(tag) {
        this.tagName = tag;
        this.className = '';
        this._text = '';
        this.title = '';
        this.style = {};
        this.dataset = {};
        this.children = [];
        this.parentNode = null;
        const element = this;
        this.classList = {
            toggle(name, force) {
                const classes = element._classSet();
                const on = force === undefined ? !classes.has(name) : !!force;
                if (on) classes.add(name); else classes.delete(name);
                element.className = [...classes].join(' ');
                return on;
            },
            add(name) { const classes = element._classSet(); classes.add(name); element.className = [...classes].join(' '); },
            remove(name) { const classes = element._classSet(); classes.delete(name); element.className = [...classes].join(' '); },
            contains(name) { return element._classSet().has(name); },
        };
    }
    _classSet() { return new Set(this.className.split(/\s+/).filter(Boolean)); }
    // textContent aggregates child text (badge + name) like a real element,
    // and clears children when assigned - matching DOM semantics closely
    // enough for the badge/label scenarios.
    get textContent() { return this.children.length ? this.children.map((child) => child.textContent).join('') : this._text; }
    set textContent(value) { this._text = value; this.children = []; }
    appendChild(node) { node.parentNode = this; this.children.push(node); return node; }
    append(...nodes) { for (const node of nodes) this.appendChild(node); }
    replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
    remove() {
        if (this.parentNode) {
            const index = this.parentNode.children.indexOf(this);
            if (index >= 0) this.parentNode.children.splice(index, 1);
            this.parentNode = null;
        }
    }
    matches(selector) { return selector.startsWith('.') && this._classSet().has(selector.slice(1)); }
    querySelector(selector) {
        for (const child of this.children) {
            if (child.matches(selector)) return child;
            const nested = child.querySelector(selector);
            if (nested) return nested;
        }
        return null;
    }
    querySelectorAll(selector) {
        const found = [];
        for (const child of this.children) {
            if (child.matches(selector)) found.push(child);
            found.push(...child.querySelectorAll(selector));
        }
        return found;
    }
}

globalThis.document = {
    createElement: (tag) => new StubElement(tag),
    createTextNode: (text) => ({ textContent: text, parentNode: null, matches() { return false; }, querySelector() { return null; }, querySelectorAll() { return []; }, remove() {} }),
    body: new StubElement('body'),
};
globalThis.ResizeObserver = class { constructor() {} observe() {} };
globalThis.requestAnimationFrame = (callback) => callback();
'''

_SCENARIO_PRELUDE = r'''
els = { usageBars: document.createElement('div') };

function makeEntry(overrides) {
    return Object.assign({
        key: 'five_hour', label: '5h', pct_text: '0%', fill_pct: 0.0,
        warn: false, dividers: [], marker_rel: null, reset_text: '', account_index: 0,
    }, overrides);
}

// Full element set for the scenarios that drive updateData() end to end.
function makeEls() {
    const names = [
        'accountSection', 'headingAccount', 'accountRows',
        'usageSection', 'headingUsage', 'usageBars',
        'extraSection', 'extraRows',
        'installSection', 'installRows', 'statusSection', 'statusText',
    ];
    return Object.fromEntries(names.map((name) => [name, document.createElement('div')]));
}

function makeAccount(overrides) {
    return Object.assign({ index: 0, label: 'a', email: 'a@example.com', plan: 'Max', error: null, refreshing: false }, overrides);
}
function makeGroup(key, label, bars) { return { key, label, bars }; }
function makeData(overrides) {
    return Object.assign({ accounts: [makeAccount()], usage_groups: [], extra: [], installations: [], status: null }, overrides);
}

function makeExtra(overrides) {
    return Object.assign({
        has_limit: true, pct_text: '25%', fill_pct: 0.25, spent_text: '$25.00 / $100.00 spent', balance_text: '', account_index: 0,
    }, overrides);
}
'''


def _run_scenario(scenario: str) -> dict:
    """Execute the DOM stub + popup.js + scenario with Node and parse its JSON output."""
    script = _DOM_STUB + _POPUP_JS.read_text(encoding='utf-8') + _SCENARIO_PRELUDE + scenario
    with TemporaryDirectory() as tmp:
        script_path = Path(tmp) / 'scenario.js'
        script_path.write_text(script, encoding='utf-8')
        proc = subprocess.run([_NODE, str(script_path)], capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise AssertionError(f'Node scenario failed:\n{proc.stderr}')
    return json.loads(proc.stdout)


@unittest.skipUnless(_NODE, 'Node.js not available')
class TestUsageBarUpdates(unittest.TestCase):
    """Tests for renderBars/updateBarElement in popup.js."""

    def test_changed_field_set_with_equal_count_updates_labels(self):
        """When the set of quota fields changes but the count stays the same
        (e.g. an account switch between plans), the bars must not show the new
        percentages under the old labels."""
        result = _run_scenario('''
renderBars(els.usageBars, [
    makeEntry({ key: 'five_hour', label: '5h', pct_text: '10%' }),
    makeEntry({ key: 'seven_day', label: '7d', pct_text: '20%' }),
], false);
renderBars(els.usageBars, [
    makeEntry({ key: 'five_hour', label: '5h', pct_text: '30%' }),
    makeEntry({ key: 'seven_day_opus', label: '7d Opus', pct_text: '99%' }),
], false);
console.log(JSON.stringify(els.usageBars.children.map((bar) => ({
    label: bar.children[0].children[0].textContent,
    pct: bar.querySelector('.bar-pct').textContent,
}))));
''')
        self.assertEqual(result, [
            {'label': '5h', 'pct': '30%'},
            {'label': '7d Opus', 'pct': '99%'},
        ])

    def test_marker_and_divider_positions_stable_across_update(self):
        """The 2 px marker/divider elements are centered with a -1px correction
        on create; an in-place update must use the identical expression, or the
        elements shift by 1 px after the first data update."""
        result = _run_scenario('''
const fields = { key: 'five_hour', label: '5h', marker_rel: 0.5, dividers: [0.25] };
renderBars(els.usageBars, [makeEntry(Object.assign({ pct_text: '10%' }, fields))], false);
const container = els.usageBars.children[0].querySelector('.bar-container');
const before = {
    marker: container.querySelector('.bar-marker').style.left,
    divider: container.querySelector('.bar-divider').style.left,
};
renderBars(els.usageBars, [makeEntry(Object.assign({ pct_text: '11%' }, fields))], false);
const after = {
    marker: container.querySelector('.bar-marker').style.left,
    divider: container.querySelector('.bar-divider').style.left,
};
console.log(JSON.stringify({ before, after }));
''')
        self.assertEqual(result['after'], result['before'])

    def test_unchanged_field_set_updates_in_place(self):
        """With an unchanged field set, bars are updated in place (no rebuild)."""
        result = _run_scenario('''
renderBars(els.usageBars, [makeEntry({ key: 'five_hour', label: '5h', pct_text: '10%' })], false);
const barBefore = els.usageBars.children[0];
renderBars(els.usageBars, [makeEntry({ key: 'five_hour', label: '5h', pct_text: '50%', fill_pct: 0.5 })], false);
console.log(JSON.stringify({
    sameElement: els.usageBars.children[0] === barBefore,
    pct: els.usageBars.children[0].querySelector('.bar-pct').textContent,
    fillWidth: els.usageBars.children[0].querySelector('.bar-fill').style.width,
}));
''')
        self.assertEqual(result, {'sameElement': True, 'pct': '50%', 'fillWidth': '50%'})

    def test_grouped_render_one_bar_per_account(self):
        result = _run_scenario('''
els = makeEls();
updateData(makeData({
    accounts: [makeAccount(), makeAccount({ index: 1, label: 'b', email: 'b@example.com' })],
    usage_groups: [makeGroup('five_hour', 'Session', [
        makeEntry({ key: 'five_hour', label: 'Session', pct_text: '40%', account_index: 0 }),
        makeEntry({ key: 'five_hour', label: 'Session', pct_text: '70%', account_index: 1 }),
    ])],
}));
const group = els.usageBars.children[0];
console.log(JSON.stringify({
    heading: group.querySelector('.group-label').textContent,
    bars: group.querySelectorAll('.usage-entry').map((bar) => ({ key: bar.dataset.key, badge: bar.querySelector('.badge').textContent, hasName: !!bar.querySelector('.bar-label'), pct: bar.querySelector('.bar-pct').textContent })),
    accountRows: els.accountRows.children.length,
}));
''')
        self.assertEqual(result['heading'], 'Session')
        self.assertEqual(result['bars'], [
            {'key': 'five_hour:0', 'badge': '1', 'hasName': False, 'pct': '40%'},
            {'key': 'five_hour:1', 'badge': '2', 'hasName': False, 'pct': '70%'},
        ])
        self.assertEqual(result['accountRows'], 2)

    def test_single_account_has_no_badges_and_group_label_is_bar_label(self):
        result = _run_scenario('''
els = makeEls();
updateData(makeData({ usage_groups: [makeGroup('five_hour', 'Session (5hr)', [makeEntry({ key: 'five_hour', label: 'Session (5hr)', pct_text: '40%' })])] }));
const bar = els.usageBars.children[0].querySelector('.usage-entry');
console.log(JSON.stringify({
    hasGroupLabel: !!els.usageBars.children[0].querySelector('.group-label'),
    label: bar.querySelector('.bar-label').textContent,
    badges: bar.querySelectorAll('.badge').length,
}));
''')
        self.assertEqual(result, {'hasGroupLabel': False, 'label': 'Session (5hr)', 'badges': 0})

    def test_compact_hide_hides_whole_group(self):
        result = _run_scenario('''
els = makeEls();
compactHide = ['seven_day'];
popupPinned = true;
updateData(makeData({ usage_groups: [
    makeGroup('five_hour', '5h', [makeEntry({ key: 'five_hour' })]),
    makeGroup('seven_day', '7d', [makeEntry({ key: 'seven_day' })]),
] }));
console.log(JSON.stringify(els.usageBars.children.map((group) => group.dataset.key)));
''')
        self.assertEqual(result, ['five_hour'])

    def test_account_row_shows_error(self):
        result = _run_scenario('''
els = makeEls();
updateData(makeData({ accounts: [makeAccount({ error: 'HTTP 500' })] }));
console.log(JSON.stringify({ text: els.accountRows.children[0].querySelector('.account-error').textContent }));
''')
        self.assertEqual(result, {'text': 'HTTP 500'})


@unittest.skipUnless(_NODE, 'Node.js not available')
class TestExtraUsageSection(unittest.TestCase):
    """Tests for the prepaid balance line in the extra-usage section."""

    def test_balance_line_shown_when_present(self):
        """A rendered balance is shown below the extra-usage bar."""
        result = _run_scenario('''
els = makeEls();
updateData(makeData({ extra: [makeExtra({ balance_text: '$55.97 available', account_index: 0 })] }));
const balance = els.extraRows.children[0].querySelector('.extra-balance');
console.log(JSON.stringify({ text: balance.textContent, display: balance.style.display }));
''')
        self.assertEqual(result, {'text': '$55.97 available', 'display': ''})

    def test_balance_line_hidden_when_absent(self):
        """Without a balance the line is hidden, leaving the section as it was before."""
        result = _run_scenario('''
els = makeEls();
updateData(makeData({ extra: [makeExtra()] }));
let balance = els.extraRows.children[0].querySelector('.extra-balance');
const empty = { text: balance.textContent, display: balance.style.display };
updateData(makeData({ extra: [makeExtra({ balance_text: undefined })] }));
balance = els.extraRows.children[0].querySelector('.extra-balance');
const missing = { text: balance.textContent, display: balance.style.display };
console.log(JSON.stringify({ empty, missing }));
''')
        self.assertEqual(result, {
            'empty': {'text': '', 'display': 'none'},
            'missing': {'text': '', 'display': 'none'},
        })

    def test_balance_line_cleared_on_update(self):
        """A balance that becomes unavailable is removed instead of lingering."""
        result = _run_scenario('''
els = makeEls();
updateData(makeData({ extra: [makeExtra({ balance_text: '$55.97 available' })] }));
updateData(makeData({ extra: [makeExtra()] }));
const balance = els.extraRows.children[0].querySelector('.extra-balance');
console.log(JSON.stringify({ text: balance.textContent, display: balance.style.display }));
''')
        self.assertEqual(result, {'text': '', 'display': 'none'})


if __name__ == '__main__':
    unittest.main()
