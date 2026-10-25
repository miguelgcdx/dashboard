"""Synthetic bridge, legacy isolation, and real file:// Chromium checks."""

import base64
from contextlib import redirect_stdout
from copy import deepcopy
import io
from datetime import datetime, timedelta, timezone
import hashlib
import html as html_module
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from dashboard import (_COVERAGE_KEYS, DEFAULT_OUTPUT, company_for, company_slots, main,
                       react_snapshot_json, render_react_dashboard, write_dashboard)
from pi_usage import collect_combined_usage


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "web" / "dist"


def fixture():
    return {
        "rows": [{"day": "2026-06-01", "provider": "synthetic",
                  "model": "</script><img src=x onerror=alert(1)>", "role": None,
                  "input": 9007199254740993, "output": 3, "cacheRead": 4,
                  "cacheWrite": 2, "cost": 0.125,
                  "prompt": "PRIVATE_PROMPT", "sessionPath": "/PRIVATE_PATH"},
                 {"day": "2026-06-02", "provider": "synthetic", "model": "test-model",
                  "role": "worker", "input": 7, "output": 1, "cacheRead": 3,
                  "cacheWrite": 0, "cost": 0.25}],
        "sessions": [],
        "roles": ["worker", "explore"],
        "stats": {"asOf": "2026-06-03T12:00:00+00:00", "timezone": "UTC",
                  "oldestDay": "2026-06-01", "newestDay": "2026-06-02",
                  "malformed": 1, "unreadable": 0, "future": 0, "outOfRange": 0,
                  "coverage": {**dict.fromkeys(_COVERAGE_KEYS, 0), "childFiles": 2,
                               "linkedChildFiles": 1, "unlinkedChildFiles": 1,
                               "private": "PRIVATE_COVERAGE"},
                  "sessionId": "PRIVATE_ID"},
        "content": "PRIVATE_CONTENT",
    }


def session_fixture():
    data = fixture()
    # Two anonymous files share a day; the parent file spans two days and models.
    zero = {**data["rows"][1], "role": None, "model": "zero-synthetic",
            "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "cost": 0}
    data["rows"].append(zero)
    private = {key: "PRIVATE_" + key for key in (
        "id", "sessionId", "sessionPath", "path", "cwd", "title", "prompt", "messages",
        "threads", "task", "stableKey", "key")}
    def daily(row):
        return {"day": row["day"], "models": [{**row, **private}], **private}
    data["sessions"] = [
        {"startDay": "2026-06-01", "endDay": "2026-06-02", "role": None, "name": None,
         "days": [daily(data["rows"][0]), daily(zero)], **private, "sessionId": None},
        {"startDay": "2026-06-02", "endDay": "2026-06-02", "role": "worker", "name": None,
         "days": [daily(data["rows"][1])], **private, "sessionId": None},
    ]
    return data


def drilldown_fixture():
    data = session_fixture()
    # Keep equal anonymous files separate, plus model switches within a multiday file.
    data["rows"].append(deepcopy(data["rows"][1]))
    data["sessions"].append(deepcopy(data["sessions"][1]))
    huge = {**data["rows"][0], "day": "2026-06-02", "provider": "broker +β&%?#"}
    other_model = {**huge, "model": "other", "input": 17, "output": 0,
                   "cacheRead": 0, "cacheWrite": 0, "cost": 0.5}
    other_provider = {**other_model, "provider": "other-provider",
                      "model": huge["model"], "input": 19, "cost": 0.75}
    for row in (huge, other_model, other_provider):
        data["rows"].append(row)
        data["sessions"][0]["days"][1]["models"].append(deepcopy(row))
    return data


def session_filter_fixture():
    data = fixture()
    data["rows"], data["sessions"] = [], []

    def model(read=0, input=0, write=0, output=0, provider="synthetic", name="target"):
        return {"provider": provider, "model": name, "input": input, "output": output,
                "cacheRead": read, "cacheWrite": write, "cost": 0.125}

    def session(name, models, earlier=None, role=None):
        days = [{"day": "2026-06-02", "models": models}]
        if earlier is not None:
            days.insert(0, {"day": "2026-06-01", "models": earlier})
        data["sessions"].append({"name": name, "sessionId": None, "role": role, "startDay": days[0]["day"],
                                 "endDay": days[-1]["day"], "days": days})
        for day in days:
            data["rows"].extend({**item, "day": day["day"], "role": role} for item in day["models"])

    session("Alpha spanning", [model(95, 5)], earlier=[model(99, 1)])
    session("Beta equal", [model(95, 3, 2, 10000)])
    session("Alpha high", [model(96, 1, 3, 10000)])
    session("Alpha decimal", [model(955, 25, 20)])
    scale = 10**18
    session("Above rounding", [model(95 * scale + 1, 5 * scale - 1)])
    session("Below rounding", [model(95 * scale - 1, 5 * scale + 1)])
    session("Output only", [model(output=7)])
    session("No cache", [model(input=100)])
    session("Perfect", [model(100, output=999)])
    session(None, [model(96, 4)])
    session(None, [model(97, 3)], role="worker")
    session("Split aggregate", [model(95), model(input=5, name="other")])
    session("Label scope", [model(99, 1, provider="provider +β&%?#"),
                            model(input=1000, name="other")])
    return data


SESSION_FILTER_CONTROLS = r"""
const view = () => document.querySelector('.sessions-view');
const control = label => [...view().querySelectorAll('.session-controls label')].find(node => node.firstChild.textContent === label)?.querySelector('input');
const enter = async (label, value) => {
  const node = control(label); check(!!node, 'Missing Sessions control: ' + label);
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(node, value);
  node.dispatchEvent(new Event('input', {bubbles: true})); await pause();
};
const search = value => enter('Session name', value);
const threshold = value => enter('Cache-read share above (%)', value);
const clear = async () => { view().querySelector('.session-controls button').click(); await pause(); };
const count = () => Number(view().querySelector('.sessions-count strong')?.textContent);
const groups = () => [...view().querySelectorAll('.session-day-group')];
const group = day => groups().find(node => node.querySelector('time').dateTime === day);
const owners = day => [...group(day).querySelectorAll('.session-owner')].map(node => node.textContent);
const openGroups = async () => {
  for (const node of groups()) if (!node.open) { node.querySelector('summary').click(); await pause(); }
};
const metrics = node => [...node.querySelectorAll(':scope > .session-metrics dd')].map(item => item.textContent).join('|');
const comparison = () => {
  const node = view().querySelector('.session-reconciliation');
  check(node.querySelector('h2').textContent.includes('Unfiltered by name/share') && node.textContent.includes('need not equal'), 'Row comparison implies subset reconciliation');
  check(!node.querySelector('[role="alert"]') && !node.textContent.includes('Displayed contributions match'), 'False subset discrepancy or match');
};
"""


def stats_fixture():
    data = session_fixture()
    base = 9007199254740993
    def row(day, amount, cost=0, role=None):
        return {"day": day, "provider": "synthetic", "model": "stats-model", "role": role,
                "input": amount, "output": 0, "cacheRead": 0, "cacheWrite": 0, "cost": cost}
    data["rows"] = [
        row("2023-03-05", 10**100, 99),  # Before the first Monday: excluded.
        row("2023-03-06", 5, 0.125),
        row("2023-12-31", 2), row("2024-01-01", 3),
        row("2024-02-20", base), row("2024-02-21", base + 1),
        row("2024-02-22", base * 2 + 1), row("2024-02-23", base * 3 + 1),
        row("2024-02-24", base * 4),
        {**row("2024-02-29", base, 0.25), "output": 3, "cacheRead": 4, "cacheWrite": 2},
        row("2024-02-29", 11, 0.5, "worker"),  # Aggregate same day across owners.
        row("2024-03-01", 0),
        row("2024-03-02", 10**100, 99),  # Inside grid but after snapshot: future.
        row("2024-03-04", 10**100, 99),  # After final Sunday: excluded.
    ]
    # Deliberately unrelated valid summaries must never enter Stats accounting.
    data["stats"].update(asOf="2024-03-01T00:15:00+14:00", timezone="Pacific/Kiritimati",
                         oldestDay="2023-03-05", newestDay="2024-03-04", future=2)
    return data


def invalid_session_cases(source, wire=False):
    """Mutate only summaries so row validation cannot mask boundary failures."""
    model = ("sessions", 0, "days", 0, "models", 0)
    session = ("sessions", 0)
    day = (*session, "days", 0)
    cases = [
        (("sessions",), None), (("sessions",), {}), (("sessions",), [None]),
        ((*session, "startDay"), "2026-02-30"), ((*session, "endDay"), "2026-6-02"),
        ((*session, "startDay"), "0000-01-01"), ((*session, "endDay"), 20260602),
        ((*session, "startDay"), "2026-06-03"), ((*session, "endDay"), "2026-06-01"),
        ((*session, "startDay"), "2026-05-31"), ((*session, "endDay"), "2026-06-03"),
        ((*session, "role"), "unknown"), ((*session, "role"), "sdd-apply"),
        ((*session, "role"), True), ((*session, "role"), []),
        *(((*session, "name"), value) for value in (True, 42, [], {})),
        *(((*session, "sessionId"), value) for value in
          (True, 42, [], {}, "", "x" * 129, " id", "id ", "id\n", "id\r", "id\t",
           "id/path", "id\\path", "../id", "_id", ".id", "-id", "id:part",
           "id<script>", "id&value", "idβ", "id\u202e", "id\x00")),
        ((*session, "days"), []), ((*session, "days"), {}), ((*session, "days"), [None]),
        ((*day, "day"), "2026-02-30"), ((*day, "day"), "2026-06-01T00:00:00Z"),
        ((*day, "models"), []), ((*day, "models"), {}), ((*day, "models"), [None]),
        ((*model, "provider"), []), ((*model, "model"), None),
        ((*model, "input"), "-1" if wire else -1), ((*model, "output"), True),
        ((*model, "cacheRead"), 1.5), ((*model, "cacheWrite"), 42 if wire else "42"),
        ((*model, "cost"), -1), ((*model, "cost"), True), ((*model, "cost"), "1"),
    ]
    if wire:
        cases.extend([((*session, "name"), value) for value in
                      ("", " ", " leading", "trailing ", "x" * 201, "bad\nname", "bad\u202ename")])
        cases.extend([((*model, "input"), value) for value in
                      ("01", "1e3", "1.0", "+1", "", "1\n", " 1", "1\t")])
    else:
        cases.extend([((*model, "cost"), value) for value in (float("nan"), float("inf"), 10**400)])
    days = source["sessions"][0]["days"]
    cases.extend([((*session, "days"), [days[1], days[0]]),
                  ((*session, "days"), [days[0], days[0], days[1]])])
    for path, value in cases:
        mutated = deepcopy(source)
        target = mutated
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        yield f"{path}: {value}", mutated
    for path in (("sessions",), (*session, "role"), (*session, "name"), (*session, "sessionId"), (*session, "days"),
                 (*session, "startDay"), (*session, "endDay"), (*day, "models"),
                 (*day, "day"), *( (*model, key) for key in
                     ("provider", "model", "input", "output", "cacheRead", "cacheWrite", "cost"))):
        mutated = deepcopy(source)
        target = mutated
        for key in path[:-1]:
            target = target[key]
        del target[path[-1]]
        yield f"missing {path}", mutated


def company_fixture():
    data = fixture()
    def row(day, provider, model, amount, output=0, read=0, write=0, role=None):
        return {"day": day, "provider": provider, "model": model, "role": role,
                "input": amount, "output": output, "cacheRead": read,
                "cacheWrite": write, "cost": 0, "company": "PRIVATE_INJECTED"}
    data["rows"] = [
        row("2026-01-31", "openai", "gpt-synthetic", 10, 5, 30, 10),
        row("2026-02-01", "openrouter", "openai/gpt-synthetic", 20, 7, role="worker"),
        row("2026-02-02", "</script><img src=x onerror=alert(1)>",
            "gpt-5-<img src=x onerror=alert(1)>", 0, 3),
        row("2026-02-02", "openai", "unknown/gpt-synthetic", 999999),
        row("2026-02-02", "broker", "claude-synthetic", 20),
        row("2026-02-02", "broker", "gemini-synthetic", 30),
        row("2026-02-02", "broker", "grok-synthetic", 40),
        row("2026-02-02", "broker", "qwen-synthetic", 10),
    ]
    data["stats"].update(oldestDay="2026-01-31", newestDay="2026-02-02")
    return data


def agent_fixture():
    data = fixture()
    data["roles"] = ["worker", "worker-extra", "unused", "verify #β%"]
    def row(day, provider, model, role, amount, output=0, read=0, write=0, cost=0):
        return {"day": day, "provider": provider, "model": model, "role": role,
                "input": amount, "output": output, "cacheRead": read,
                "cacheWrite": write, "cost": cost}
    data["rows"] = [
        row("2026-01-31", "openai", "gpt-event", "worker", 10, 5, 30, 10, 0.125),
        row("2026-02-01", "broker", "gpt-event", "worker", 20, 7, cost=0.25),
        row("2026-02-02", "broker", "claude-event", "worker", 0, 3, cost=0.5),
        row("2026-02-02", "openai", "gpt-event", None, 1000),
        row("2026-02-02", "openai", "gpt-event", "worker-extra", 2000),
        row("2026-02-02", "", "", "verify #β%", 0, 4),
    ]
    # Deliberate decoys: only per-event row metadata belongs in the wire contract.
    data["task"] = {"agent": "worker", "model": "PRIVATE_LAUNCH_MODEL", "tokens": 999999}
    data["rows"][0]["tokens"] = 999999
    data["stats"].update(oldestDay="2026-01-31", newestDay="2026-02-02")
    return data


def agent_panels_fixture():
    data = agent_fixture()
    data["roles"] += ["explore", "verify"]
    base = dict(data["rows"][0], input=0, output=0, cacheRead=0, cacheWrite=0, cost=0)
    huge = 10**22
    data["rows"] = [dict(base, day=(datetime(2026, 1, 1) + timedelta(days=i)).date().isoformat(),
                         model="gpt-6-sol", input=1, cost=0.125) for i in range(40)]
    data["rows"] += [
        dict(base, day="2026-01-01", model="gpt-6-sol", cacheWrite=huge),
        dict(base, day="2026-01-01", model="gpt-6-astra", output=huge + 41, cost=2),
        # Classify from actual model, not the misleading direct provider.
        dict(base, day="2026-02-28", provider="openai", model="anthropic/claude-synthetic", input=huge, cost=3),
        dict(base, day="2026-02-28", provider="openai", model="deepseek/deepseek-synthetic", input=huge, cost=4),
        dict(base, day="2026-01-01", provider="a-broker", model="tied", cacheRead=huge, cost=5),
        dict(base, day="2026-01-01", provider="b-broker", model="tied", input=huge, cost=6),
        dict(base, day="2026-01-01", provider="c-broker", model="gpt-6-sol", input=20, cost=7),
        dict(base, day="2026-01-01", provider="d-broker", model="unknown", input=19),
        dict(base, day="2026-01-01", provider="e-broker", model="unknown", input=18),
        dict(base, day="2026-01-01", provider="", model="", output=17),
        dict(base, day="2025-12-31", provider="zero", model="zero", input=0),
        dict(base, day="2026-01-01", model="gpt-6-sol", role=None, input=huge * 100, cost=999),
        dict(base, day="2026-01-01", model="gpt-6-sol", role="worker-extra", input=huge * 100, cost=999),
        dict(base, day="2026-01-01", model="gpt-6-sol", role="explore", input=5, output=100, cacheRead=95),
        dict(base, day="2026-02-01", model="gpt-6-sol", role="explore", cacheWrite=100),
        dict(base, day="2026-01-01", model="plain", role="explore", input=8),
        dict(base, day="2026-01-01", model="gpt-6-sol", role="verify", cacheWrite=50, cacheRead=50),
        dict(base, day="2026-01-01", model="output-only", role="verify", output=200),
        dict(base, day="2026-01-01", model="zero", role="verify"),
    ]
    data["rows"].reverse()  # The chart must sort, not trust source iteration order.
    data["stats"].update(asOf="2026-02-01T00:30:00+14:00", timezone="Pacific/Kiritimati",
                         oldestDay="2025-12-31", newestDay="2026-02-28", future=9)
    return data


AGENT_PANEL_CHECKS = r"""
window.location.hash = '#/agents/worker'; await pause();
const snapshot = JSON.parse(document.getElementById('usage-data').textContent);
const select = label => [...document.querySelectorAll('.usage-filters label')].find(node => node.firstChild.textContent === label).querySelector('select');
const choose = async (label, value) => { const node = select(label); node.value = value; node.dispatchEvent(new Event('change', {bubbles: true})); await pause(); };
const tokens = row => ['input', 'output', 'cacheRead', 'cacheWrite'].reduce((total, key) => total + BigInt(row[key]), 0n);
const number = value => BigInt(value.replaceAll(',', '').replace('tokens', '').trim());
const selectedRows = () => snapshot.rows.filter(row => row.role === document.querySelector('h1').textContent && (!select('Provider').value || JSON.stringify(row.provider) === select('Provider').value) && (!select('Model').value || JSON.stringify(row.model) === select('Model').value));
const start = (day, period) => {
  if (period === 'month') return day.slice(0, 7);
  if (period === 'day') return day;
  const date = new Date(day + 'T00:00:00Z'); date.setUTCDate(date.getUTCDate() - (date.getUTCDay() + 6) % 7);
  return date.toISOString().slice(0, 10);
};
const keyOrder = (a, b) => a < b ? -1 : a > b ? 1 : 0;
const mode = async value => { [...document.querySelectorAll('.agent-panels .ranking-switch button')].find(node => node.textContent === value).click(); await pause(); };
const expanded = () => document.querySelector('.agent-panels .model-expansion:not([hidden])');
const visibleShare = node => node?.querySelector(':scope > [aria-hidden="true"]')?.textContent;
const checkShare = (node, expected) => {
  check(visibleShare(node) === expected, 'Visible cache share is not the bare percentage/N/A');
  const description = node.querySelector('.visually-hidden');
  check(description?.textContent.includes('Cache-read share: ' + expected), 'Cache percentage lost accessible meaning');
  check(getComputedStyle(description).position === 'absolute' && description.getBoundingClientRect().width <= 1 && getComputedStyle(description).clipPath !== 'none', 'Cache label still visibly rendered');
  if (expected === 'N/A') check(description.textContent.includes('zero denominator'), 'N/A missing accessible denominator explanation');
  const rendered = node.cloneNode(true); rendered.querySelector('.visually-hidden').remove();
  check(rendered.textContent.trim() === expected, 'Visible cache label was not removed');
};
const checkScope = () => {
  const rows = selectedRows(), period = select('Period').value;
  const total = rows.reduce((sum, row) => sum + tokens(row), 0n);
  check(number(document.querySelector('.metric strong').textContent) === total, 'Agent cards narrowed dates or included another owner');
  const expected = new Map();
  for (const row of rows) {
    const day = start(row.day, period), bucket = expected.get(day) ?? {amount: 0n, read: 0n, denominator: 0n};
    bucket.amount += tokens(row); bucket.read += BigInt(row.cacheRead);
    bucket.denominator += BigInt(row.input) + BigInt(row.cacheRead) + BigInt(row.cacheWrite);
    expected.set(day, bucket);
  }
  const bars = [...document.querySelectorAll('.agent-panels .trend > li[data-recorded="true"]')];
  check(JSON.stringify(bars.map(node => node.querySelector('time').dateTime)) === JSON.stringify([...expected.keys()].sort()), 'Agent dropped or fabricated recorded date buckets');
  const chartDates = new Set(expected.keys());
  if (period === 'day' && chartDates.size) {
    const latest = [...chartDates].sort().at(-1);
    for (let offset = 0; offset < 7; offset++) {
      const timestamp = Date.parse(latest + 'T00:00:00Z') - offset * 86400000;
      if (timestamp >= Date.parse('0001-01-01T00:00:00Z')) chartDates.add(new Date(timestamp).toISOString().slice(0, 10));
    }
  }
  check(JSON.stringify([...document.querySelectorAll('.agent-panels .trend time')].map(node => node.dateTime)) === JSON.stringify([...chartDates].sort()), 'Daily context lost calendar days or invented weekly/monthly buckets');
  for (const gap of document.querySelectorAll('.agent-panels .trend > li[data-recorded="false"]')) {
    const date = gap.querySelector('time').dateTime;
    check(!expected.has(date) && gap.dataset.state === 'missing' && gap.textContent.includes('No recorded usage'), 'Gap claimed recorded usage');
    check(!gap.querySelector('svg, .agent-trend-cache-share, .agent-trend-token-amount') && !gap.textContent.includes('Recorded zero'), 'Gap has fake bar, tokens or cache percentage');
    check(gap.textContent.includes('Future date') === (date > snapshot.stats.asOf.slice(0, 10)), 'Missing day lost future context');
  }
  for (const bar of bars) {
    const bucket = expected.get(bar.querySelector('time').dateTime), amount = bucket.amount;
    const amountNode = bar.querySelector('.agent-trend-token-amount');
    check(amountNode.textContent === amount.toLocaleString('en-US') + ' tokens', 'Agent bucket amount/unit did not reconcile');
    const visibleAmount = amountNode.cloneNode(true); visibleAmount.querySelectorAll('.visually-hidden').forEach(node => node.remove());
    check(visibleAmount.textContent === amount.toLocaleString('en-US'), 'Trend still visibly shows the literal tokens suffix');
    const unit = amountNode.querySelector('.visually-hidden');
    check(unit.textContent === ' tokens' && getComputedStyle(unit).position === 'absolute' && unit.getBoundingClientRect().width <= 1 && getComputedStyle(unit).clipPath !== 'none', 'Trend lost accessible-only token unit');
    if (amount === 0n) check(bar.textContent.includes('Recorded zero') && bar.querySelector('.fill').getAttribute('width') === '0', 'Zero has a fabricated bar');
    const share = bucket.denominator === 0n ? 'N/A' : (Number((bucket.read * 10000n + bucket.denominator / 2n) / bucket.denominator) / 100).toFixed(2) + '%';
    const metric = bar.querySelector(':scope > span > .agent-trend-cache-share');
    checkShare(metric, share);
    const color = getComputedStyle(metric).color, modelShare = document.querySelector('.agent-panels .model-cache-share');
    check(color === 'rgb(164, 173, 191)' && getComputedStyle(metric).opacity === '1', 'Trend percentage must remain legible in the muted color, not transparent');
    if (modelShare) check(color === getComputedStyle(modelShare).color, 'Trend and model cache percentages differ in muted color');
    check(metric.getBoundingClientRect().top >= bar.querySelector('.agent-trend-token-amount').getBoundingClientRect().bottom && getComputedStyle(metric).textAlign === 'right' && metric.getBoundingClientRect().height > 0, 'Trend share not visibly beneath right-aligned tokens');
  }
  for (const title of ['Model totals', 'Provider totals']) {
    const column = title === 'Model totals' ? 3 : 1;
    check([...document.querySelectorAll(`[aria-label="${title}"] tbody tr`)].reduce((sum, row) => sum + number(row.cells[column].textContent), 0n) === total, 'Full table no longer reconciles');
  }
  const providers = document.getElementById('agent-ranking-title').textContent === 'Top providers';
  const grouped = new Map();
  for (const row of rows) {
    const key = providers ? row.provider : JSON.stringify([row.provider, row.model]);
    const entry = grouped.get(key) ?? {key, provider: row.provider, model: row.model, amount: 0n, read: 0n, denominator: 0n, companies: new Set()};
    entry.amount += tokens(row); entry.read += BigInt(row.cacheRead);
    entry.denominator += BigInt(row.input) + BigInt(row.cacheRead) + BigInt(row.cacheWrite);
    entry.companies.add(row.company ?? 'Company unavailable'); grouped.set(key, entry);
  }
  const ranked = [...grouped.values()].filter(item => !providers || item.amount > 0n).sort((a, b) => a.amount === b.amount ? keyOrder(a.key, b.key) : a.amount > b.amount ? -1 : 1).slice(0, 5);
  const entries = [...document.querySelectorAll(providers ? '.agent-panels .ranked-providers > li' : '.agent-panels .models > li')];
  check(entries.length === ranked.length && entries.length <= 5, 'Ranking cap/real rows changed');
  entries.forEach((entry, index) => {
    const item = ranked[index];
    check(number(entry.querySelector(providers ? '.model-total' : '.model-token-amount').textContent) === item.amount, 'Exact ranking lost BigInt or token category');
    if (providers) check(entry.querySelector('strong').textContent === (item.provider || '(unspecified provider)'), 'Provider ranking inferred brands or tie order');
    else {
      check(entry.querySelector('strong').textContent === (item.model || '(unspecified model)'), 'Model ranking pair/tie order wrong');
      const company = item.companies.size === 1 ? [...item.companies][0] : 'Company unavailable/mixed';
      const label = entry.querySelector('.model-toggle').getAttribute('aria-label');
      check(label.includes('Company: ' + company) && label.includes('Provider: ' + (item.provider || '(unspecified provider)')), 'Compact row lost accessible classified company/raw provider');
      check(label.includes(item.amount.toLocaleString('en-US') + ' tokens'), 'Compact model lost accessible token unit');
      check(!entry.querySelector('.model-provider, .model-company') && entry.querySelector('.model-identity').textContent === (item.model || '(unspecified model)'), 'Compact row still shows identity lines');
      const visibleTotal = entry.querySelector('.model-total').cloneNode(true);
      visibleTotal.querySelectorAll('.visually-hidden').forEach(node => node.remove());
      check(!visibleTotal.textContent.includes('tokens'), 'Compact model still shows literal token unit');
      const tableRow = [...document.querySelectorAll('[aria-label="Model totals"] tbody tr')].find(row => row.cells[0].textContent === (item.provider || '(unspecified)') && row.cells[1].textContent === (item.model || '(unspecified)'));
      check(tableRow?.cells[2].textContent === company, 'Full Agent table lost classified company or raw provider/model');
      const share = item.denominator === 0n ? 'N/A' : (Number((item.read * 10000n + item.denominator / 2n) / item.denominator) / 100).toFixed(2) + '%';
      const metric = entry.querySelector('.model-toggle .model-total > .model-cache-share');
      checkShare(metric, share);
      check(entry.querySelector('.model-toggle').getAttribute('aria-describedby').split(' ').includes(metric.querySelector('.visually-hidden').id), 'Model button omits cache semantics from accessible description');
      check(!metric.closest('.model-expansion') && metric.getBoundingClientRect().top >= entry.querySelector('.model-token-amount').getBoundingClientRect().bottom, 'Cache share hidden in disclosure or above tokens');
      check(getComputedStyle(metric).textAlign === 'right' && metric.getBoundingClientRect().height > 0, 'Cache share not visible/right-aligned');
      check(parseFloat(getComputedStyle(metric).fontSize) >= 13 && parseFloat(getComputedStyle(metric).fontSize) <= 14, 'Compact model percentage was not modestly enlarged');
    }
  });
  const notes = [...document.querySelectorAll('.agent-panels .model-empty-slot')];
  check(notes.length === (!providers && ranked.length && total > 0n ? 5 - ranked.length : 0), 'Empty notes not honest');
  check(notes.every(node => node.textContent.includes('Empty row') && !node.querySelector('button,a,summary,.model-cache-share') && !node.hasAttribute('tabindex')), 'Empty note became ranked, metric-bearing or expandable');
  if (providers) check(!document.querySelector('.agent-panels .model-cache-share'), 'Model cache share leaked into Provider mode');
  check(!document.querySelector('.agent-panels a[href*="sessions"]'), 'Agent chart implies role-scoped Sessions');
  check(!document.querySelector('.usage-buckets'), 'Agent kept old date list');
};
const checkLayout = () => {
  const trend = document.querySelector('.agent-panels .trend-panel').getBoundingClientRect();
  const ranking = document.querySelector('.agent-panels .models-panel').getBoundingClientRect();
  check(document.documentElement.scrollWidth <= innerWidth, 'Agent layout overflow');
  const region = document.querySelector('.agent-trend-content'), box = region.getBoundingClientRect();
  check(region.scrollWidth <= region.clientWidth + 1, 'Trend has horizontal clipping/overflow');
  check(region.scrollHeight <= region.clientHeight + 1, 'Trend has nested vertical overflow');
  for (const node of [region, region.parentElement, region.parentElement.parentElement]) {
    const style = getComputedStyle(node);
    check(style.overflowY === 'visible' && style.maxHeight === 'none' && style.scrollbarGutter === 'auto', 'Trend ancestor constrains page flow');
  }
  check(!region.hasAttribute('tabindex') && !region.hasAttribute('role'), 'Obsolete scrolling focus/region remains');
  check(!document.querySelector('.agent-trend-scroll'), 'Nested scroll wrapper remains');
  const rem = parseFloat(getComputedStyle(document.documentElement).fontSize);
  for (const node of region.querySelectorAll('time, .trend > li > span, .agent-trend-cache-share, .agent-trend-cache-share > [aria-hidden="true"], .agent-trend-token-amount, .agent-trend-gap, small')) {
    const rect = node.getBoundingClientRect();
    check(rect.left >= box.left - 1 && rect.right <= box.right + 1, 'Trend values are horizontally clipped');
    check(Math.abs(parseFloat(getComputedStyle(node).fontSize) - rem) < 0.01, 'Visible Agent trend row text is not 1rem');
  }
  if (innerWidth > 1050) check(trend.left < ranking.left && Math.abs(trend.top - ranking.top) <= 1 && Math.abs(trend.height - ranking.height) <= 1 && Math.abs(trend.bottom - ranking.bottom) <= 1, 'Agent desktop panels are not equal-height with aligned tops/bottoms');
  else {
    check(ranking.top >= trend.bottom && Math.abs(trend.left - ranking.left) <= 1 && ranking.height < 1600, 'Agent mobile order or natural height lost');
    check(ranking.bottom - document.querySelector('#agent-ranking-content').getBoundingClientRect().bottom < 40, 'Mobile ranking panel retains paired-height whitespace');
  }
};
const checkPageReachability = () => {
  const region = document.querySelector('.agent-trend-content');
  for (const row of region.querySelectorAll('.trend > li')) {
    window.scrollTo(0, window.scrollY + row.getBoundingClientRect().top - 16);
    const rect = row.getBoundingClientRect();
    check(rect.top >= 0 && rect.bottom <= innerHeight, 'Recorded bucket is not reachable by page scroll');
    check(region.scrollTop === 0 && region.parentElement.scrollTop === 0, 'Reaching bucket required nested scrolling');
    check(document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2)?.closest('li') === row, 'Recorded bucket is obscured or clipped');
  }
};
"""


def overview_fixture():
    data = fixture()
    def row(day, provider, model, amount, output=0, read=0, write=0, cost=0):
        return {"day": day, "provider": provider, "model": model, "role": None,
                "input": amount, "output": output, "cacheRead": read,
                "cacheWrite": write, "cost": cost}
    data["rows"] = [
        row("2023-11-01", "beta", "ancient", 9007199254740993),
        row("2025-11-30", "beta", "older", 11),
        row("2025-12-01", "beta", "older", 13),
        row("2025-12-22", "beta", "older", 17),
        row("2025-12-28", "beta", "older", 19),
        row("2025-12-29", "alpha", "shared", 10, 5, 30, 10, 0.125),
        row("2025-12-31", "beta", "shared", 20, 7, cost=0.25),
        row("2026-01-01", "alpha", "other", 0, 3, cost=0.5),
        row("2026-01-02", "alpha", "shared", 0),
        row("2026-01-04", "beta", "shared", 23),
        row("2026-01-05", "alpha", "shared", 31, cost=1),
    ]
    # The instant is January 5 in UTC; controls must use January 6 locally.
    data["stats"].update(asOf="2026-01-06T00:30:00+14:00", timezone="Pacific/Kiritimati",
                         oldestDay="2023-11-01", newestDay="2026-01-05")
    data["task"] = {"model": "PRIVATE_LAUNCH_MODEL", "tokens": 999999}
    return data


def expansion_fixture():
    data = overview_fixture()
    template = data["rows"][5]
    data["rows"].extend([
        {**template, "day": "2024-02-29", "input": 9007199254740993},
        {**template, "day": "2025-02-28", "input": 28},
        {**template, "day": "2025-04-30", "input": 30},
        {**template, "day": "2025-12-29", "role": "worker", "input": 2},
        {**template, "day": "2026-01-08", "input": 41},
        {**template, "day": "2026-01-02", "provider": "", "model": "",
         "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "cost": 0},
        {**template, "day": "2026-01-04", "provider": "broker +β&%?#",
         "model": "</script><img src=x onerror=alert(1)>", "input": 9007199254740993},
    ])
    # Synthetic measured files match the daily rows, without IDs or paths.
    data["sessions"] = [
        {"startDay": row["day"], "endDay": row["day"], "role": row["role"],
         "days": [{"day": row["day"], "models": [deepcopy(row)]}]}
        for row in data["rows"]
    ]
    for session in data["sessions"]:
        session["name"] = None
        session["sessionId"] = None
    data["stats"].update(newestDay="2026-01-08", future=1)
    return data


def custom_range_fixture():
    data = expansion_fixture()
    template = data["rows"][5]
    # Cross-month September coverage includes gaps, zero, multiple models and owners.
    for index in range(40):
        day = (datetime(2025, 8, 28) + timedelta(days=index)).date().isoformat()
        if index == 9:
            continue
        zero = index == 8
        data["rows"].append({**template, "day": day, "input": 0 if zero else index + 1,
                             "output": 0 if zero else 2, "cacheRead": 0 if zero else 3,
                             "cacheWrite": 0 if zero else 1, "cost": 0 if zero else 0.125})
        if not zero and index % 2 == 0:
            data["rows"].append({**template, "day": day, "provider": "beta",
                                 "model": "other", "role": "worker", "input": index + 100})
    data["sessions"] = [
        {"startDay": row["day"], "endDay": row["day"], "role": row["role"],
         "days": [{"day": row["day"], "models": [deepcopy(row)]}]}
        for row in data["rows"]
    ]
    for session in data["sessions"]:
        session["name"] = None
        session["sessionId"] = None
    return data


def top_models_fixture():
    data = session_fixture()  # Unrelated summaries must not affect row-based ranking.
    template = data["rows"][1]
    def row(day, provider, model, amount, **extra):
        return {**template, "day": day, "provider": provider, "model": model,
                "role": None, "input": amount, "output": 0, "cacheRead": 0,
                "cacheWrite": 0, "cost": 0, **extra}
    data["rows"] = [
        row("2026-06-17", "Zeta", "shared", 2**53 - 5, output=2, cacheRead=2, cacheWrite=1),
        row("2026-06-17", "Zeta", "shared", 1, role="worker"),
        row("2026-06-17", "Alpha", "shared", 2**53),
        row("2026-06-17", "Alpha", "daily", 40),
        row("2026-06-17", "Beta", "fourth", 40),
        row("2026-06-16", "Weekly", "week-only", 30),
        row("2026-06-01", "Monthly", "month-only", 10**20),
        row("2026-05-31", "Historical", "old-only", 10**21),
        row("2026-06-14", "Alpha", "daily", 0),
    ]
    data["stats"].update(asOf="2026-06-18T12:00:00+00:00",
                         oldestDay="2026-05-31", newestDay="2026-06-17")
    return data


def model_prices_fixture():
    data = session_fixture()  # Decoy summaries must not enter pair accounting.
    template = data["rows"][1]
    models = ["gpt-6-sol", "gpt-6-astra", "gpt-5.6-sol", "gpt-5.6-terra",
              "gpt-5.5", "gpt-6-sol-latest", "openai/gpt-6-sol", "GPT-6-SOL",
              "gpt-6-sol ", "", "__proto__", "constructor",
              '</script><img src="https://invalid.test/leak" onerror=alert(1)>']
    providers = ["openai", "openai-codex", "", "OpenAI", "openai ",
                 "openrouter", "openai-compatible", '<img src="https://invalid.test/provider">']
    data["rows"] = []
    for provider in providers:
        for index, model in enumerate(models if provider in providers[:2] else ["gpt-6-sol"]):
            data["rows"].append({**template, "provider": provider, "model": model,
                                 "day": "2026-06-02", "role": None,
                                 "input": 10**100 + index, "output": 2**53 + 1,
                                 "cacheRead": 2**53 + 3, "cacheWrite": 2**53 + 5,
                                 "cost": 0 if provider == "openai" else 0.125})
    selected = data["rows"][len(models)]  # Same SKU on another provider stays separate.
    data["rows"].extend([
        {**selected, "role": "worker", "input": 7, "output": 11,
         "cacheRead": 13, "cacheWrite": 17, "cost": 0.25},
        {**selected, "day": "2026-06-01", "input": 19, "output": 23,
         "cacheRead": 29, "cacheWrite": 31, "cost": 0.5},
        {**selected, "day": "2026-05-01", "input": 10**110, "cost": 99},
    ])
    data["stats"].update(oldestDay="2026-05-01", newestDay="2026-06-02")
    return data


def provider_fixture():
    data = session_fixture()  # Unrelated valid summaries must never enter this chart.
    base = 10**350  # Number conversion would overflow, not merely lose precision.
    template = data["rows"][1]
    def row(provider, amount, day="2026-06-02", model="shared", role=None, **extra):
        return {**template, "provider": provider, "model": model, "day": day,
                "role": role, "input": amount, "output": 0, "cacheRead": 0,
                "cacheWrite": 0, "cost": 0, **extra}
    data["rows"] = [
        row("Zeta", 4 * base, output=1, cacheRead=2, cacheWrite=1),
        row("Zeta", 2, model="child-model", role="worker"),
        row("Alpha", 4 * base + 5),  # One token below Zeta; name sort must not win.
        row('</script><img src=x onerror=alert(1)>;url(https://invalid.test/)', 3 * base),
        row("", 2 * base),
        row("Delta", base), row("Echo", base), row("Foxtrot", base),
        row("Cost only", 0, cost=999),
        row("Context only", 10**360, day="2026-06-01"),
        row("Zero only", 0, day="2026-05-31", cost=99),
        row("Outside trend window", 10**355, day="2026-05-01"),
        row("Future", 17, day="2026-06-04"),
    ]
    data["stats"].update(oldestDay="2026-05-01", newestDay="2026-06-04", future=1)
    return data


OVERVIEW_CONTROLS = r"""
const controls = document.querySelector('[aria-label="Overview filters"]');
const resetButton = controls.querySelector('button');
const reset = async () => { resetButton.click(); await pause(); };
const select = label => [...controls.querySelectorAll('label')].find(node => node.firstChild.textContent === label)?.querySelector('select');
const choose = async (label, value) => {
  const node = select(label); check(!!node, 'Missing select: ' + label);
  check([...node.options].some(option => option.value === value), 'Missing option: ' + label + '=' + value);
  node.value = value; node.dispatchEvent(new Event('change', {bubbles: true})); await pause();
};
const calendar = label => [...controls.querySelectorAll('label')].find(node => node.firstChild.textContent === label)?.querySelector('input[type="date"]');
const dayInput = () => calendar('Day');
const chooseDate = async (label, value) => {
  const node = calendar(label); check(!!node, 'Missing calendar input: ' + label);
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(node, value);
  node.dispatchEvent(new Event('input', {bubbles: true})); await pause();
};
const chooseDay = value => chooseDate('Day', value);
const pickPeriod = async label => {
  const option = [...select('Period').options].find(node => node.textContent === label);
  check(!!option, 'Missing period option: ' + label);
  await choose('Period', option.value);
  check(select('Period').selectedOptions[0].textContent === label, 'Selected period not exposed');
};
const metrics = () => [...document.querySelectorAll('.metric strong')].map(node => node.textContent);
const total = () => metrics()[0];
const scope = () => document.querySelector('.overview-scope').textContent;
const trend = () => [...document.querySelectorAll('.trend > li')];
const dates = () => trend().map(node => node.querySelector('time').dateTime);
const modelNames = () => [...document.querySelectorAll('.models strong')].map(node => node.textContent);
"""


EXPANSION_CHECKS = r"""
const snapshot = JSON.parse(document.querySelector('#usage-data').textContent);
const openPanel = () => document.querySelector('.trend-expansion:not([hidden])');
const bars = () => [...document.querySelectorAll('.trend-period')];
const barFor = day => bars().find(node => node.querySelector('time').dateTime === day);
const tokenTotal = rows => rows.reduce((sum, row) => sum + ['input', 'output', 'cacheRead', 'cacheWrite'].reduce((n, key) => n + BigInt(row[key]), 0n), 0n);
const costTotal = rows => rows.reduce((sum, row) => sum + row.cost, 0);
const filteredRows = () => snapshot.rows.filter(row => (!select('Provider').value || JSON.stringify(row.provider) === select('Provider').value) && (!select('Model').value || JSON.stringify(row.model) === select('Model').value));
const suffix = () => {
  const params = new URLSearchParams();
  for (const [key, label] of [['provider', 'Provider'], ['model', 'Model']]) {
    if (select(label).value) params.set(key, JSON.parse(select(label).value));
  }
  return params.size ? '?' + params.toString() : '';
};
const expand = async (start, end) => {
  const before = metrics().join('|') + scope() + modelNames().join('|');
  const bar = barFor(start);
  check(!!bar && bar.tagName === 'BUTTON', 'Period bar is not a native button: ' + start);
  bar.focus();
  check(document.activeElement === bar && bar.tabIndex === 0 && getComputedStyle(bar).outlineStyle !== 'none', 'Bar lacks visible keyboard focus');
  check(bar.getBoundingClientRect().height >= 44, 'Period target below 44px');
  bar.click(); await pause();
  const panel = openPanel();
  check(panel && document.querySelectorAll('.trend-expansion:not([hidden])').length === 1, 'Must expand exactly one period');
  check(bar.getAttribute('aria-expanded') === 'true' && document.getElementById(bar.getAttribute('aria-controls')) === panel, 'Disclosure relationship incorrect');
  check(panel.getAttribute('aria-labelledby') === bar.id, 'Expansion lacks accessible name');
  check(bars().filter(node => node.getAttribute('aria-expanded') === 'true').length === 1, 'Stale expanded button');
  check(metrics().join('|') + scope() + modelNames().join('|') === before, 'Expansion widened cards or top models');
  check(panel.textContent.includes('Cards and top models keep their selected range'), 'Context vs card scope hidden');
  const links = [...panel.querySelectorAll('.trend-all-sessions')];
  const count = (Date.parse(end) - Date.parse(start)) / 86400000 + 1;
  check(links.length === count, 'Incomplete calendar period');
  let summedTokens = 0n, summedCost = 0;
  for (const [index, link] of links.entries()) {
    const day = new Date(Date.parse(start) + index * 86400000).toISOString().slice(0, 10);
    const rows = filteredRows().filter(row => row.day === day);
    const amount = tokenTotal(rows), cost = costTotal(rows);
    summedTokens += amount; summedCost += cost;
    check(link.querySelector('time').dateTime === day && link.getAttribute('href') === '#/sessions/' + day, 'Date link must open unfiltered day');
    check(link.getAttribute('aria-label').includes('View sessions for ' + day), 'Daily link purpose unclear');
    const filteredLink = link.parentElement.querySelector('.trend-filtered-sessions');
    check(!!filteredLink === !!suffix(), 'Separate filtered link missing or spurious');
    const amountLink = filteredLink ?? link;
    if (filteredLink) {
      check(filteredLink.getAttribute('href') === '#/sessions/' + day + suffix() && filteredLink.textContent.includes('Filtered Sessions'), 'Filtered link lost labels or scope');
      check(link.textContent.includes('All sessions for this day') && !link.textContent.includes('tokens'), 'Filtered amount mislabeled all-day total');
      filteredLink.focus(); check(document.activeElement === filteredLink && getComputedStyle(filteredLink).outlineStyle !== 'none', 'Filtered link lacks focus');
      check(filteredLink.getBoundingClientRect().height >= 44, 'Filtered link below touch target');
    }
    if (rows.length) {
      check(amountLink.textContent.includes(amount.toLocaleString('en-US') + ' tokens') && amountLink.textContent.includes('$' + cost.toFixed(4) + ' recorded cost estimate'), 'Daily totals lost categories, precision or filters');
      if (amount === 0n) check(amountLink.textContent.includes('Recorded zero'), 'Recorded zero not identified');
    } else {
      check(amountLink.textContent.includes('No recorded usage') && !amountLink.textContent.includes('0 tokens'), 'Gap presented as recorded zero');
    }
    const future = day > snapshot.stats.asOf.slice(0, 10);
    check(amountLink.textContent.includes('Future date after snapshot') === future, 'Future status uses browser clock or UTC conversion');
    check(link.parentElement.dataset.state === (future ? 'future' : !rows.length ? 'missing' : amount === 0n ? 'zero' : 'recorded'), 'Day state incorrect');
    link.focus(); check(document.activeElement === link && getComputedStyle(link).outlineStyle !== 'none', 'Daily link focus missing');
    check(link.getBoundingClientRect().height >= 44, 'Expanded daily target below 44px');
  }
  for (const node of [bar, panel.querySelector('.trend-expansion-total')]) {
    const label = node.getAttribute('aria-label') ?? node.textContent;
    check(label.includes(summedTokens.toLocaleString('en-US') + ' tokens') || (!summedTokens && label.includes('No recorded usage')), 'Expanded sum differs from clicked bar');
    check(label.includes('$' + summedCost.toFixed(4)), 'Daily costs differ from clicked bar');
  }
  check(!document.querySelector('img') && !panel.textContent.includes('PRIVATE_'), 'Hostile/private data rendered');
  check(document.documentElement.scrollWidth <= window.innerWidth, 'Expanded period overflows viewport');
  return panel;
};
const closeExpansion = async () => {
  const panel = openPanel(), bar = document.getElementById(panel.getAttribute('aria-labelledby'));
  const close = panel.querySelector('.trend-close');
  close.focus(); check(document.activeElement === close && getComputedStyle(close).outlineStyle !== 'none', 'Close lacks visible focus');
  check(close.getBoundingClientRect().height >= 44, 'Close target below 44px');
  close.click(); await pause();
  check(!openPanel() && bar.getAttribute('aria-expanded') === 'false' && document.activeElement === bar, 'Close must collapse and return focus');
};
"""


PROVIDER_CHECKS = OVERVIEW_CONTROLS + EXPANSION_CHECKS + r"""
const providerPanel = () => document.querySelector('[aria-labelledby="providers-title"]');
const providerItems = () => [...providerPanel().querySelectorAll('.provider-legend li')];
const checkProviders = (start, end) => {
  const rows = filteredRows().filter(row => !start || row.day >= start && row.day <= end);
  const grouped = new Map();
  for (const row of rows) grouped.set(row.provider, (grouped.get(row.provider) ?? 0n) + tokenTotal([row]));
  const sorted = [...grouped].filter(([, amount]) => amount > 0n).sort(([a, x], [b, y]) => x === y ? (a < b ? -1 : a > b ? 1 : 0) : x > y ? -1 : 1);
  const expected = sorted.slice(0, 5).map(([name, amount]) => [name.trim() ? name : '(unspecified provider)', amount]);
  if (sorted.length > 5) expected.push(['Other (' + (sorted.length - 5) + (sorted.length === 6 ? ' provider)' : ' providers)'), sorted.slice(5).reduce((sum, [, n]) => sum + n, 0n)]);
  const amount = tokenTotal(rows), panel = providerPanel(), items = providerItems();
  check(total() === amount.toLocaleString('en-US'), 'Cards do not match selected daily rows');
  check(items.length === expected.length, 'Provider count or positive-volume gating incorrect');
  check(panel.getAttribute('aria-describedby') === 'providers-description' && document.getElementById('providers-description').textContent.includes('Missing records'), 'Provider accounting description unavailable');
  check(!panel.querySelector('img, a, style') && !panel.textContent.includes('PRIVATE_'), 'Provider labels created markup or leaked private summaries');
  if (!amount) {
    check(!panel.querySelector('[role="img"]') && !panel.textContent.includes('100.00%'), 'Empty selection rendered fake chart');
    check(panel.textContent.includes('No positive token usage') && panel.textContent.includes(rows.length ? 'Recorded zero tokens' : 'No recorded usage'), 'Missing and zero provider states conflated');
    return;
  }
  check(panel.querySelector('.provider-total strong').textContent === total(), 'Chart and card scope differ or summaries double counted');
  check(panel.querySelector('[role="img"]').getAttribute('aria-label').includes(amount.toLocaleString('en-US')), 'Doughnut lacks accessible total');
  check(panel.querySelector('.provider-legend').getAttribute('aria-label') === 'Provider token shares', 'Legend lacks accessible name');
  let sum = 0n;
  items.forEach((node, index) => {
    const [name, n] = expected[index]; sum += n;
    check(node.querySelector('.provider-name').textContent === name, 'Provider exact rank/name/Other incorrect');
    check(node.querySelector('.provider-amount').textContent === n.toLocaleString('en-US') + ' tokens', 'Provider amount lost BigInt precision');
    const basis = (n * 10000n + amount / 2n) / amount;
    const share = (basis / 100n).toString() + '.' + (basis % 100n).toString().padStart(2, '0') + '%';
    check(node.querySelector('.provider-share').textContent === share, 'Provider percentage incorrect');
    check(!!node.querySelector('.provider-leader') === (index === 0), 'Most used provider not singled out');
  });
  check(sum === amount, 'Legend does not reconcile to cards');
  const arcs = [...panel.querySelectorAll('[stroke-dasharray]')];
  check(arcs.length === expected.length, 'Doughnut segment count mismatch');
  const lengths = arcs.map(node => Number(node.getAttribute('stroke-dasharray').split(' ')[0]));
  check(lengths.every(n => Number.isFinite(n) && n >= 0 && n <= 100) && Math.abs(lengths.reduce((a, b) => a + b, 0) - 100) < 0.00001, 'Unbounded or incomplete BigInt ring geometry');
  check(arcs.every(node => /^#[a-f0-9]{6}$/.test(node.getAttribute('stroke'))), 'Unsafe provider interpolation into colors');
};
"""


TOP_MODELS_CHECKS = PROVIDER_CHECKS + r"""
const modelPanel = () => document.querySelector('[aria-labelledby="models-title"]');
const checkModelLayout = () => {
  const panel = modelPanel(), trendPanel = document.querySelector('[aria-labelledby="trend-title"]');
  const description = panel.getAttribute('aria-description') ?? '';
  for (const text of [document.querySelector('.overview-scope strong').textContent,
    'Up to five provider/model pairs', 'same selected rows', 'provider/model filters', 'Pi parent and linked child usage']) {
    check(description.includes(text), 'Model accessible scope/cap missing: ' + text);
  }
  check(!panel.querySelector(':scope > p:not(.empty)'), 'Visible model scope paragraph remains');
  check(!panel.innerText.includes('Up to five') && !panel.innerText.includes('Pi parent and linked child usage'), 'Visible footer was replaced with more copy');
  check(document.querySelectorAll('[aria-labelledby="trend-title"]').length === 1 && document.querySelectorAll('[aria-labelledby="providers-title"]').length === 1, 'Chart duplicated');
  check(trendPanel.nextElementSibling === providerPanel() && providerPanel().nextElementSibling === panel, 'Semantic chart order changed');
  const a = trendPanel.getBoundingClientRect(), b = providerPanel().getBoundingClientRect(), c = panel.getBoundingClientRect();
  check(b.top >= a.bottom && Math.abs(b.left - a.left) <= 1 && Math.abs(b.width - a.width) <= 1, 'Provider not below trend in same column');
  if (innerWidth > 1050) {
    check(Math.abs(c.height - a.height) <= 1 && Math.abs(c.top - a.top) <= 1, 'Actual desktop panel heights do not match');
    check(c.left >= a.right && c.bottom < b.bottom, 'Models aligned to combined trend/provider column');
  } else {
    check(c.top >= b.bottom && Math.abs(c.left - a.left) <= 1, 'Mobile order must be trend, provider, models');
    const contentBottom = panel.lastElementChild.getBoundingClientRect().bottom;
    check(c.bottom - contentBottom <= parseFloat(getComputedStyle(panel).paddingBottom) + 20, 'Mobile models retain forced blank height');
  }
  check(document.documentElement.scrollWidth <= innerWidth, 'Models layout overflows viewport');
};
const checkModels = (start, end, count) => {
  checkProviders(start, end);
  const rows = filteredRows().filter(row => !start || row.day >= start && row.day <= end);
  const groups = new Map();
  for (const row of rows) {
    const key = JSON.stringify([row.provider, row.model]);
    groups.set(key, (groups.get(key) ?? 0n) + tokenTotal([row]));
  }
  const ranked = [...groups].sort(([a, x], [b, y]) => x === y ? a.localeCompare(b) : x > y ? -1 : 1).slice(0, 5);
  const items = [...modelPanel().querySelectorAll('.models > li')];
  check(items.length === count && items.length === ranked.length, 'Invented, missing, or uncapped model pairs');
  items.forEach((node, index) => {
    const [key, amount] = ranked[index], [provider, model] = JSON.parse(key);
    check(node.querySelector('strong').textContent === model && node.querySelector('.model-identity > span').textContent === provider, 'Models include out-of-range pairs or incorrect BigInt/tie order');
    check(node.querySelector('.rank').textContent === String(index + 1), 'Model rank label incorrect');
    check(node.querySelector('.model-token-amount').textContent === amount.toLocaleString('en-US'), 'Model totals include extra dates/summaries or lose precision');
  });
  const emptySlots = [...modelPanel().querySelectorAll('.model-empty-slot')];
  const weekly = ['week', 'Last Week'].includes(select('Period').value);
  const daily = ['day', 'Today', 'Yesterday'].includes(select('Period').value) && start === end;
  const expectedSlots = (daily || weekly) && count > 0 && tokenTotal(rows) > 0n ? 5 - count : 0;
  check(emptySlots.length === expectedSlots, 'Empty rows do not complete the eligible selected ranking to five visual rows');
  for (const slot of emptySlots) {
    const label = 'Empty row · No additional model in this selected ' + (weekly ? 'weekly range' : 'day') + ' and filters';
    check(slot.textContent === label && slot.getAttribute('role') === 'note', 'Empty row lacks truthful visible scope');
    check(slot.getAttribute('aria-label') === label + '. ' + document.querySelector('.overview-scope strong').textContent + '. Not a ranked model.', 'Empty row lacks accessible scope and non-model semantics');
    check(!slot.closest('ol') && !slot.querySelector('.rank, .model-total, strong, a, svg, time'), 'Empty row pretends to be a ranked model or measured usage');
    check(slot.checkVisibility({visibilityProperty: true}) && !/tokens|cost|\$|\d/.test(slot.textContent), 'Empty row is hidden or claims measurements/rank');
  }
  if (innerWidth > 1050 && count) {
    const visualRows = [...items, ...emptySlots].map(node => node.getBoundingClientRect());
    check(Math.max(...visualRows.map(rect => rect.height)) - Math.min(...visualRows.map(rect => rect.height)) <= 1, 'Desktop interior rows are not evenly distributed');
    const panel = modelPanel(), bottom = panel.getBoundingClientRect().bottom;
    check(bottom - visualRows.at(-1).bottom <= parseFloat(getComputedStyle(panel).paddingBottom) + 9, 'Desktop rows leave unused space below ranking: ' + select('Period').value + ' ' + start + ' gap=' + (bottom - visualRows.at(-1).bottom));
  }
  checkModelLayout();
};
"""


PROVIDER_RANKING_CHECKS = TOP_MODELS_CHECKS + r"""
const rankingButtons = () => [...modelPanel().querySelectorAll('.ranking-switch button')];
const switchRanking = async mode => {
  const panel = modelPanel(), buttons = rankingButtons();
  const before = metrics().join('|') + providerPanel().innerHTML + document.querySelector('.trend-panel').innerHTML + location.href +
    [...controls.querySelectorAll('input, select')].map(node => node.value).join('|');
  const height = panel.getBoundingClientRect().height;
  const button = buttons.find(node => node.textContent === mode);
  check(buttons.length === 2 && button?.tagName === 'BUTTON' && button.type === 'button' && button.tabIndex === 0, 'Ranking switch is not two native keyboard buttons');
  check(button.parentElement.getAttribute('role') === 'group' && button.parentElement.getAttribute('aria-label') === 'Ranking view', 'Switch has no accessible group name');
  check(document.getElementById(button.getAttribute('aria-controls')) === panel.querySelector('.ranking-content'), 'Switch controls missing content');
  button.focus();
  check(document.activeElement === button && getComputedStyle(button).outlineStyle !== 'none', 'Ranking button lacks visible keyboard focus');
  check(button.getBoundingClientRect().height >= 32, 'Ranking button target too small');
  button.click(); await pause();
  check(document.activeElement === button, 'Switch stole keyboard focus');
  check(buttons.every(node => node.getAttribute('aria-pressed') === String(node === button)), 'Pressed state does not expose the active mode');
  check(document.getElementById('models-title').textContent === 'Top ' + mode.toLowerCase(), 'Mode title inaccessible or stale');
  const views = [...panel.querySelectorAll('.ranking-view')];
  check(views.filter(node => !node.hidden && !node.inert).length === 1 && views.filter(node => node.checkVisibility({visibilityProperty: true})).length === 1, 'Inactive ranking remains visible or interactive');
  check(before === metrics().join('|') + providerPanel().innerHTML + document.querySelector('.trend-panel').innerHTML + location.href +
    [...controls.querySelectorAll('input, select')].map(node => node.value).join('|'), 'Presentation switch mutated cards/donut/trend/filters/route');
  if (innerWidth > 1050) check(Math.abs(panel.getBoundingClientRect().height - height) <= 1, 'Switch changed desktop panel height');
};
const checkProviderRanking = (start, end) => {
  checkProviders(start, end);
  const panel = modelPanel(), rows = filteredRows().filter(row => !start || row.day >= start && row.day <= end);
  const groups = new Map();
  for (const row of rows) groups.set(row.provider, (groups.get(row.provider) ?? 0n) + tokenTotal([row]));
  const ranked = [...groups].filter(([, n]) => n > 0n).sort(([a, x], [b, y]) => x === y ? (a < b ? -1 : a > b ? 1 : 0) : x > y ? -1 : 1);
  const items = [...panel.querySelectorAll('.ranked-providers > li')];
  check(items.length === Math.min(5, ranked.length), 'Provider ranking invented, omitted or uncapped rows');
  items.forEach((node, index) => {
    const [name, amount] = ranked[index];
    check(node.querySelector('strong').textContent === (name.trim() ? name : '(unspecified provider)'), 'Provider ranking inferred brands or changed raw-name ties');
    check(node.querySelector('.rank').textContent === String(index + 1), 'Provider ordinal incorrect');
    check(node.querySelector('.model-total').textContent === amount.toLocaleString('en-US') + 'tokens', 'Provider total lost categories, selected scope or BigInt precision');
    check(node.checkVisibility({visibilityProperty: true}), 'Real provider row hidden');
  });
  const description = panel.getAttribute('aria-description');
  for (const text of [document.querySelector('.overview-scope strong').textContent, 'Up to five recorded providers', 'same selected rows', 'provider/model filters', 'raw-name-order ties']) {
    check(description.includes(text), 'Provider scope/cap/tie description missing: ' + text);
  }
  check(description.includes(ranked.length > 5 ? (ranked.length - 5) + ' additional positive-volume providers omitted' : 'unused ranks are omitted'), 'Omitted provider ranks not disclosed accessibly');
  const active = panel.querySelector('.ranking-view:not([hidden])');
  check(!active.querySelector('.model-empty-slot, .models') && !active.textContent.includes('Other ('), 'Providers acquired fake competitors/model placeholders/Other rank');
  if (!ranked.length) check(active.textContent.includes(rows.length ? 'Recorded zero tokens' : 'No recorded usage'), 'Provider missing and recorded-zero states conflated');
  check(!panel.querySelector('img, [style]') && !panel.textContent.includes('PRIVATE_'), 'Ranking leaked private fields or created hostile markup');
  const trendBounds = document.querySelector('.trend-panel').getBoundingClientRect(), bounds = panel.getBoundingClientRect();
  if (innerWidth > 1050) {
    check(Math.abs(bounds.top - trendBounds.top) <= 1 && Math.abs(bounds.height - trendBounds.height) <= 1, 'Provider ranking height differs from trend');
    check(bounds.bottom < providerPanel().getBoundingClientRect().bottom, 'Ranking sized to combined left column');
  } else {
    check(bounds.top >= providerPanel().getBoundingClientRect().bottom && providerPanel().getBoundingClientRect().top >= trendBounds.bottom, 'Mobile order changed');
    check(bounds.bottom - active.getBoundingClientRect().bottom <= parseFloat(getComputedStyle(panel).paddingBottom) + 10, 'Mobile ranking retained hidden model height');
  }
  check(panel.scrollWidth <= panel.clientWidth && document.documentElement.scrollWidth <= innerWidth, 'Long provider/BigInt overflows viewport');
};
"""


MODEL_DETAILS_CHECKS = OVERVIEW_CONTROLS + EXPANSION_CHECKS + r"""
const modelToggles = () => [...document.querySelectorAll('.model-toggle')];
const modelDetail = () => document.querySelector('.model-expansion:not([hidden])');
const modelButton = (provider, model) => modelToggles().find(node =>
  node.querySelector('.model-identity > span').textContent === (provider || '(unspecified provider)') &&
  node.querySelector('strong').textContent === (model || '(unspecified model)'));
const checkModelDetail = async (provider, model, start = '2026-06-02', end = start) => {
  const button = modelButton(provider, model);
  check(!!button && button.tagName === 'BUTTON' && button.type === 'button', 'Real model has no native toggle');
  const before = metrics().join('|') + location.href + document.querySelector('.provider-panel').innerHTML;
  button.focus(); button.click(); await pause();
  const panel = modelDetail();
  check(panel && button.getAttribute('aria-expanded') === 'true' && document.getElementById(button.getAttribute('aria-controls')) === panel, 'Model disclosure relationship missing');
  check(panel.getAttribute('aria-labelledby') === button.id && document.activeElement === button, 'Model focus or accessible name lost');
  check(getComputedStyle(button).outlineStyle !== 'none' && button.getBoundingClientRect().height >= 44, 'Toggle focus or touch target missing');
  check(document.querySelectorAll('.model-expansion:not([hidden])').length === 1, 'More than one pair expanded');
  const rows = snapshot.rows.filter(row => row.provider === provider && row.model === model && (!start || row.day >= start && row.day <= end));
  check(button.querySelector('.model-token-amount').textContent === tokenTotal(rows).toLocaleString('en-US'), 'Ranking lost selected pair totals or BigInt precision');
  const description = document.getElementById(panel.getAttribute('aria-describedby'));
  check(description && button.getAttribute('aria-describedby') === description.id && description.classList.contains('visually-hidden'), 'Provenance is not linked accessibly to expansion and toggle');
  const style = getComputedStyle(description), rect = description.getBoundingClientRect();
  check(style.position === 'absolute' && style.clipPath === 'inset(50%)' && rect.width <= 1 && rect.height <= 1, 'Caveat description is visible');
  check(!description.hidden && style.display !== 'none' && style.visibility !== 'hidden', 'Provenance was removed from accessibility tree');
  const rates = panel.querySelector('.model-rates');
  check(panel.children.length === 2 && panel.firstElementChild === rates && panel.lastElementChild === description, 'Expansion contains ancillary visible content');
  check(rates.children.length === 3 && [...rates.children].every(node => node.children.length === 2 && node.firstElementChild.tagName === 'DT' && node.lastElementChild.tagName === 'DD'), 'Expansion must contain only three label/value fields');
  check([...rates.querySelectorAll('dt')].map(node => node.textContent).join('|') === 'Input · API reference (USD / 1M tokens)|Output · API reference (USD / 1M tokens)|Recorded cost estimate (USD)', 'Visible reference/recorded labels lost category/unit');
  check([...rates.querySelectorAll(':scope > div:not(.model-recorded-cost) dd')].every(node => /^(\$\d+|Unavailable)$/.test(node.textContent)), 'Visible reference fields contain commentary or fabricated spend');
  check(rates.querySelector('.model-recorded-cost dd').textContent === '$' + costTotal(rows).toFixed(4), 'Recorded cost is not the selected provider/model pair total');
  for (const phrase of ['API reference, not actual Pi/Codex spend', 'Historical applicability unknown', 'no per-request tier or effective tariff',
    'no per-component dollar spend', 'not an invoice', 'zero may mean unavailable metadata', '>272K', 'processing tier', 'region', 'Batch', 'subscriptions', 'selected dates', 'subscription pricing']) {
    check(description.textContent.includes(phrase), 'Missing accessible cost/reference caveat: ' + phrase);
  }
  check(before === metrics().join('|') + location.href + document.querySelector('.provider-panel').innerHTML, 'Expansion changed data, route or donut');
  check(!panel.querySelector('a, button, summary, h3, img, style, script') && !document.querySelector('#root').textContent.includes('PRIVATE_'), 'Reference introduced ancillary controls, remote navigation, markup or private fields');
  const bounds = document.querySelector('.models-panel').getBoundingClientRect(), trendBounds = document.querySelector('.trend-panel').getBoundingClientRect();
  if (innerWidth > 1050) check(Math.abs(bounds.height - trendBounds.height) <= 1 && Math.abs(bounds.top - trendBounds.top) <= 1, 'Expanded desktop panel height differs from trend');
  else check(bounds.bottom - document.querySelector('.ranking-content').getBoundingClientRect().bottom < 30, 'Mobile expansion retains forced whitespace');
  check(document.documentElement.scrollWidth <= innerWidth && panel.scrollWidth <= panel.clientWidth, 'Expanded details overflow viewport');
  return panel;
};
const collapseModel = async () => {
  const button = document.getElementById(modelDetail().getAttribute('aria-labelledby'));
  button.focus(); button.click(); await pause();
  check(!modelDetail() && button.getAttribute('aria-expanded') === 'false' && document.activeElement === button, 'Collapse lost focus/state');
};
"""


CUSTOM_RANGE_CHECKS = OVERVIEW_CONTROLS + EXPANSION_CHECKS + r"""
const chooseRange = async (start, end) => {
  await chooseDate('Start', start); await chooseDate('End', end);
};
const checkRange = (start, end, count) => {
  check(!document.querySelector('[role="alert"]'), 'Valid custom range rejected');
  check(scope().includes(count.toLocaleString('en-US') + (count === 1 ? ' day inclusive' : ' days inclusive')) && scope().includes(start) && scope().includes(end), 'Inclusive count or endpoints missing');
  const rows = filteredRows().filter(row => row.day >= start && row.day <= end);
  const sum = key => rows.reduce((n, row) => n + BigInt(row[key]), 0n);
  const denominator = sum('input') + sum('cacheRead') + sum('cacheWrite');
  const share = denominator ? (Number((sum('cacheRead') * 10000n + denominator / 2n) / denominator) / 100).toFixed(2) + '%' : 'N/A';
  const expected = [tokenTotal(rows).toLocaleString('en-US'), sum('output').toLocaleString('en-US'), share, '$' + costTotal(rows).toFixed(4)];
  check(metrics().join('|') === expected.join('|'), 'Full-range cards ignored endpoints or filters: ' + start + '/' + end);
  const groups = new Map();
  for (const row of rows) {
    const key = JSON.stringify([row.provider, row.model]);
    groups.set(key, (groups.get(key) ?? 0n) + tokenTotal([row]));
  }
  const ranked = [...groups].sort(([a, x], [b, y]) => x === y ? a.localeCompare(b) : x > y ? -1 : 1).slice(0, 5);
  const models = [...document.querySelectorAll('.models li')];
  check(models.length === ranked.length, 'Top model pair count incorrect');
  models.forEach((node, index) => {
    const [key, amount] = ranked[index], [provider, model] = JSON.parse(key);
    check(node.querySelector('strong').textContent === (model || '(unspecified model)') && node.querySelector('.model-identity > span').textContent === (provider || '(unspecified provider)'), 'Top models rank outside selected range');
    check(node.querySelector('.model-token-amount').textContent === amount.toLocaleString('en-US'), 'Top model amount is not full range');
  });
  const length = Math.min(count, 31);
  check(trend().length === length, 'Custom trend truncated short range or exceeded cap');
  const first = new Date(Date.parse(end) - (length - 1) * 86400000).toISOString().slice(0, 10);
  trend().forEach((node, index) => {
    const day = new Date(Date.parse(first) + index * 86400000).toISOString().slice(0, 10);
    const daily = rows.filter(row => row.day === day), amount = tokenTotal(daily);
    const future = day > snapshot.stats.asOf.slice(0, 10);
    const state = future ? 'future' : !daily.length ? 'missing' : amount === 0n ? 'zero' : 'recorded';
    const link = node.querySelector('a');
    check(node.querySelector('time').dateTime === day && node.dataset.state === state, 'Day label/state shifted: ' + day);
    check(link.getAttribute('href') === '#/sessions/' + day + suffix(), 'Custom daily link lost filters');
    check(link.getAttribute('aria-label').includes('View sessions for ' + day), 'Link lacks accessible name');
    check(link.textContent.includes('Future date after snapshot') === future, 'Future label missing or uses browser timezone');
    check(daily.length ? link.textContent.includes(amount.toLocaleString('en-US') + ' tokens') : link.textContent.includes('No recorded usage') && !link.textContent.includes('0 tokens'), 'Daily amount or gap incorrect');
    if (daily.length && amount === 0n) check(link.textContent.includes('Recorded zero'), 'Zero is not explicitly recorded');
    link.focus(); check(document.activeElement === link && getComputedStyle(link).outlineStyle !== 'none', 'Day link keyboard focus missing');
  });
  if (count > 31) {
    const text = document.querySelector('[aria-labelledby="trend-title"]').getAttribute('aria-description');
    check(text.includes('Last 31 days of the selected range') && text.includes(first + ' to ' + end) && text.includes('full ' + count.toLocaleString('en-US') + '-day range'), 'Bounded trend hides full card scope');
  }
  check(document.documentElement.scrollWidth <= innerWidth, 'Custom range overflows viewport');
};
"""


def payload(document):
    return json.loads(re.search(
        r'<script type="application/json" id="usage-data">(.*?)</script>',
        document, re.S).group(1))


def sha(value):
    return base64.b64encode(hashlib.sha256(value.encode()).digest()).decode()


class ReactBridgeTests(unittest.TestCase):
    def setUp(self):
        # Test artifacts stay inside the permitted generated-output directory.
        DIST.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=DIST, prefix="bridge-test-")
        self.addCleanup(self.temp.cleanup)
        self.assets = Path(self.temp.name)
        self.script = "'use strict';document.getElementById('root').textContent='local';"
        self.style = "body { color: #eee; background: #111; }"
        (self.assets / "dashboard.js").write_text(self.script)
        (self.assets / "dashboard.css").write_text(self.style)

    def test_allowlist_precision_escaping_and_csp_hashes(self):
        document = render_react_dashboard(fixture(), self.assets)
        data = payload(document)
        self.assertEqual(set(data), {"version", "rows", "roles", "stats", "companySlots", "sessions"})
        self.assertEqual(data["version"], 5)
        self.assertEqual(data["sessions"], [])
        self.assertEqual(data["companySlots"], ["OpenAI", "DeepSeek", None, None, None])
        self.assertEqual(data["rows"][0]["input"], "9007199254740993")
        self.assertEqual(data["rows"][0]["model"], fixture()["rows"][0]["model"])
        self.assertEqual(data["rows"][1]["role"], "worker")
        self.assertEqual(data["roles"], ["worker", "explore"])
        self.assertEqual(set(data["stats"]["coverage"]), set(_COVERAGE_KEYS))
        for row in data["rows"]:
            self.assertEqual(set(row), {"day", "provider", "model", "role", "company", "input",
                                        "output", "cacheRead", "cacheWrite", "cost"})
            for key in ("input", "output", "cacheRead", "cacheWrite"):
                self.assertIsInstance(row[key], str)
        for forbidden in ("PRIVATE_", "</script><img", "<img", "unsafe-inline", 'src="', 'href="http'):
            self.assertNotIn(forbidden, document)
        self.assertIn("\\u003c/script\\u003e", document)
        self.assertIn(f"script-src 'sha256-{sha(self.script)}'", document)
        self.assertIn(f"style-src 'sha256-{sha(self.style)}'", document)
        self.assertIn("connect-src 'none'", document)
        self.assertIn('http-equiv="refresh" content="300"', document)
        self.assertIn(f"<script>{self.script}</script>", document)

    def test_sessions_allowlist_every_level_precision_and_no_source_mutation(self):
        source = session_fixture()
        before = deepcopy(source)
        document = render_react_dashboard(source, self.assets)
        data = payload(document)
        self.assertEqual(source, before)
        self.assertEqual(len(data["sessions"]), 2)
        self.assertEqual(data["sessions"][0]["startDay"], "2026-06-01")
        self.assertEqual(data["sessions"][0]["endDay"], "2026-06-02")
        self.assertIsNone(data["sessions"][0]["role"])
        self.assertEqual(data["sessions"][1]["role"], "worker")
        self.assertEqual(data["sessions"][0]["days"][0]["models"][0]["input"], "9007199254740993")
        for session in data["sessions"]:
            self.assertEqual(set(session), {"startDay", "endDay", "role", "name", "sessionId", "days"})
            self.assertIsNone(session["sessionId"])
            self.assertIsNone(session["name"])
            for day in session["days"]:
                self.assertEqual(set(day), {"day", "models"})
                for model in day["models"]:
                    self.assertEqual(set(model), {"provider", "model", "input", "output",
                                                  "cacheRead", "cacheWrite", "cost"})
                    for key in ("input", "output", "cacheRead", "cacheWrite"):
                        self.assertRegex(model[key], r"^(0|[1-9][0-9]*)$")
        self.assertNotIn("PRIVATE_", document)
        self.assertNotIn("</script><img", document)
        self.assertIn("\\u003c/script\\u003e", document)

    def test_invalid_session_schema_fails_before_publication(self):
        output = self.assets / "untouched.html"
        output.write_text("existing output")
        for label, source in invalid_session_cases(session_fixture()):
            with self.subTest(mutation=label), \
                 patch("dashboard.collect_combined_usage", return_value=source):
                with self.assertRaises(ValueError):
                    main(["--preview-react", "--output", str(output)])
                self.assertEqual(output.read_text(), "existing output")

    def test_collector_bridge_only_accepted_files_reconcile_without_identifiers(self):
        roots = [self.assets / name for name in ("parents", "tasks", "children", "agents")]
        for root in roots:
            root.mkdir()
        parents, tasks, children, agents = roots
        (agents / "worker.md").write_text("PRIVATE_AGENT_CONTENT")
        def event(identity, day, amount):
            return {"type": "usage", "id": identity, "timestamp": day + "T12:00:00Z",
                    "provider": "synthetic", "model": "synthetic-model", "usage": {"input": amount},
                    "prompt": "PRIVATE_PROMPT"}
        accepted = event("PRIVATE_ID", "2026-06-01", 9007199254740993)
        zero = event("PRIVATE_ZERO_ID", "2026-06-02", 0)
        def write(root, name, entries):
            (root / name).write_text("\n".join(json.dumps(entry) for entry in entries))
        write(parents, "a-PRIVATE_PATH.jsonl", [accepted, zero])
        write(parents, "b-copy.jsonl", [accepted])
        write(parents, "empty.jsonl", [{"type": "message", "message": {
            "role": "user", "content": "PRIVATE_MESSAGE"}}])
        write(parents, "invalid.jsonl", [event("invalid", "2026-06-01", -1)])
        write(children, "linked.jsonl", [event("child", "2026-06-01", 7)])
        write(children, "unlinked.jsonl", [event("unlinked", "2026-06-01", 999)])
        (tasks / "link.json").write_text(json.dumps({"task": {
            "agent": "worker", "sessionPath": "linked.jsonl", "title": "PRIVATE_TITLE"}}))
        source = collect_combined_usage(parents, tasks, children, agents,
                                        now=datetime(2026, 6, 3, tzinfo=timezone.utc), tz=timezone.utc)
        data = payload(render_react_dashboard(source, self.assets))
        self.assertEqual(len(data["sessions"]), 2)
        self.assertEqual({session["role"] for session in data["sessions"]}, {None, "worker"})
        self.assertEqual(sum(len(session["days"]) for session in data["sessions"]), 3)
        for row in data["rows"]:
            contributions = [model for session in data["sessions"] if session["role"] == row["role"]
                             for day in session["days"] if day["day"] == row["day"]
                             for model in day["models"]
                             if (model["provider"], model["model"]) == (row["provider"], row["model"])]
            for key in ("input", "output", "cacheRead", "cacheWrite"):
                self.assertEqual(sum(int(model[key]) for model in contributions), int(row[key]))
            self.assertEqual(sum(model["cost"] for model in contributions), row["cost"])
        self.assertNotIn("PRIVATE_", json.dumps(data))
        self.assertNotIn("sessionPath", json.dumps(data))

    def test_company_contract_reuses_classification_and_disjoint_rows(self):
        source = company_fixture()
        document = render_react_dashboard(source, self.assets)
        data = payload(document)
        self.assertEqual(data["companySlots"], ["OpenAI", "DeepSeek", "xAI", "Google", "Anthropic"])
        self.assertEqual(data["companySlots"], company_slots(source["rows"]))
        self.assertEqual(len(data["rows"]), len(source["rows"]))
        for original, exported in zip(source["rows"], data["rows"]):
            self.assertEqual(exported["company"], company_for(original["provider"], original["model"]))
            self.assertEqual(exported["role"], original["role"])
            self.assertEqual(exported["input"], str(original["input"]))
        self.assertIsNone(data["rows"][3]["company"])
        self.assertEqual(sum(int(row["input"]) for row in data["rows"] if row["company"] == "OpenAI"), 30)
        self.assertEqual(sum(int(row["output"]) for row in data["rows"] if row["company"] == "OpenAI"), 15)
        self.assertNotIn("PRIVATE_", document)
        self.assertNotIn("</script><img", document)
        self.assertEqual(source["rows"][0]["company"], "PRIVATE_INJECTED")

    def test_zai_company_snapshot_is_accepted_by_typescript_parser(self):
        compiler = ROOT / "web" / "node_modules" / "typescript"
        if not shutil.which("node") or not compiler.is_dir():
            self.skipTest("Node and installed TypeScript are required for the parser check")
        source = fixture()
        source["rows"] = [{**source["rows"][0], "provider": "commandcode", "model": model}
                          for model in ("zai-org/glm-5.3", "z-ai/glm-5.3-flash")]
        before = deepcopy(source)
        exported = json.loads(react_snapshot_json(source))
        self.assertEqual(source, before)
        self.assertEqual(exported["companySlots"], ["OpenAI", "DeepSeek", "Z.ai", None, None])
        self.assertEqual([row["company"] for row in exported["rows"]], ["Z.ai", "Z.ai"])
        program = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('./web/node_modules/typescript');
const code = ts.transpileModule(fs.readFileSync('web/src/data.ts', 'utf8'), {
  compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
}).outputText;
const exports = {};
vm.runInNewContext(code, {exports});
const snapshot = JSON.parse(fs.readFileSync(0, 'utf8'));
const before = JSON.stringify(snapshot);
const parsed = exports.parseSnapshot(snapshot);
assert.equal(parsed.companySlots[2], 'Z.ai');
assert.deepEqual(Array.from(parsed.rows, row => [row.provider, row.model, row.company, String(row.input)]),
  snapshot.rows.map(row => [row.provider, row.model, 'Z.ai', row.input]));
assert.equal(typeof parsed.rows[0].input, 'bigint');
assert.equal(JSON.stringify(snapshot), before);
snapshot.rows[0].company = 'Unrecognized company';
assert.throws(() => exports.parseSnapshot(snapshot), /Invalid company/);
console.log('PASS: Z.ai snapshot preserves model/provider identity and exact tokens.');
"""
        result = subprocess.run(["node", "-e", program], cwd=ROOT,
                                input=json.dumps(exported), text=True,
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout,
                         "PASS: Z.ai snapshot preserves model/provider identity and exact tokens.\n")
        self.assertEqual(result.stderr, "")
        self.assertEqual(exported["rows"][0]["input"], "9007199254740993")

    def test_agent_contract_preserves_event_models_and_unused_roles(self):
        data = payload(render_react_dashboard(agent_fixture(), self.assets))
        self.assertEqual(data["roles"], ["worker", "worker-extra", "unused", "verify #β%"])
        rows = [row for row in data["rows"] if row["role"] == "worker"]
        self.assertEqual([(row["provider"], row["model"]) for row in rows],
                         [("openai", "gpt-event"), ("broker", "gpt-event"), ("broker", "claude-event")])
        self.assertEqual([sum(int(row[key]) for row in rows)
                          for key in ("input", "output", "cacheRead", "cacheWrite")], [30, 15, 30, 10])
        self.assertEqual(sum(row["cost"] for row in rows), 0.875)
        self.assertEqual(len([row for row in data["rows"] if row["role"] is None]), 1)
        self.assertFalse(any(row["role"] == "unused" for row in data["rows"]))
        self.assertNotIn("PRIVATE_LAUNCH_MODEL", json.dumps(data))
        self.assertNotIn('"tokens"', json.dumps(data))

    def test_company_empty_slots_and_bigint_volume_ranking(self):
        source = fixture()
        source["rows"] = []
        self.assertEqual(payload(render_react_dashboard(source, self.assets))["companySlots"],
                         ["OpenAI", "DeepSeek", None, None, None])
        source = company_fixture()
        source["rows"][-1]["cacheRead"] = 9007199254740993
        data = payload(render_react_dashboard(source, self.assets))
        self.assertEqual(data["companySlots"], ["OpenAI", "DeepSeek", "Alibaba", "xAI", "Google"])
        self.assertEqual(data["rows"][-1]["cacheRead"], "9007199254740993")

    def test_missing_empty_and_unsafe_assets_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "React assets unavailable"):
            render_react_dashboard(fixture(), self.assets / "absent")
        for script, style in (("", self.style), (self.script, ""),
                              ("</ScRiPt>", self.style), (self.script, "</STYLE>")):
            with self.subTest(script=script, style=style):
                (self.assets / "dashboard.js").write_text(script)
                (self.assets / "dashboard.css").write_text(style)
                with self.assertRaises(ValueError):
                    render_react_dashboard(fixture(), self.assets)

    def test_invalid_tokens_costs_and_roles_rejected(self):
        for key, value in (("input", -1), ("output", True), ("cacheRead", 1.5),
                           ("input", "42"), ("cost", float("nan")),
                           ("cost", float("inf")), ("cost", -1),
                           ("role", "sdd-apply"), ("role", "unconfigured")):
            with self.subTest(key=key, value=value):
                data = fixture()
                data["rows"][0][key] = value
                with self.assertRaises(ValueError):
                    render_react_dashboard(data, self.assets)
        for role in ("sdd-apply", "/private/path", "bad\\path", ""):
            with self.subTest(role=role):
                data = fixture()
                data["roles"].append(role)
                with self.assertRaises(ValueError):
                    render_react_dashboard(data, self.assets)

    def test_invalid_stats_as_of_rejected(self):
        for value in (None, True, 20260603, "", "not-a-date", "2026-06-03",
                      "2026-06-03T12:00:00", "2026-06-03 12:00:00Z",
                      "2026-6-03T12:00:00Z", "2026-06-03T12:00:00+0000",
                      "2026-06-03T12:00:00+00:00:30", "2026-06-03T12:00:00Z\n",
                      "2026-06-03T12:00:00,123Z", "2026-06-03T12:00:00.Z",
                      "2026-02-30T12:00:00Z", "2025-02-29T12:00:00Z",
                      "0000-01-01T00:00:00Z", "2026-13-01T12:00:00Z",
                      "2026-06-03T25:00:00Z", "2026-06-03T12:60:00Z",
                      "2026-06-03T12:00:60Z", "2026-06-03T12:00:00+24:00",
                      "2026-06-03T12:00:00+00:60", "2026-06-03T12:00:00-00:60"):
            with self.subTest(asOf=value):
                source = fixture()
                source["stats"]["asOf"] = value
                with self.assertRaises(ValueError):
                    render_react_dashboard(source, self.assets)

    def test_invalid_stats_calendar_days_rejected(self):
        for key in ("oldestDay", "newestDay"):
            for value in (True, 20260601, "", "not-a-date", "2026-6-01",
                          "2026-06-01T00:00:00Z", "2026-06-01\n", "0000-01-01",
                          "2026-02-30", "2025-02-29", "2026-13-01", "2026-06-00"):
                with self.subTest(key=key, value=value):
                    source = fixture()
                    source["stats"][key] = value
                    with self.assertRaises(ValueError):
                        render_react_dashboard(source, self.assets)

    def test_invalid_stats_warning_and_coverage_counters_rejected(self):
        warnings = ("malformed", "unreadable", "future", "outOfRange")
        for group, keys in ((None, warnings), ("coverage", _COVERAGE_KEYS)):
            for key in keys:
                for value in (-1, True, 1.5, "1", None, float("nan"), float("inf"),
                              9007199254740992, 9007199254740993, 10**100):
                    with self.subTest(group=group, key=key, value=value):
                        source = fixture()
                        target = source["stats"] if group is None else source["stats"][group]
                        target[key] = value
                        with self.assertRaises(ValueError):
                            render_react_dashboard(source, self.assets)

    def test_stats_boundaries_preserve_dates_and_unbounded_token_precision(self):
        for as_of, day in (("2024-02-29T12:34:56Z", "2024-02-29"),
                           ("2026-06-03T12:34:56.123456+05:30", "2026-06-03"),
                           ("2026-06-03T12:34:56.1-03:30", "2026-06-03"),
                           ("2026-06-03T12:34:56+00:00", None)):
            for limit in (0, 9007199254740991):
                with self.subTest(asOf=as_of, day=day, limit=limit):
                    source = session_fixture()
                    source["stats"].update(asOf=as_of, oldestDay=day, newestDay=day)
                    for key in ("malformed", "unreadable", "future", "outOfRange"):
                        source["stats"][key] = limit
                    source["stats"]["coverage"] = dict.fromkeys(_COVERAGE_KEYS, limit)
                    source["rows"][0]["output"] = 10**100
                    source["sessions"][0]["days"][0]["models"][0]["output"] = 10**100
                    before = deepcopy(source)
                    data = payload(render_react_dashboard(source, self.assets))
                    self.assertEqual(source, before)
                    self.assertEqual(data["stats"]["asOf"], as_of)
                    self.assertEqual(data["stats"]["oldestDay"], day)
                    self.assertEqual(data["stats"]["newestDay"], day)
                    for key in ("malformed", "unreadable", "future", "outOfRange"):
                        self.assertEqual(data["stats"][key], limit)
                    self.assertEqual(data["stats"]["coverage"], dict.fromkeys(_COVERAGE_KEYS, limit))
                    for model in (data["rows"][0], data["sessions"][0]["days"][0]["models"][0]):
                        self.assertEqual(model["input"], "9007199254740993")
                        self.assertEqual(model["output"], str(10**100))

    def test_invalid_stats_fail_before_publication(self):
        output = self.assets / "untouched.html"
        write_dashboard(output, "existing output")
        for key, value in (("asOf", "not-a-date"), ("oldestDay", "2026-02-30"),
                           ("newestDay", "2026-13-01"), ("malformed", 9007199254740992),
                           ("coverage", dict.fromkeys(_COVERAGE_KEYS, 9007199254740992))):
            with self.subTest(key=key):
                source = fixture()
                source["stats"][key] = value
                with self.assertRaises(ValueError):
                    write_dashboard(output, render_react_dashboard(source, self.assets))
                self.assertEqual(output.read_text(), "existing output")
                self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
                self.assertFalse(list(self.assets.glob(".dashboard-*")))

    def test_preview_uses_existing_private_atomic_writer(self):
        output = self.assets / "private" / "preview.html"
        document = render_react_dashboard(fixture(), self.assets)
        write_dashboard(output, document)
        self.assertEqual(stat.S_IMODE(output.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        self.assertEqual(output.read_text(), document)
        write_dashboard(output, document)
        self.assertEqual(list(output.parent.iterdir()), [output])

    def test_default_cli_remains_legacy_without_loading_build(self):
        with patch("dashboard.collect_usage", return_value=fixture()) as legacy, \
             patch("dashboard.collect_combined_usage") as combined, \
             patch("dashboard.render_dashboard", return_value="legacy") as render, \
             patch("dashboard.render_react_dashboard") as react, \
             patch("dashboard.write_dashboard") as write:
            main([])
            legacy.assert_called_once()
            combined.assert_not_called()
            render.assert_called_once_with(legacy.return_value)
            react.assert_not_called()
            write.assert_called_once_with(DEFAULT_OUTPUT, "legacy")

    def test_dev_json_cli_reuses_nested_allowlist_without_assets_or_publication(self):
        source = session_fixture()
        expected = payload(render_react_dashboard(source, self.assets))
        output = io.StringIO()
        with patch("dashboard.collect_combined_usage", return_value=source) as combined, \
             patch("dashboard.collect_usage") as legacy, \
             patch("dashboard.render_react_dashboard") as render, \
             patch("dashboard.write_dashboard") as write, \
             patch("dashboard.Path.read_text", side_effect=AssertionError("No asset reads")), \
             redirect_stdout(output):
            main(["--dev-json", "--sessions", str(self.assets / "parents")])
        combined.assert_called_once_with(self.assets / "parents")
        legacy.assert_not_called()
        render.assert_not_called()
        write.assert_not_called()
        self.assertEqual(json.loads(output.getvalue()), expected)
        self.assertEqual(output.getvalue(), react_snapshot_json(source) + "\n")
        for forbidden in ("PRIVATE_", "sessionPath", '"prompt"', '"cwd"', "Dashboard updated"):
            self.assertNotIn(forbidden, output.getvalue())
        self.assertIn('"9007199254740993"', output.getvalue())
        self.assertNotIn("</script><img", output.getvalue())

    def test_dev_json_cli_rejects_other_outputs_and_sanitizes_failures(self):
        for args in (["--dev-json", "--preview-react"],
                     ["--dev-json", "--output", str(self.assets / "not-written.html")]):
            with self.subTest(args=args), patch("sys.stderr"), \
                 patch("dashboard.collect_combined_usage") as combined, \
                 patch("dashboard.write_dashboard") as write:
                with self.assertRaises(SystemExit) as error:
                    main(args)
                self.assertEqual(error.exception.code, 2)
                combined.assert_not_called()
                write.assert_not_called()
        for error in (OSError("PRIVATE_SOURCE_PATH"), ValueError("PRIVATE_RECORD")):
            output = io.StringIO()
            with self.subTest(error=type(error).__name__), \
                 patch("dashboard.collect_combined_usage", side_effect=error), \
                 patch("dashboard.write_dashboard") as write, redirect_stdout(output):
                with self.assertRaises(SystemExit) as stopped:
                    main(["--dev-json"])
                self.assertEqual(str(stopped.exception), "Usage snapshot unavailable.")
                self.assertEqual(output.getvalue(), "")
                write.assert_not_called()
        # Validation failures share the same stdout-empty, sanitized failure boundary.
        source = session_fixture()
        source["sessions"][0]["days"][0]["models"][0]["input"] = -1
        with patch("dashboard.collect_combined_usage", return_value=source), redirect_stdout(io.StringIO()) as output:
            with self.assertRaisesRegex(SystemExit, "Usage snapshot unavailable"):
                main(["--dev-json"])
            self.assertEqual(output.getvalue(), "")

    def test_preview_cli_is_explicit_and_separate(self):
        output = self.assets / "preview.html"
        with patch("dashboard.collect_usage") as legacy, \
             patch("dashboard.collect_combined_usage", return_value=fixture()) as combined, \
             patch("dashboard.render_react_dashboard", return_value="preview") as react, \
             patch("dashboard.write_dashboard") as write:
            main(["--preview-react", "--sessions", str(self.assets / "sessions"),
                  "--output", str(output)])
            legacy.assert_not_called()
            combined.assert_called_once_with(self.assets / "sessions")
            react.assert_called_once_with(combined.return_value, DIST)
            write.assert_called_once_with(output, "preview")
        for args in (["--preview-react"],
                     ["--preview-react", "--output", str(DEFAULT_OUTPUT)]):
            with self.subTest(args=args), patch("dashboard.collect_combined_usage") as combined, \
                 patch("dashboard.write_dashboard") as write, patch("sys.stderr"):
                with self.assertRaises(SystemExit) as error:
                    main(args)
                self.assertEqual(error.exception.code, 2)
                combined.assert_not_called()
                write.assert_not_called()

    def test_missing_build_does_not_replace_output(self):
        output = self.assets / "untouched.html"
        output.write_text("existing output")
        with patch("dashboard.collect_combined_usage", return_value=fixture()), \
             patch("dashboard.render_react_dashboard", side_effect=ValueError("build unavailable")):
            with self.assertRaises(ValueError):
                main(["--preview-react", "--output", str(output)])
        self.assertEqual(output.read_text(), "existing output")


@unittest.skipUnless(shutil.which("chromium"), "Chromium unavailable; file:// proof pending")
class ReactBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (DIST / "dashboard.js").is_file() or not (DIST / "dashboard.css").is_file():
            raise unittest.SkipTest("Build React assets before running browser verification")

    def browser(self, document, checks, viewport="1280,900", browser_timezone="UTC", virtual_time=4000):
        # Assert the real bundle hashes, not just the stub assets in bridge tests.
        self.assertIn(f"script-src 'sha256-{sha((DIST / 'dashboard.js').read_text())}'", document)
        self.assertIn(f"style-src 'sha256-{sha((DIST / 'dashboard.css').read_text())}'", document)
        policy = re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', document).group(1)
        self.assertNotIn("unsafe-inline", policy)
        self.assertNotIn("nonce-", policy)
        # A CSP-hashed, test-only probe drives the real bundle without a server,
        # DevTools listener, browser dependency, or access to the user's profile.
        probe = r"""
const errors = [];
const evidence = {};
window.addEventListener('error', event => errors.push(event.message));
window.addEventListener('unhandledrejection', () => errors.push('unhandled rejection'));
window.addEventListener('securitypolicyviolation', event => errors.push(event.violatedDirective));
const pause = () => new Promise(resolve => setTimeout(resolve, 100));
const check = (condition, message) => { if (!condition) throw new Error(message); };
(async () => {
  const result = document.createElement('pre');
  result.id = 'browser-result';
  try {
    await pause();
    CHECKS
    check(errors.length === 0, JSON.stringify(errors));
    check(performance.getEntriesByType('resource').length === 0, 'Unexpected resource requests');
    result.textContent = JSON.stringify({ok: true, errors, resources: 0, evidence});
  } catch (error) {
    result.textContent = JSON.stringify({ok: false, error: String(error), errors});
  }
  document.body.append(result);
})();
""".replace("CHECKS", checks).replace("</script", "<\\/script")
        document = document.replace("script-src ", f"script-src 'sha256-{sha(probe)}' ", 1)
        document = document.replace('<script type="application/json"',
                                    f'<script>{probe}</script><script type="application/json"', 1)
        with tempfile.TemporaryDirectory(prefix="browser-test-") as name:
            directory = Path(name)
            output = directory / "preview.html"
            write_dashboard(output, document)
            profile = directory / "profile"
            profile.mkdir(mode=0o700)
            command = [shutil.which("chromium"), "--headless", "--disable-gpu",
                       "--no-first-run", "--no-default-browser-check", "--disable-sync",
                       "--disable-background-networking", "--disable-component-update",
                       "--disable-extensions", "--disable-breakpad", "--disable-crash-reporter",
                       "--password-store=basic", f"--user-data-dir={profile}",
                       f"--crash-dumps-dir={profile}", f"--window-size={viewport}",
                       f"--virtual-time-budget={virtual_time}",
                       "--dump-dom", output.as_uri()]
            result = subprocess.run(command, capture_output=True, text=True, timeout=45,
                                    env={"PATH": os.defpath, "HOME": str(profile),
                                         "XDG_CONFIG_HOME": str(profile / "config"),
                                         "XDG_CACHE_HOME": str(profile / "cache"),
                                         "LANG": "C.UTF-8", "TZ": browser_timezone})
            self.assertEqual(result.returncode, 0, result.stderr[-3000:])
            found = re.search(r'<pre id="browser-result">(.*?)</pre>', result.stdout, re.S)
            self.assertIsNotNone(found, "Browser probe did not finish: " + result.stderr[-3000:])
            encoded = html_module.unescape(found.group(1))
            self.assertTrue(encoded.startswith("{"), f"Browser probe emitted non-JSON: {encoded[:2000]!r}")
            observed = json.loads(encoded)
            self.assertTrue(observed["ok"], observed)
            return observed

    def test_file_model_details_exact_offline_reference_rates_and_provenance(self):
        for provider in ("openai", "openai-codex"):
            with self.subTest(provider=provider):
                self.browser(render_react_dashboard(model_prices_fixture(), DIST), MODEL_DETAILS_CHECKS +
                             "const provider = " + json.dumps(provider) + ";" + r"""
await choose('Provider', JSON.stringify(provider));
for (const [model, input, output] of [['gpt-6-sol', 2, 10], ['gpt-6-astra', 10, 50], ['gpt-5.6-sol', 4, 20], ['gpt-5.6-terra', 2, 12]]) {
  await choose('Model', JSON.stringify(model));
  check(!modelDetail(), 'Model filter retained stale disclosure');
  const panel = await checkModelDetail(provider, model);
  check([...panel.querySelectorAll('.model-rates > div:not(.model-recorded-cost) dd')].map(node => node.textContent).join('|') === '$' + input + '|$' + output, 'Official input/output reference differs from verified snapshot');
  const provenance = document.getElementById(panel.getAttribute('aria-describedby')).textContent;
  check(provenance.includes('https://developers.openai.com/api/docs/models/' + model), 'Missing accessible exact official model source');
  check(provenance.includes('Captured as of 2026-09-24'), 'Reference as-of confused with selected date');
  check(panel.textContent.includes('Standard short-context text tokens') && panel.textContent.includes('USD per 1M (1,000,000) tokens'), 'Reference unit/category missing');
  check(panel.textContent.includes('Promotional rate; not guaranteed after November 21, 2026.') === (model === 'gpt-5.6-sol'), 'Promotional limitation lost or applied to wrong SKU');
  check(!panel.textContent.includes('Rate unavailable'), 'Exact reference was not found');
}
""")

    def test_file_model_details_unknown_alias_blank_and_malicious_rates_unavailable(self):
        for provider in ("openai", "openai-codex"):
            with self.subTest(provider=provider):
                self.browser(render_react_dashboard(model_prices_fixture(), DIST), MODEL_DETAILS_CHECKS +
                             "const provider = " + json.dumps(provider) + ";" + r"""
await choose('Provider', JSON.stringify(provider));
const unsupported = [...select('Model').options].slice(1).map(node => JSON.parse(node.value)).filter(model => !['gpt-6-sol', 'gpt-6-astra', 'gpt-5.6-sol', 'gpt-5.6-terra'].includes(model));
for (const model of unsupported) {
  await choose('Model', JSON.stringify(model));
  const panel = await checkModelDetail(provider, model);
  check([...panel.querySelectorAll('.model-rates > div:not(.model-recorded-cost) dd')].map(node => node.textContent).join('|') === 'Unavailable|Unavailable', 'Unknown/alias model received guessed rate');
  const description = document.getElementById(panel.getAttribute('aria-describedby')).textContent;
  check(description.includes('Rate unavailable') && !description.includes('Official source'), 'Unsupported SKU received source or lacks accessible unavailable explanation');
}
check(!document.querySelector('img'), 'Hostile label became markup');
""")
        self.browser(render_react_dashboard(model_prices_fixture(), DIST), MODEL_DETAILS_CHECKS + r"""
for (const provider of ['', 'OpenAI', 'openai ', 'openrouter', 'openai-compatible', '<img src="https://invalid.test/provider">']) {
  await choose('Provider', JSON.stringify(provider));
  const panel = await checkModelDetail(provider, 'gpt-6-sol');
  check([...panel.querySelectorAll('.model-rates > div:not(.model-recorded-cost) dd')].map(node => node.textContent).join('|') === 'Unavailable|Unavailable', 'Provider alias/blank/hostile provider received rate');
}
""")

    def test_file_model_details_pair_scope_collapse_filters_modes_and_responsive(self):
        for viewport in ("1280,900", "390,844"):
            with self.subTest(viewport=viewport):
                self.browser(render_react_dashboard(model_prices_fixture(), DIST), MODEL_DETAILS_CHECKS + r"""
await choose('Model', JSON.stringify('gpt-6-sol'));
check(modelToggles().length === 5, 'Actual pair cap changed');
// Filter to direct provider first; each same-name pair has independent totals.
await choose('Provider', JSON.stringify('openai-codex'));
await choose('Model', JSON.stringify('gpt-6-sol'));
await checkModelDetail('openai-codex', 'gpt-6-sol'); await collapseModel();
for (const note of document.querySelectorAll('.model-empty-slot')) {
  note.click(); await pause();
  check(!modelDetail() && !note.querySelector('button, a, summary') && !note.hasAttribute('tabindex'), 'Empty note is interactive');
}
await checkModelDetail('openai-codex', 'gpt-6-sol');
await choose('Provider', JSON.stringify('openai'));
check(!modelDetail(), 'Provider change kept stale expansion');
await choose('Model', JSON.stringify('gpt-6-sol'));
await checkModelDetail('openai', 'gpt-6-sol');
await chooseDay('2026-06-01'); check(!modelDetail(), 'Date change retained expansion');
await reset(); await choose('Provider', JSON.stringify('openai-codex')); await choose('Model', JSON.stringify('gpt-6-sol'));
await pickPeriod('Weekly'); await checkModelDetail('openai-codex', 'gpt-6-sol', '2026-06-01', '2026-06-07');
await choose('Calendar span', '2'); check(!modelDetail(), 'Span retained expansion');
await checkModelDetail('openai-codex', 'gpt-6-sol', '2026-05-25', '2026-06-07');
const providers = [...document.querySelectorAll('.ranking-switch button')].find(node => node.textContent === 'Providers');
providers.click(); await pause();
check(!modelDetail() && !document.querySelector('.ranked-providers button'), 'Provider mode retained model disclosure or made provider rows expandable');
document.querySelector('.ranking-switch button').click(); await pause();
check(!modelDetail(), 'Switch back resurrected stale details');
await pickPeriod('Custom range'); await chooseDate('Start', '2026-06-01');
await checkModelDetail('openai-codex', 'gpt-6-sol', '2026-06-01', '2026-06-02');
await chooseDate('End', '2026-06-01'); check(!modelDetail(), 'Custom end retained expansion');
await checkModelDetail('openai-codex', 'gpt-6-sol', '2026-06-01', '2026-06-01');
await chooseDate('Start', '2026-06-03'); check(!modelDetail() && modelToggles().length === 0, 'Invalid range retained stale disclosure');
await pickPeriod('Monthly'); await checkModelDetail('openai-codex', 'gpt-6-sol', '2026-06-01', '2026-06-30');
await choose('Ending month', '2026-05-01'); check(!modelDetail(), 'Month selection retained expansion');
await checkModelDetail('openai-codex', 'gpt-6-sol', '2026-05-01', '2026-05-31');
await pickPeriod('All time'); check(!modelDetail(), 'All-time selection retained expansion');
await checkModelDetail('openai-codex', 'gpt-6-sol', null, null);
""", viewport=viewport, virtual_time=10000)

    def test_file_top_models_selected_pairs_cap_accessible_scope_and_layout(self):
        for viewport in ("1280,900", "390,844"):
            with self.subTest(viewport=viewport):
                self.browser(render_react_dashboard(top_models_fixture(), DIST), TOP_MODELS_CHECKS + r"""
checkModels('2026-06-17', '2026-06-17', 4);
check(modelNames().join('|') === 'shared|shared|daily|fourth', 'Latest day must show its four real pairs only');
check(modelPanel().querySelector('.model-identity > span').textContent === 'Zeta', 'One-token lead above Number precision lost');
await pickPeriod('Weekly'); checkModels('2026-06-15', '2026-06-21', 5);
check(modelNames().includes('week-only') && !modelNames().includes('month-only'), 'Week imports other dates');
const bar = document.querySelector('.trend-period');
bar.click(); await pause(); checkModelLayout();
bar.click(); await pause(); checkModelLayout();
await pickPeriod('Monthly'); checkModels('2026-06-01', '2026-06-30', 5);
check(modelNames()[0] === 'month-only' && !modelNames().includes('old-only') && !modelNames().includes('week-only'), 'Month exceeds cap or scope');
await pickPeriod('Custom range');
await chooseDate('Start', '2026-06-16'); checkModels('2026-06-16', '2026-06-17', 5);
await chooseDate('Start', '2026-06-17'); checkModels('2026-06-17', '2026-06-17', 4);
await pickPeriod('All time'); checkModels(null, null, 5);
check(new Set(snapshot.rows.map(row => JSON.stringify([row.provider, row.model]))).size === 7 && modelNames()[0] === 'old-only', 'Seven historical pairs must be capped at five');
await pickPeriod('Daily');
await choose('Model', JSON.stringify('shared')); checkModels('2026-06-17', '2026-06-17', 2);
await choose('Provider', JSON.stringify('Zeta')); checkModels('2026-06-17', '2026-06-17', 1);
await choose('Model', JSON.stringify('shared')); checkModels('2026-06-17', '2026-06-17', 1);
check(document.querySelector('.trend-day').getAttribute('href').includes('provider=Zeta&model=shared'), 'Filtered day link changed');
""", viewport=viewport)

    def test_file_top_models_zero_missing_invalid_and_recovery_layout(self):
        for viewport in ("1280,900", "390,844"):
            with self.subTest(viewport=viewport):
                self.browser(render_react_dashboard(top_models_fixture(), DIST), TOP_MODELS_CHECKS + r"""
await chooseDay('2026-06-14'); checkModels('2026-06-14', '2026-06-14', 1);
check(modelPanel().querySelector('.model-token-amount').textContent === '0' && modelPanel().querySelector('.model-cache-share').textContent === 'N/A' && scope().includes('Recorded usage is present'), 'Recorded zero misrepresented');
await chooseDay('2026-06-13'); checkModels('2026-06-13', '2026-06-13', 0);
check(modelPanel().innerText.includes('No measured models in this selection') && scope().includes('zero totals do not establish inactivity'), 'Missing usage misrepresented');
await pickPeriod('Daily'); await choose('Provider', JSON.stringify('Historical'));
checkModels('2026-06-17', '2026-06-17', 0);
await reset(); await pickPeriod('Custom range'); await chooseDate('Start', '2026-06-18');
checkModelLayout();
check(!modelPanel().querySelector('.models') && modelPanel().innerText.includes('Top models unavailable: invalid range'), 'Invalid range shows stale ranking');
check(total() === 'Unavailable' && document.querySelector('[role="alert"]') && calendar('Start').getAttribute('aria-invalid') === 'true', 'Invalid state inaccessible');
await chooseDate('Start', '2026-06-17'); checkModels('2026-06-17', '2026-06-17', 4);
""", viewport=viewport)

    def test_file_top_models_empty_rows_daily_weekly_presets_filters_and_zero(self):
        data = top_models_fixture()
        template = data["rows"][0]
        data["rows"] = [
            {**template, "day": day, "provider": "Extra" if index == 4 else "Included",
             "model": f"selected-{index}", "input": amount + index if amount else 0,
             "output": 0, "cacheRead": 0, "cacheWrite": 0, "cost": 0}
            for day, count, amount in (("2026-06-18", 4, 100), ("2026-06-17", 5, 10),
                                       ("2026-06-16", 4, 0), ("2026-06-15", 3, 5),
                                       ("2026-06-08", 3, 20), ("2026-06-01", 4, 0))
            for index in range(count)
        ]
        data["stats"].update(oldestDay="2026-06-01", newestDay="2026-06-18")
        for viewport in ("1280,900", "390,844"):
            with self.subTest(viewport=viewport):
                self.browser(render_react_dashboard(data, DIST), TOP_MODELS_CHECKS + r"""
checkModels('2026-06-18', '2026-06-18', 4);
await pickPeriod('Today'); checkModels('2026-06-18', '2026-06-18', 4);
await pickPeriod('Yesterday'); checkModels('2026-06-17', '2026-06-17', 5);
await choose('Provider', JSON.stringify('Included')); checkModels('2026-06-17', '2026-06-17', 4);
await choose('Model', JSON.stringify('selected-0')); checkModels('2026-06-17', '2026-06-17', 1);
await reset(); await chooseDay('2026-06-16'); checkModels('2026-06-16', '2026-06-16', 4);
check(!modelPanel().querySelector('.model-empty-slot'), 'Zero usage must not fabricate an empty fifth model');
await chooseDay('2026-06-15'); checkModels('2026-06-15', '2026-06-15', 3);
await chooseDay('2026-06-14'); checkModels('2026-06-14', '2026-06-14', 0);
await chooseDay('');
check(total() === 'Unavailable' && !modelPanel().querySelector('.models, .model-empty-slot'), 'Invalid day retained ranked or empty slots');
await reset(); await pickPeriod('Weekly'); checkModels('2026-06-15', '2026-06-21', 5);
await pickPeriod('Last Week'); checkModels('2026-06-08', '2026-06-14', 3);
await pickPeriod('Weekly'); await chooseDate('Week containing', '2026-06-08');
checkModels('2026-06-08', '2026-06-14', 3);
check(dates().length === 5 && modelPanel().querySelectorAll('.model-empty-slot').length === 2, 'Three-real weekly selection needs two empty rows and five context weeks');
await expand('2026-06-08', '2026-06-14'); checkModels('2026-06-08', '2026-06-14', 3); await closeExpansion();
await chooseDate('Week containing', '2026-06-01'); checkModels('2026-06-01', '2026-06-07', 4);
await chooseDate('Week containing', '2026-05-25'); checkModels('2026-05-25', '2026-05-31', 0);
await chooseDate('Week containing', '');
check(total() === 'Unavailable' && !modelPanel().querySelector('.models, .model-empty-slot'), 'Invalid week retained visual rows');
await reset(); await choose('Provider', JSON.stringify('Included'));
for (const mode of ['Last 7 Days', 'Monthly', 'All time', 'Custom range']) {
  await pickPeriod(mode);
  check(modelNames().length === 4 && !modelPanel().querySelector('.model-empty-slot'), 'Ineligible mode gained a filler slot: ' + mode);
  checkModelLayout();
}
""", viewport=viewport)

    def test_file_top_models_filtered_one_to_four_real_pairs_complete_visual_rows(self):
        data = top_models_fixture()
        template = data["rows"][0]
        data["rows"] = [
            {**template, "day": "2026-06-17", "provider": f"group-{count}",
             "model": f"model-{index}", "input": index + 1,
             "output": 0, "cacheRead": 0, "cacheWrite": 0, "cost": 0.125}
            for count in range(1, 5) for index in range(count)
        ]
        data["stats"].update(oldestDay="2026-06-17")
        for viewport in ("1280,900", "390,844"):
            with self.subTest(viewport=viewport):
                self.browser(render_react_dashboard(data, DIST), TOP_MODELS_CHECKS + r"""
for (const mode of ['Daily', 'Weekly']) {
  await pickPeriod(mode); await choose('Provider', '');
  const start = mode === 'Daily' ? '2026-06-17' : '2026-06-15';
  const end = mode === 'Daily' ? '2026-06-17' : '2026-06-21';
  checkModels(start, end, 5);
  for (const count of [4, 3, 2, 1]) {
    await choose('Provider', JSON.stringify('group-' + count));
    checkModels(start, end, count);
    check(modelPanel().querySelectorAll('.model-empty-slot').length === 5 - count, 'Filtered real count did not control empty rows');
    check(!modelPanel().querySelector('img, [style]') && !modelPanel().textContent.includes('PRIVATE_'), 'Empty rows introduced unsafe content or inline styles');
  }
  await choose('Provider', ''); await choose('Model', JSON.stringify('model-0'));
  checkModels(start, end, 4);
}
""", viewport=viewport)

    def test_file_overview_panel_typography_is_scoped_responsive_and_accessible(self):
        data = provider_fixture()
        data["rows"][0]["model"] = '</script><img src=x onerror=alert(1)>' + 'hostile' * 60
        for viewport in ("1280,900", "390,844"):
            with self.subTest(viewport=viewport):
                self.browser(render_react_dashboard(data, DIST), TOP_MODELS_CHECKS + r"""
const size = selector => parseFloat(getComputedStyle(document.querySelector(selector)).fontSize);
const larger = (selector, previous) => {
  const delta = size(selector) - previous;
  check(delta >= 1 - 0.01 && delta <= 2 + 0.01, 'Typography increase outside 1–2px: ' + selector + '=' + delta);
};
for (const mode of ['Daily', 'Weekly', 'Monthly', 'Custom range', 'All time']) {
  await pickPeriod(mode);
  if (mode === 'Daily') await chooseDay('2026-06-02');
  if (mode === 'Custom range') await chooseDate('Start', '2026-06-02');
  for (const panel of ['.trend-panel', '.models-panel']) {
    larger(panel + ' h2', 14);
    larger(panel + ' .panel-heading span', 11.2);
    const node = document.querySelector(panel), bounds = node.getBoundingClientRect();
    check(node.scrollWidth <= node.clientWidth + 1, 'Panel content clipped or overflowing: ' + panel);
    for (const label of node.querySelectorAll('time, strong, .model-total, .model-identity > span')) {
      if (!label.checkVisibility({visibilityProperty: true})) continue;
      const rect = label.getBoundingClientRect();
      check(rect.left >= bounds.left && rect.right <= bounds.right, 'Label overflow with large tokens or hostile text');
    }
  }
  const rem = parseFloat(getComputedStyle(document.documentElement).fontSize);
  check(size('.trend-day time') === rem && size('.trend-day > span') === rem, 'Overview bucket typography must be 1rem');
  larger('.models strong', 12.6);
  larger('.model-identity > span', 10.5);
  larger('.model-total', 14);
  check(Math.abs(size('.model-cache-share') - 0.95 * rem) < 0.01, 'Model cache share must match Agent size');
  check(size('.provider-panel h2') === 14 && Math.abs(size('.provider-panel .panel-heading span') - 11.2) < 0.01, 'Provider typography changed');
  checkModelLayout();
  if (mode === 'Weekly' || mode === 'Monthly') {
    check(size('.trend-cache-share') === rem, 'Overview bucket cache share must be 1rem');
    const bar = bars().at(-1); bar.focus();
    check(document.activeElement === bar && getComputedStyle(bar).outlineStyle !== 'none', 'Larger bar lost keyboard focus');
    bar.click(); await pause();
    larger('.trend-contributions time', 10.5);
    check(openPanel().querySelectorAll('.trend-all-sessions').length >= 7, 'Larger labels lost accessible day links');
    checkModelLayout(); await closeExpansion();
  }
}
check(!document.querySelector('img') && !document.querySelector('#root').textContent.includes('PRIVATE_'), 'Hostile markup or private field escaped into UI');
document.querySelector('nav a[href="#/companies"]').click(); await pause();
check(size('.company-card h2') === 17.5, 'Other route typography changed');
""", viewport=viewport)

    def test_file_top_models_empty_snapshot_keeps_scope_without_fabrication(self):
        data = top_models_fixture()
        data["rows"] = []
        self.browser(render_react_dashboard(data, DIST), TOP_MODELS_CHECKS + r"""
checkModels(null, null, 0);
check(modelPanel().innerText.includes('No measured models in this selection'), 'Empty rows borrowed session-summary models');
await pickPeriod('All time'); checkModels(null, null, 0);
""", viewport="390,844")

    def test_file_ranking_native_keyboard_and_accessibility_tree(self):
        self.ranking_native_keyboard_and_accessibility_tree('#/overview')

    def test_file_agent_native_keyboard_mouse_and_accessibility_tree(self):
        self.ranking_native_keyboard_and_accessibility_tree('#/agents/worker')

    def ranking_native_keyboard_and_accessibility_tree(self, route):
        # CDP over inherited pipes sends trusted keys without a TCP listener or
        # third-party browser driver. Both file and fresh profile are synthetic.
        data = fixture()
        data["rows"] = [dict(data["rows"][1], provider="openai-codex", model="keyboard-model")]
        if route.startswith('#/agents/'):
            data["stats"].update(asOf="2026-06-03T00:15:00+14:00", timezone="Pacific/Kiritimati")
        document = render_react_dashboard(data, DIST)
        self.assertIn(f"script-src 'sha256-{sha((DIST / 'dashboard.js').read_text())}'", document)
        self.assertIn(f"style-src 'sha256-{sha((DIST / 'dashboard.css').read_text())}'", document)
        script = r"""
import { spawn } from 'node:child_process';
const [chromium, profile, url] = process.argv.slice(1);
const browser = spawn(chromium, ['--headless', '--disable-gpu', '--no-first-run',
  '--no-default-browser-check', '--disable-sync', '--disable-background-networking',
  '--disable-component-update', '--disable-extensions', '--disable-breakpad',
  '--disable-crash-reporter', '--password-store=basic', '--remote-debugging-pipe',
  '--user-data-dir=' + profile, '--crash-dumps-dir=' + profile, 'about:blank'],
  {stdio: ['ignore', 'ignore', 'ignore', 'pipe', 'pipe']});
let sequence = 0, buffer = '';
const pending = new Map();
const check = (value, message) => { if (!value) throw new Error(message); };
browser.stdio[4].on('data', chunk => {
  buffer += chunk.toString();
  let end;
  while ((end = buffer.indexOf('\0')) >= 0) {
    const message = JSON.parse(buffer.slice(0, end)); buffer = buffer.slice(end + 1);
    const callback = pending.get(message.id);
    if (callback) { pending.delete(message.id); callback(message); }
  }
});
const call = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
  const id = ++sequence;
  const timer = setTimeout(() => { pending.delete(id); reject(new Error('CDP timeout: ' + method)); }, 10000);
  pending.set(id, message => {
    clearTimeout(timer);
    message.error ? reject(new Error(JSON.stringify(message.error))) : resolve(message.result);
  });
  browser.stdio[3].write(JSON.stringify({id, method, params, sessionId}) + '\0');
});
try {
  const {targetId} = await call('Target.createTarget', {url: 'about:blank'});
  const {sessionId} = await call('Target.attachToTarget', {targetId, flatten: true});
  const page = (method, params) => call(method, params, sessionId);
  const evaluate = async expression => {
    const result = await page('Runtime.evaluate', {expression, awaitPromise: true, returnByValue: true});
    check(!result.exceptionDetails, JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  await page('Page.enable');
  await page('Page.addScriptToEvaluateOnNewDocument', {source: `window.probeErrors = [];
    for (const type of ['error', 'unhandledrejection', 'securitypolicyviolation'])
      addEventListener(type, () => window.probeErrors.push(type));`});
  await page('Page.navigate', {url});
  await evaluate(`new Promise((resolve, reject) => {
    let count = 0;
    const ready = () => document.querySelector('.ranking-switch button') ? resolve(true) :
      ++count > 100 ? reject(new Error('UI not ready')) : setTimeout(ready, 50);
    ready();
  })`);
  const key = async (key, code, virtual, modifiers = 0) => {
    for (const type of ['keyDown', 'keyUp']) await page('Input.dispatchKeyEvent',
      {type, key, code, windowsVirtualKeyCode: virtual, modifiers,
        ...(type === 'keyDown' && (key === 'Enter' || key === ' ') ?
          {text: key === 'Enter' ? String.fromCharCode(13) : ' ',
            unmodifiedText: key === 'Enter' ? String.fromCharCode(13) : ' '} : {})});
    await evaluate('new Promise(resolve => setTimeout(resolve, 50))');
  };
  const assertMode = async (mode, pressed) => {
    const {nodes} = await page('Accessibility.getFullAXTree');
    const buttons = nodes.filter(node => !node.ignored && node.role?.value === 'button' && ['Models', 'Providers'].includes(node.name?.value));
    check(buttons.length === 2, 'Both toggle buttons must be in accessibility tree');
    check(buttons.every(node => String(node.properties.find(p => p.name === 'pressed')?.value.value) === String(node.name.value === pressed)), 'AX pressed state wrong: ' + JSON.stringify(buttons.map(node => ({name: node.name, properties: node.properties}))));
    check(nodes.some(node => !node.ignored && node.role?.value === 'heading' && node.name?.value === 'Top ' + mode), 'AX mode heading missing');
    // Agent full totals tables intentionally retain model text in both modes.
    check(nodes.some(node => !node.ignored && node.role?.value === 'button' && node.name?.value.includes('API reference for keyboard-model')) === (mode === 'models'), 'Inactive model disclosure exposed to accessibility tree');
    check(await evaluate(`document.activeElement.textContent === ${JSON.stringify(pressed)} && getComputedStyle(document.activeElement).outlineStyle !== 'none'`), 'Keyboard focus missing');
  };
  const before = await evaluate(`document.querySelector('.metrics').innerHTML + (document.querySelector('.provider-panel')?.innerHTML ?? '') + location.href`);
  for (const width of [1280, 390]) {
    await page('Emulation.setDeviceMetricsOverride', {width, height: 900, deviceScaleFactor: 1, mobile: false});
    if (url.includes('#/agents/')) {
      const {nodes} = await page('Accessibility.getFullAXTree');
      const heading = nodes.find(node => !node.ignored && node.role?.value === 'heading' && node.name?.value === 'worker');
      check(!!heading, 'Agent role accessible heading name lost');
      for (const phrase of ['Snapshot as of 2026-06-03 00:15', 'Timezone: Pacific/Kiritimati',
        'Actual usage-event providers and models for this exact role, not task launch metadata or task token totals.',
        'Historical child attribution may be incomplete; parent, SDD, and unlinked usage are excluded.']) {
        check(heading.description?.value.includes(phrase), 'Agent AX heading description missing: ' + phrase);
      }
      check(nodes.some(node => !node.ignored && node.role?.value === 'link' && node.name?.value.includes('Back to Agents')), 'Agent back link missing from AX');
    }
    for (const expanded of [true, false]) {
      const point = await evaluate(`(() => {
        const node = document.querySelector('.model-toggle'); node.scrollIntoView({block: 'center'});
        const rect = node.getBoundingClientRect(); return {x: rect.x + rect.width / 2, y: rect.y + rect.height / 2};
      })()`);
      await page('Input.dispatchMouseEvent', {type: 'mouseMoved', ...point});
      await page('Input.dispatchMouseEvent', {type: 'mousePressed', button: 'left', clickCount: 1, ...point});
      await page('Input.dispatchMouseEvent', {type: 'mouseReleased', button: 'left', clickCount: 1, ...point});
      await evaluate('new Promise(resolve => setTimeout(resolve, 50))');
      check(await evaluate(`(() => {
        const node = document.querySelector('.model-toggle'), style = getComputedStyle(node);
        return node.getAttribute('aria-expanded') === '${expanded}' && !node.matches(':focus-visible') &&
          style.backgroundColor === 'rgba(0, 0, 0, 0)' && style.borderTopColor === 'rgba(0, 0, 0, 0)' &&
          style.outlineStyle === 'none' && document.documentElement.scrollWidth <= innerWidth;
      })()`), 'Mouse activation left persistent highlight, wrong state or mobile overflow');
    }
  }
  await page('Emulation.setDeviceMetricsOverride', {width: 1280, height: 900, deviceScaleFactor: 1, mobile: false});
  await key('Tab', 'Tab', 9); // Restore genuine keyboard modality after pointer checks.
  await evaluate(`document.querySelector('.ranking-switch button').focus()`);
  await assertMode('models', 'Models');
  await key('Tab', 'Tab', 9);
  check(await evaluate(`document.activeElement.textContent === 'Providers'`), 'Tab did not reach Providers');
  await key('Enter', 'Enter', 13);
  await assertMode('providers', 'Providers');
  await key('Tab', 'Tab', 9, 8);
  check(await evaluate(`document.activeElement.textContent === 'Models'`), 'Shift-Tab did not return to Models');
  await key(' ', 'Space', 32);
  await assertMode('models', 'Models');
  await key('Tab', 'Tab', 9); // Providers, then the first real model row.
  await key('Tab', 'Tab', 9);
  check(await evaluate(`document.activeElement.classList.contains('model-toggle')`), 'Tab did not reach real model toggle');
  const assertDisclosure = async expanded => {
    check(await evaluate(`document.activeElement.classList.contains('model-toggle') && document.activeElement.getAttribute('aria-expanded') === '${expanded}' && getComputedStyle(document.activeElement).outlineStyle !== 'none'`), 'Disclosure lost focus/expanded state');
    const {nodes} = await page('Accessibility.getFullAXTree');
    const toggle = nodes.find(node => !node.ignored && node.role?.value === 'button' && node.name?.value.includes('API reference for keyboard-model'));
    check(toggle && toggle.properties.find(p => p.name === 'expanded')?.value.value === expanded, 'AX disclosure state missing');
    check(nodes.some(node => !node.ignored && node.role?.value === 'StaticText' && node.name?.value === 'Input · API reference (USD / 1M tokens)') === expanded, 'Collapsed rates leaked to AX or open rates missing');
    if (expanded) check(toggle.description?.value.includes('Historical applicability unknown') && toggle.description?.value.includes('Rate unavailable'), 'AX toggle description lost pricing caveats');
  };
  await key('Enter', 'Enter', 13); await assertDisclosure(true);
  await key(' ', 'Space', 32); await assertDisclosure(false);
  await key(' ', 'Space', 32); await assertDisclosure(true);
  await key('Enter', 'Enter', 13); await assertDisclosure(false);
  check(before === await evaluate(`document.querySelector('.metrics').innerHTML + (document.querySelector('.provider-panel')?.innerHTML ?? '') + location.href`), 'Keyboard switch changed data or route');
  if (url.includes('#/overview')) {
    const selectOverview = async (label, value) => evaluate(`(async () => {
      const node = [...document.querySelectorAll('.overview-controls label')].find(node => node.firstChild.textContent === ${JSON.stringify(label)}).querySelector('select');
      node.value = ${JSON.stringify(value)}; node.dispatchEvent(new Event('change', {bubbles: true}));
      await new Promise(resolve => setTimeout(resolve, 50));
    })()`);
    const returnOverview = async () => {
      await evaluate(`document.querySelector('nav a[href="#/overview"]').click(); new Promise(resolve => setTimeout(resolve, 50))`);
      await selectOverview('Provider', JSON.stringify('openai-codex'));
      await selectOverview('Model', JSON.stringify('keyboard-model'));
    };
    const clickTarget = async selector => {
      const point = await evaluate(`(() => {
        const node = document.querySelector(${JSON.stringify(selector)}); node.scrollIntoView({block: 'center'});
        const rect = node.getBoundingClientRect(); return {x: rect.x + rect.width / 2, y: rect.y + rect.height / 2};
      })()`);
      await page('Input.dispatchMouseEvent', {type: 'mousePressed', button: 'left', clickCount: 1, ...point});
      await page('Input.dispatchMouseEvent', {type: 'mouseReleased', button: 'left', clickCount: 1, ...point});
      await evaluate('new Promise(resolve => setTimeout(resolve, 50))');
    };
    const expected = '#/sessions/2026-06-02?provider=openai-codex&model=keyboard-model';
    for (const width of [1280, 390, 320]) {
      await page('Emulation.setDeviceMetricsOverride', {width, height: 900, deviceScaleFactor: 1, mobile: false});
      for (const mode of ['day', 'all', 'custom', 'week', 'month']) {
        await returnOverview(); await selectOverview('Period', mode);
        const {nodes} = await page('Accessibility.getFullAXTree');
        const target = nodes.find(node => !node.ignored && ['link', 'button'].includes(node.role?.value) &&
          node.name?.value.includes('11 tokens · Cache-read share: 30.00%'));
        check(!!target, 'Trend AX lacks numeric token unit/cache share');
        const model = nodes.find(node => !node.ignored && node.role?.value === 'button' && node.name?.value.includes('API reference for keyboard-model'));
        check(model?.name.value.includes('11 tokens · Cache-read share: 30.00%'), 'Model AX lost selected pair token/cache semantics');
        if (['week', 'month'].includes(mode)) {
          await evaluate(`document.querySelector('.trend > li:last-child .trend-period').focus()`);
          await key('Enter', 'Enter', 13);
          check(await evaluate(`document.activeElement.getAttribute('aria-expanded') === 'true' && getComputedStyle(document.activeElement).outlineStyle !== 'none'`), 'Native Enter did not expand period with focus');
          await key(' ', 'Space', 32);
          check(await evaluate(`document.activeElement.getAttribute('aria-expanded') === 'false'`), 'Native Space did not collapse period');
          await key(' ', 'Space', 32);
          check(await evaluate(`document.querySelector('.trend-expansion:not([hidden])').textContent.includes('$0.2500 recorded cost estimate')`), 'Expanded recorded cost disappeared');
          await evaluate(`document.querySelector('.trend-filtered-sessions[href="${expected}"]').focus()`);
          await key('Enter', 'Enter', 13);
          check(await evaluate(`location.hash === '${expected}' && !!document.querySelector('.sessions-view')`), 'Filtered contribution native navigation failed');
          await returnOverview(); await selectOverview('Period', mode);
          await clickTarget('.trend > li:last-child .trend-period');
          await clickTarget('.trend-all-sessions[href="#/sessions/2026-06-02"] time');
          check(await evaluate(`location.hash === '#/sessions/2026-06-02'`), 'All-day contribution pointer navigation inherited filters');
        } else {
          const selector = '.trend-day[href="' + expected + '"]';
          if (mode === 'day') await clickTarget(selector + ' time');
          else {
            await key('Tab', 'Tab', 9);
            await evaluate(`document.querySelector(${JSON.stringify(selector)}).focus()`);
            check(await evaluate(`document.activeElement.tagName === 'A' && getComputedStyle(document.activeElement).outlineStyle !== 'none'`), 'Day link lost native visible focus');
            await key('Enter', 'Enter', 13);
          }
          check(await evaluate(`location.hash === '${expected}' && !!document.querySelector('.sessions-view')`), 'Day native navigation lost query');
          await returnOverview(); await selectOverview('Period', mode);
          await clickTarget(selector + ' .trend-amount');
          check(await evaluate(`location.hash === '${expected}'`), 'Amount is no longer part of day link');
        }
      }
    }
  }
  check(await evaluate(`window.probeErrors.length === 0 && performance.getEntriesByType('resource').length === 0`), 'Page error, CSP violation or resource request');
  console.log('Trusted Tab/Shift-Tab, Enter/Space, AX states, isolation and offline CSP passed');
} finally {
  browser.kill();
  await new Promise(resolve => browser.once('exit', resolve));
}
"""
        with tempfile.TemporaryDirectory(dir=DIST, prefix="keyboard-test-") as name:
            directory = Path(name)
            output = directory / "preview.html"
            write_dashboard(output, document)
            profile = directory / "profile"
            profile.mkdir(mode=0o700)
            result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script,
                                     shutil.which("chromium"), str(profile), output.as_uri() + route],
                                    capture_output=True, text=True, timeout=45,
                                    env={"PATH": os.defpath, "HOME": str(profile),
                                         "XDG_CONFIG_HOME": str(profile / "config"),
                                         "XDG_CACHE_HOME": str(profile / "cache"), "LANG": "C.UTF-8"})
            self.assertEqual(result.returncode, 0, result.stderr[-3000:])
            self.assertIn("offline CSP passed", result.stdout)

    def test_file_ranking_switch_single_actual_provider_and_model_slots(self):
        data = top_models_fixture()
        data["rows"] = [dict(row, provider="openai-codex") for row in data["rows"]
                        if row["day"] == "2026-06-17"]
        data["stats"].update(oldestDay="2026-06-17")
        for viewport in ("1280,900", "390,844"):
            with self.subTest(viewport=viewport):
                self.browser(render_react_dashboard(data, DIST), PROVIDER_RANKING_CHECKS + r"""
check(rankingButtons()[0].textContent === 'Models' && rankingButtons()[0].getAttribute('aria-pressed') === 'true', 'Models is not the default');
for (const mode of ['Daily', 'Weekly']) {
  await pickPeriod(mode);
  const start = mode === 'Daily' ? '2026-06-17' : '2026-06-15', end = mode === 'Daily' ? start : '2026-06-21';
  checkModels(start, end, 3);
  check(modelPanel().querySelectorAll('.model-empty-slot').length === 2, 'Eligible model empty rows missing');
  await switchRanking('Providers'); checkProviderRanking(start, end);
  check(modelPanel().querySelectorAll('.ranked-providers li').length === 1 && modelPanel().querySelector('.ranked-providers strong').textContent === 'openai-codex', 'Single real provider gained fictional competitors or a brand name');
  await switchRanking('Models'); checkModels(start, end, 3);
}
""", viewport=viewport)

    def test_file_ranking_providers_bigint_top_five_filters_and_raw_labels(self):
        for viewport in ("1280,900", "390,844"):
            with self.subTest(viewport=viewport):
                self.browser(render_react_dashboard(provider_fixture(), DIST), PROVIDER_RANKING_CHECKS + r"""
await chooseDay('2026-06-02'); await switchRanking('Providers'); checkProviderRanking('2026-06-02', '2026-06-02');
check(modelPanel().querySelector('.ranked-providers strong').textContent === 'Zeta', 'One-token provider lead beyond Number range lost');
check([...modelPanel().querySelectorAll('.ranked-providers strong')].at(-1).textContent === 'Delta', 'Top-five tie boundary incorrect');
await choose('Model', JSON.stringify('shared')); checkProviderRanking('2026-06-02', '2026-06-02');
check(modelPanel().querySelector('.ranked-providers strong').textContent === 'Alpha', 'Model-filter provider ranking stale');
await choose('Provider', JSON.stringify('Zeta')); await choose('Model', JSON.stringify('child-model'));
checkProviderRanking('2026-06-02', '2026-06-02'); check(total() === '2', 'Child/model filter not exact');
await choose('Provider', JSON.stringify('')); checkProviderRanking('2026-06-02', '2026-06-02');
check(modelPanel().querySelector('.ranked-providers strong').textContent === '(unspecified provider)', 'Blank provider missing label');
await switchRanking('Models');
check(modelPanel().querySelector('.model-identity > span').textContent === '(unspecified provider)', 'Switch back lost blank provider filter');
""", viewport=viewport)
        data = provider_fixture()
        template = data["rows"][0]
        data["rows"] = [dict(template, provider=name, input=10**350, output=0,
                             cacheRead=0, cacheWrite=0) for name in ("a", "Z", " ", "")]
        self.browser(render_react_dashboard(data, DIST), PROVIDER_RANKING_CHECKS + r"""
await switchRanking('Providers'); checkProviderRanking('2026-06-02', '2026-06-02');
check([...modelPanel().querySelectorAll('.ranked-providers strong')].map(node => node.textContent).join('|') === '(unspecified provider)|(unspecified provider)|Z|a', 'Raw tie order normalized or localized identifiers');
""")

    def test_file_ranking_providers_all_periods_inclusive_scope(self):
        self.browser(render_react_dashboard(provider_fixture(), DIST), PROVIDER_RANKING_CHECKS + r"""
await switchRanking('Providers'); checkProviderRanking('2026-06-04', '2026-06-04');
await pickPeriod('Weekly'); await chooseDate('Week containing', '2026-06-02'); checkProviderRanking('2026-06-01', '2026-06-07');
await pickPeriod('Monthly'); checkProviderRanking('2026-06-01', '2026-06-30');
await choose('Ending month', '2026-05-01'); checkProviderRanking('2026-05-01', '2026-05-31');
await pickPeriod('Custom range'); await chooseDate('Start', '2026-05-01'); await chooseDate('End', '2026-06-02');
checkProviderRanking('2026-05-01', '2026-06-02'); check(trend().length === 31, 'Long range context changed');
await chooseDate('Start', '2026-06-01'); checkProviderRanking('2026-06-01', '2026-06-02');
await chooseDate('Start', '2026-06-02'); checkProviderRanking('2026-06-02', '2026-06-02');
await pickPeriod('All time'); checkProviderRanking();
await choose('Model', JSON.stringify('shared')); checkProviderRanking();
await choose('Provider', JSON.stringify('Zeta')); checkProviderRanking();
await switchRanking('Models'); checkModels(null, null, 2);
""")

    def test_file_ranking_providers_zero_missing_invalid_empty_and_recovery(self):
        self.browser(render_react_dashboard(provider_fixture(), DIST), PROVIDER_RANKING_CHECKS + r"""
await switchRanking('Providers');
await chooseDay('2026-05-31'); checkProviderRanking('2026-05-31', '2026-05-31');
await chooseDay('2026-05-30'); checkProviderRanking('2026-05-30', '2026-05-30');
await chooseDay('2026-06-05'); checkProviderRanking('2026-06-05', '2026-06-05');
await chooseDay('');
check(total() === 'Unavailable' && modelPanel().innerText.includes('Top providers unavailable: invalid range') && !modelPanel().querySelector('.ranked-providers'), 'Invalid day rendered stale providers');
await pickPeriod('Custom range'); await chooseDate('Start', '2026-06-05');
check(total() === 'Unavailable' && modelPanel().innerText.includes('Top providers unavailable: invalid range'), 'Invalid range rendered stale providers');
await switchRanking('Models');
check(modelPanel().innerText.includes('Top models unavailable: invalid range') && !modelPanel().querySelector('.model-empty-slot'), 'Invalid model state not restored');
await switchRanking('Providers'); await chooseDate('End', '2026-06-05'); checkProviderRanking('2026-06-05', '2026-06-05');
await pickPeriod('Daily'); checkProviderRanking('2026-06-04', '2026-06-04');
""")
        data = provider_fixture()
        data["rows"] = []
        self.browser(render_react_dashboard(data, DIST), PROVIDER_RANKING_CHECKS + r"""
await switchRanking('Providers'); checkProviderRanking();
await pickPeriod('All time'); checkProviderRanking();
await switchRanking('Models'); checkModels(null, null, 0);
""", viewport="390,844")

    def test_file_provider_bigint_ranking_other_and_responsive_order(self):
        for viewport in ("1280,900", "390,844"):
            with self.subTest(viewport=viewport):
                self.browser(render_react_dashboard(provider_fixture(), DIST), PROVIDER_CHECKS + r"""
await chooseDay('2026-06-02'); checkProviders('2026-06-02', '2026-06-02');
check(providerItems()[0].querySelector('.provider-name').textContent === 'Zeta', 'One-token lead lost above Number precision');
check(providerItems()[4].querySelector('.provider-name').textContent === 'Delta', 'Exact tie not ordered deterministically');
check(providerItems()[5].textContent.includes('Other (2 providers)'), 'Tail provider count incorrect');
const trendPanel = document.querySelector('[aria-labelledby="trend-title"]');
const modelPanel = document.querySelector('[aria-labelledby="models-title"]');
check(trendPanel.nextElementSibling === providerPanel(), 'Provider chart must immediately follow trend');
const a = trendPanel.getBoundingClientRect(), b = providerPanel().getBoundingClientRect(), c = modelPanel.getBoundingClientRect();
check(b.top >= a.bottom && b.left === a.left && b.width === a.width, 'Chart not below trend in same column');
check(window.innerWidth > 1050 ? c.left >= b.right : c.top >= b.bottom, 'Top models order changed');
check(document.documentElement.scrollWidth <= window.innerWidth, 'Long labels/huge tokens overflow mobile');
check(!trendPanel.querySelector(':scope > p:not(.empty)') && document.querySelector('.page-heading .badge').textContent.includes('Snapshot as of'), 'PUD-20 compact copy/as-of regressed');
""", viewport=viewport)

    def test_file_provider_filters_blank_model_and_exact_ties(self):
        self.browser(render_react_dashboard(provider_fixture(), DIST), PROVIDER_CHECKS + r"""
await chooseDay('2026-06-02');
await choose('Provider', JSON.stringify('Zeta')); checkProviders('2026-06-02', '2026-06-02');
await choose('Model', JSON.stringify('child-model')); checkProviders('2026-06-02', '2026-06-02');
check(total() === '2', 'Linked child contribution lost or double counted');
await choose('Provider', JSON.stringify('')); checkProviders('2026-06-02', '2026-06-02');
check(providerItems()[0].textContent.includes('(unspecified provider)'), 'Blank provider is unlabeled');
await choose('Provider', ''); await choose('Model', JSON.stringify('shared'));
checkProviders('2026-06-02', '2026-06-02');
check(providerItems()[0].querySelector('.provider-name').textContent === 'Alpha', 'Model filter failed to change leader by one token');
""")
        data = provider_fixture()
        data["rows"] = [row for row in data["rows"] if row["provider"] in ("Delta", "Echo")]
        self.browser(render_react_dashboard(data, DIST), PROVIDER_CHECKS + r"""
checkProviders('2026-06-02', '2026-06-02');
check(providerItems()[0].textContent.includes('Delta') && providerItems()[0].textContent.includes('Most used · tied'), 'Tied leader not disclosed');
check(providerItems().every(node => node.querySelector('.provider-share').textContent === '50.00%'), 'Equal shares incorrect');
""")

    def test_file_provider_day_custom_week_all_and_future_scope(self):
        self.browser(render_react_dashboard(provider_fixture(), DIST), PROVIDER_CHECKS + r"""
await chooseDay('2026-06-02'); checkProviders('2026-06-02', '2026-06-02');
await chooseDay('2026-06-01'); checkProviders('2026-06-01', '2026-06-01');
await pickPeriod('Custom range');
await chooseDate('Start', '2026-05-01'); await chooseDate('End', '2026-06-02');
checkProviders('2026-05-01', '2026-06-02');
check(trend().length === 31, 'Long custom context no longer bounded');
await pickPeriod('Weekly'); await chooseDate('Week containing', '2026-06-02');
checkProviders('2026-06-01', '2026-06-07');
check(providerPanel().textContent.includes('Selection includes dates after snapshot'), 'Future-range caveat missing');
await pickPeriod('All time'); checkProviders();
await pickPeriod('Daily'); checkProviders('2026-06-04', '2026-06-04');
check(total() === '17' && providerPanel().textContent.includes('future dates are not evidence of inactivity'), 'Future recorded tokens diverge from cards');
""")

    def test_file_provider_zero_missing_invalid_and_empty(self):
        self.browser(render_react_dashboard(provider_fixture(), DIST), PROVIDER_CHECKS + r"""
await chooseDay('2026-05-31'); checkProviders('2026-05-31', '2026-05-31');
await chooseDay('2026-05-30'); checkProviders('2026-05-30', '2026-05-30');
await chooseDay('2026-06-05'); checkProviders('2026-06-05', '2026-06-05');
check(providerPanel().textContent.includes('after snapshot'), 'Future missing day not identified');
await pickPeriod('Custom range'); await chooseDate('Start', '2026-06-05');
check(providerPanel().textContent.includes('unavailable: invalid range') && !providerItems().length && !providerPanel().querySelector('[role="img"]'), 'Invalid range showed stale/fake provider shares');
await chooseDate('End', '2026-06-05'); checkProviders('2026-06-05', '2026-06-05');
""")
        data = provider_fixture()
        data["rows"] = []
        self.browser(render_react_dashboard(data, DIST), PROVIDER_CHECKS + r"""
checkProviders();
check(!providerItems().length, 'Session summaries created provider usage without daily rows');
""")

    def test_file_overview_navigation_csp_precision_and_privacy(self):
        self.browser(render_react_dashboard(fixture(), DIST), OVERVIEW_CONTROLS + r"""
check(select('Period').value === 'day' && total() === '11', 'Overview must start on latest recorded day');
await pickPeriod('All time');
check(document.querySelector('h1')?.textContent === 'Overview', 'Overview did not render');
check(getComputedStyle(document.body).margin === '0px', 'Extracted CSS did not apply');
check(getComputedStyle(document.documentElement).backgroundColor === 'rgb(13, 16, 23)', 'Dark theme missing');
check(document.querySelector('.metric strong')?.textContent === '9,007,199,254,741,013', 'BigInt precision lost');
check(document.querySelectorAll('.metric strong')[1]?.textContent === '4', 'Output total incorrect');
check(document.querySelectorAll('.metric strong')[3]?.textContent === '$0.3750', 'Recorded cost incorrect');
check(document.querySelectorAll('.trend li').length === 2, 'Daily trend missing');
check(document.querySelectorAll('.models li').length === 2, 'Model ranking missing');
check(document.querySelector('.models strong')?.textContent === '</script><img src=x onerror=alert(1)>', 'Label escaped incorrectly');
check(!document.querySelector('img'), 'Hostile label became markup');
check(!document.querySelector('.quality, footer'), 'Overview global quality/footer still mounted');
check(!document.querySelector('.company-section'), 'Dense companies on overview');
for (const name of ['Companies', 'Agents', 'Sessions', 'Stats']) {
  document.querySelector(`nav a[href="#/${name.toLowerCase()}"]`).click();
  await pause();
  check(document.querySelector('h1')?.textContent === name, 'Hash route failed: ' + name);
  check(document.querySelector('[aria-current="page"]')?.textContent === name, 'Active link wrong');
  if (name === 'Companies') {
    check(document.querySelectorAll('.company-card').length === 5, 'Five company slots missing');
  } else if (name === 'Agents') {
    check(document.querySelectorAll('.agent-card').length === 2, 'Configured roles missing');
  } else if (name === 'Sessions') {
    check(document.querySelector('.sessions-view')?.textContent.includes('No measured session files'), 'Empty Sessions not honest');
    check(!document.querySelector('.placeholder'), 'Sessions still a placeholder');
  } else {
    check(document.querySelector('.stats-view') && !document.querySelector('.placeholder'), 'Stats not implemented');
  }
  check(!document.querySelector('.overview-controls, .overview-grid'), 'Overview leaked into another view');
}
window.location.hash = '#/unknown';
await pause();
check(document.querySelector('h1')?.textContent === 'View not found', 'Unknown route not explicit');
document.querySelector('a[href="#/overview"]').click();
await pause();
check(document.querySelector('h1')?.textContent === 'Overview', 'Return navigation failed');
document.querySelector('.skip-link').click();
await pause();
check(document.activeElement?.id === 'main', 'Keyboard skip link failed');
check(document.querySelector('h1')?.textContent === 'Overview', 'Skip link changed route');
""")

    def test_file_stats_monday_calendar_precision_states_accessibility_and_mobile(self):
        for viewport, browser_timezone in (("1280,900", "UTC"), ("500,900", "America/Los_Angeles")):
            with self.subTest(viewport=viewport, browser_timezone=browser_timezone):
                data = stats_fixture()
                # Ten distinct recorded days; same-day owner rows are summed once.
                expected = format(9007199254740993 * 12 + 33, ",")
                self.browser(render_react_dashboard(data, DIST),
                             "const expected = " + json.dumps(expected) + ";\n" + r"""
window.location.hash = '#/stats'; await pause();
const view = document.querySelector('.stats-view');
check(view && !document.querySelector('.placeholder'), 'Stats still a placeholder');
check(document.querySelector('nav [aria-current="page"]').textContent === 'Stats', 'Stats navigation inactive');
const weeks = [...view.querySelectorAll('.heatmap-week')];
const cells = [...view.querySelectorAll('.heatmap-week .heatmap-day')];
check(weeks.length === 52 && cells.length === 364, 'Calendar must be exactly 52 by 7');
check(cells[0].dataset.day === '2023-03-06' && cells.at(-1).dataset.day === '2024-03-03', '52-week range incorrect');
for (const [index, week] of weeks.entries()) {
  const days = [...week.querySelectorAll('.heatmap-day')];
  check(days.length === 7 && new Date(days[0].dataset.day + 'T00:00:00Z').getUTCDay() === 1, 'Week does not start Monday');
  check(week.getAttribute('aria-label') === 'Week of ' + days[0].dataset.day, 'Week label missing');
  for (const [offset, cell] of days.entries()) {
    const expectedDay = new Date(Date.parse('2023-03-06T00:00:00Z') + (index * 7 + offset) * 86400000).toISOString().slice(0, 10);
    check(cell.dataset.day === expectedDay, 'Calendar skipped/repeated a day');
    check(cell.getAttribute('aria-label').startsWith(expectedDay + ' · ') && cell.getAttribute('role') === 'img', 'Day lacks accessible label');
  }
}
const cell = day => cells.find(node => node.dataset.day === day);
check(cell('2023-12-31') && cell('2024-01-01') && cell('2024-02-29'), 'Leap/year boundary lost');
check(cell('2024-03-01').dataset.state === 'zero' && cell('2024-03-01').getAttribute('aria-label').includes('Recorded zero · 0 tokens'), 'Snapshot-local zero day lost');
check(cell('2024-02-28').dataset.state === 'missing' && cell('2024-02-28').getAttribute('aria-label').includes('not zero'), 'Missing day confused with recorded zero');
for (const day of ['2024-03-02', '2024-03-03']) {
  check(cell(day).dataset.state === 'future' && cell(day).getAttribute('aria-label').includes('Future'), 'Future incorrectly measured or inactive');
  check(!cell(day).getAttribute('aria-label').includes('0 tokens'), 'Future appeared as zero usage');
}
for (const [day, level] of [['20', '1'], ['21', '2'], ['22', '3'], ['23', '4'], ['24', '4']]) {
  check(cell('2024-02-' + day).dataset.level === level, 'BigInt intensity threshold rounded: ' + day);
}
check(cell('2024-02-29').getAttribute('aria-label').includes('9,007,199,254,741,013 tokens'), 'Daily owner totals lost precision or token categories');
check(cell('2024-02-29').getAttribute('aria-label').includes('$0.7500'), 'Same-day cost not summed');
check([...view.querySelectorAll('.metric strong')].map(node => node.textContent).join('|') === expected + '|$0.8750|10', 'Headlines double counted sessions, future or outside rows');
check(view.textContent.includes('Estimate') && view.textContent.includes('not a bill'), 'Cost not labeled estimate');
check(view.textContent.includes('2023-03-06 through 2024-03-01') && view.textContent.includes('2024-03-01T00:15:00+14:00') && view.textContent.includes('Pacific/Kiritimati'), 'Range/timezone/as-of not explicit');
check(view.textContent.includes('Historical attribution may be incomplete'), 'Coverage warning missing');
check(document.querySelector('.quality').textContent.includes('2 events with future timestamps included'), 'Quality future notice changed');
const legend = view.querySelector('[aria-label="Heatmap legend"]');
check(legend.querySelectorAll('li').length === 7 && ['Missing data', 'Recorded zero', 'Future', '>0–25%', '>75–100%'].every(text => legend.textContent.includes(text)), 'Legend lacks state/intensity labels');
const appearances = ['2024-02-28', '2024-03-01', '2024-03-02'].map(day => {
  const style = getComputedStyle(cell(day)); return [style.backgroundColor, style.backgroundImage, style.borderStyle].join('|');
});
check(new Set(appearances).size === 3, 'Missing, zero and future visually indistinguishable');
const scroller = view.querySelector('.heatmap-scroll');
scroller.focus(); check(document.activeElement === scroller && getComputedStyle(scroller).outlineStyle !== 'none', 'Heatmap scrolling lacks visible keyboard focus');
if (window.innerWidth === 500) {
  check(scroller.scrollWidth > scroller.clientWidth, 'Narrow heatmap must scroll horizontally');
  scroller.scrollLeft = scroller.scrollWidth; check(scroller.scrollLeft > 0, 'Final weeks unreachable');
}
const disclosure = view.querySelector('.stats-day-labels');
const summary = disclosure.querySelector('summary');
summary.focus(); check(document.activeElement === summary && getComputedStyle(summary).outlineStyle !== 'none', 'Day labels lack keyboard access');
summary.click(); await pause();
check(disclosure.open && disclosure.querySelectorAll('tbody tr').length === 364, 'Readable day labels missing');
check(disclosure.textContent.includes('Thursday') && disclosure.textContent.includes('9,007,199,254,741,013'), 'Readable labels lack weekday or exact totals');
check(document.documentElement.scrollWidth <= window.innerWidth, 'Stats overflows page at narrow viewport');
window.location.hash = '#/overview'; await pause();
check(!document.querySelector('.stats-view') && document.querySelector('h1').textContent === 'Overview', 'Stats leaked into Overview');
""", viewport=viewport, browser_timezone=browser_timezone)

    def test_file_stats_year_boundary_empty_and_future_only_rows(self):
        for as_of, start, end, future_days in (
            ("2026-01-01T23:30:00-12:00", "2025-01-06", "2026-01-04", 3),
            ("2026-01-04T00:30:00+14:00", "2025-01-06", "2026-01-04", 0),
            ("2026-01-05T00:30:00+14:00", "2025-01-13", "2026-01-11", 6),
        ):
            with self.subTest(as_of=as_of):
                data = fixture()
                data["rows"] = [] if future_days == 0 else [{**data["rows"][0], "day": end}]
                data["stats"].update(asOf=as_of, oldestDay=None, newestDay=None)
                setup = "const expected = " + json.dumps(dict(start=start, end=end, future=future_days)) + ";\n"
                self.browser(render_react_dashboard(data, DIST), setup + r"""
window.location.hash = '#/stats'; await pause();
const view = document.querySelector('.stats-view');
const cells = [...view.querySelectorAll('.heatmap-week .heatmap-day')];
check(cells.length === 364 && cells[0].dataset.day === expected.start && cells.at(-1).dataset.day === expected.end, 'Year-boundary calendar drifted');
check(cells.filter(node => node.dataset.state === 'future').length === expected.future, 'Partial week future days wrong');
check(cells.filter(node => node.dataset.state === 'missing').length === 364 - expected.future, 'Empty history became recorded zero');
check([...view.querySelectorAll('.metric strong')].map(node => node.textContent).join('|') === '0|$0.0000|0', 'Future-only/empty rows invented measured usage');
""")

    def test_file_stats_unbounded_bigint_and_calendar_limits(self):
        data = fixture()
        data["rows"] = [{**data["rows"][1], "input": 10**400, "cost": 1e308},
                        {**data["rows"][1], "input": 0, "cost": 1e308}]
        self.browser(render_react_dashboard(data, DIST), r"""
window.location.hash = '#/stats'; await pause();
const view = document.querySelector('.stats-view');
const maximum = view.querySelector('[data-day="2026-06-02"]');
check(maximum.dataset.level === '4' && maximum.dataset.state === 'measured', 'Unbounded BigInt shade failed');
check(view.querySelector('.metric strong').textContent.replaceAll(',', '') === String(10n ** 400n + 8n), 'Unbounded headline precision lost');
check(view.querySelectorAll('.metric strong')[1].textContent === 'Unavailable', 'Overflowed cost falsely precise');
view.querySelector('.stats-day-labels summary').click(); await pause();
check(document.documentElement.scrollWidth <= window.innerWidth, 'Unbounded token label overflows mobile');
""", viewport="500,900")
        for as_of in ("0001-01-01T00:00:00Z", "0001-06-01T00:00:00Z", "9999-12-31T00:00:00Z"):
            with self.subTest(as_of=as_of):
                data = fixture()
                data["rows"] = []
                data["stats"].update(asOf=as_of, oldestDay=None, newestDay=None)
                self.browser(render_react_dashboard(data, DIST), r"""
window.location.hash = '#/stats'; await pause();
const view = document.querySelector('.stats-view');
check(view && view.textContent.includes('Calendar range unavailable'), 'Out-of-bounds calendar crashed or silently truncated');
check(view.textContent.includes('No partial heatmap or 52-week totals'), 'Bounded-span fallback unexplained');
check(!view.querySelector('.heatmap-day, .metric'), 'Unsupported range rendered misleading totals');
""")

    def test_file_sessions_index_multiday_privacy_and_mobile(self):
        for viewport in ("1280,900", "500,900"):
            with self.subTest(viewport=viewport):
                self.browser(render_react_dashboard(session_fixture(), DIST), r"""
window.location.hash = '#/sessions'; await pause();
const view = document.querySelector('.sessions-view');
check(document.querySelector('h1').textContent === 'Sessions', 'Sessions index missing');
check(document.querySelector('nav [aria-current="page"]').textContent === 'Sessions', 'Sessions nav inactive');
check(view.querySelector('.sessions-count').textContent === '2 measured session files with attributed usage', 'File count mislabeled');
check(!view.textContent.includes('Not a count of unique conversations'), 'Sessions identity explanation still mounted');
const groups = [...view.querySelectorAll('.session-day-group')];
check(groups.length === 2 && groups.every(node => !node.open), 'Day groups must start collapsed');
check(!view.querySelector('.session-file'), 'Collapsed groups mounted a flat file list');
check(groups.map(node => node.querySelector('time').dateTime).join() === '2026-06-02,2026-06-01', 'Groups are not newest first');
check(groups[0].querySelector('h2').textContent === 'Tuesday · 2026-06-02 · Yesterday', 'Weekday/relative label wrong');
const metrics = node => [...node.querySelectorAll(':scope > .session-metrics dd')].map(item => item.textContent).join('|');
check(metrics(view.querySelector('.sessions-totals')) === '9,007,199,254,741,013|9,007,199,254,741,000|4|7 (0.00%)|2|$0.3750', 'Index precision or double counting');
for (const group of groups) {
  const heading = group.querySelector('summary');
  heading.focus(); check(document.activeElement === heading && getComputedStyle(heading).outlineStyle !== 'none', 'Group keyboard focus missing');
  check(heading.getBoundingClientRect().height >= 44, 'Group touch target too small');
  heading.click(); await pause();
  check(group.open, 'Day disclosure failed');
  for (const file of group.querySelectorAll('.session-file')) {
    const summary = file.querySelector('summary');
    check(!file.querySelector('details').open, 'Compact card must start collapsed');
    summary.focus(); check(document.activeElement === summary && getComputedStyle(summary).outlineStyle !== 'none', 'Card focus missing');
    summary.click(); await pause();
    check(file.querySelector('details').open, 'File disclosure failed');
  }
}
check(groups[0].querySelectorAll('.session-file').length === 2 && groups[1].querySelectorAll('.session-file').length === 1, 'Multiday file not counted once per contributing day');
check(groups[0].querySelector('.session-group-count').textContent === '2 measured session files with attributed usage', 'Group file count mislabeled');
check(metrics(groups[0].querySelector('.session-group-totals')) === '11|7|1|3 (30.00%)|0|$0.2500', 'Day group includes lifetime totals');
check(metrics(groups[1].querySelector('.session-group-totals')) === '9,007,199,254,741,002|9,007,199,254,740,993|3|4 (0.00%)|2|$0.1250', 'Older day total lost precision');
const files = [...groups[0].querySelectorAll('.session-file')];
check(files.every(file => file.querySelector('.session-owner').textContent === 'ID unavailable'), 'Missing header ID invented');
check(files[0].querySelector('.session-name-source').textContent === 'Parent' && files[1].querySelector('.session-name-source').textContent === 'Non-SDD role: worker', 'Owners incorrect');
const earlier = groups[1].querySelector('.session-day'), later = files[0].querySelector('.session-day');
check(earlier.textContent.includes('</script><img src=x onerror=alert(1)>') && !earlier.textContent.includes('zero-synthetic'), 'Day-one models mixed');
check(later.textContent.includes('zero-synthetic') && !later.textContent.includes('9,007,199'), 'Other-day tokens leaked');
check(metrics(later.querySelector('.session-models > li')) === '0|0|0|0 (N/A)|0|$0.0000', 'Accepted zero usage lost');
check(!document.querySelector('img'), 'Model became executable markup');
for (const forbidden of ['PRIVATE_', 'sessionPath', 'stableKey', '/PRIVATE_PATH']) check(!document.getElementById('root').textContent.includes(forbidden), 'Private metadata rendered: ' + forbidden);
check(!view.querySelector('.session-file [id], [data-session-id], [data-session-path]'), 'Synthetic or source identifiers exposed');
check([...view.querySelectorAll('[id]')].every(node => ['session-name-help', 'session-share-help'].includes(node.id)), 'Unexpected identity outside fixed control help');
check(document.documentElement.scrollWidth <= window.innerWidth, 'Sessions overflow viewport');
window.location.hash = '#/stats'; await pause();
check(document.querySelector('h1').textContent === 'Stats' && document.querySelector('.stats-view') && !document.querySelector('.placeholder'), 'Stats route missing');
check(!document.querySelector('.sessions-view'), 'Sessions leaked into Stats');
""", viewport=viewport)

    def test_file_sessions_selected_day_declutter_and_route_boundary(self):
        data = session_filter_fixture()
        data["stats"].update(asOf="2026-06-02T00:15:00+14:00", timezone="Pacific/Kiritimati")
        for viewport in ("1280,900", "500,900"):
            with self.subTest(viewport=viewport):
                self.browser(render_react_dashboard(data, DIST), SESSION_FILTER_CONTROLS + r"""
const navigate = async route => { location.hash = route; await pause(); };
const globalContext = () => {
  check(document.querySelector('.quality')?.textContent.includes('1 unlinked child files omitted'), 'Global quality lost outside valid day');
  check(document.querySelector('footer')?.textContent.includes('2026-06-02T00:15:00+14:00'), 'Global footer lost outside valid day');
};
const descriptions = () => {
  for (const input of view().querySelectorAll('.session-controls input')) {
    const ids = input.getAttribute('aria-describedby').split(' ');
    for (const id of ids) {
      const node = document.getElementById(id);
      check(node && document.querySelectorAll('#' + id).length === 1 && node.textContent.trim().length > 20, 'Description missing, empty or duplicated');
      const style = getComputedStyle(node), box = node.getBoundingClientRect();
      check(style.display !== 'none' && style.visibility === 'visible' && !node.closest('[hidden], [aria-hidden="true"]'), 'Guidance unavailable to assistive technology');
      if (id.endsWith('-help')) {
        check(style.position === 'absolute' && style.clipPath === 'inset(50%)' && box.width <= 1 && box.height <= 1, 'Long guidance still visually occupies Sessions page');
      } else {
        check(style.clipPath === 'none' && box.width > 1 && box.height > 1, 'Invalid error is hidden');
      }
    }
    input.focus();
    check(document.activeElement === input && getComputedStyle(input).outlineStyle !== 'none', 'Input keyboard focus missing');
  }
};
const dayPresentation = expected => {
  check(!document.querySelector('.quality, footer'), 'Selected-day global context mounted');
  check(!view().textContent.includes('Not a count of unique conversations') && !view().textContent.includes('not verified human-authored'), 'Selected-day identity/privacy paragraphs still mounted');
  const heading = view().querySelector('.page-heading');
  check(heading.textContent.includes('As of 2026-06-02 00:15') && heading.textContent.includes('Timezone: Pacific/Kiritimati'), 'Snapshot-local heading context missing or converted to browser timezone');
  check(heading.querySelector('time').dateTime === '2026-06-02T00:15:00+14:00', 'As-of timestamp lost offset');
  const node = view().querySelector('.sessions-count');
  check(count() === expected && node.textContent === expected + ' measured session files with attributed usage', 'Count hardcoded or carries extra prose');
  for (const element of [node, node.querySelector('strong')]) {
    const style = getComputedStyle(element), box = element.getBoundingClientRect();
    check(style.display !== 'none' && style.visibility === 'visible' && style.clipPath === 'none' && box.width > 1 && box.height > 1, 'Real dynamic count hidden');
    for (const pseudo of ['::before', '::after']) check(['none', 'normal', '""'].includes(getComputedStyle(element, pseudo).content), 'Count replaced by generated content');
  }
  check(view().querySelector('.session-controls') && view().querySelector('.sessions-totals') && view().querySelector('.session-reconciliation'), 'Day controls or totals removed');
  check(view().querySelectorAll('.session-file').length === expected, 'Count differs from visible measured files');
  check(document.documentElement.scrollWidth <= innerWidth, 'Day presentation overflows viewport');
  descriptions();
};
await navigate('#/sessions/2026-06-02');
dayPresentation(13);
check(group('2026-06-02').querySelector('h2').textContent.endsWith('Today'), 'Relative heading no longer snapshot-local');
check(view().textContent.includes('Displayed contributions match'), 'Default day reconciliation lost');
await search('Alpha'); dayPresentation(3); comparison();
await threshold('95'); dayPresentation(2); comparison();
await threshold('95.');
descriptions();
check(!view().querySelector('.sessions-count, .sessions-totals, .session-file'), 'Invalid threshold shows count or stale files');
check(view().querySelector('#session-share-error')?.getAttribute('role') === 'alert' && view().querySelector('.sessions-unavailable')?.textContent.includes('UNAVAILABLE'), 'Invalid feedback missing');
check(!document.querySelector('.quality, footer'), 'Invalid input changed valid route presentation');
await clear(); dayPresentation(13);
check(!control('Cache-read share above (%)').getAttribute('aria-describedby').includes('session-share-error'), 'Cleared error leaves dangling description');
await navigate('#/sessions/2026-06-02?provider=' + encodeURIComponent('provider +β&%?#') + '&model=target');
dayPresentation(1);
check(view().querySelector('.session-filter-scope').textContent.includes('provider +β&%?#') && view().querySelector('.session-filter-scope').textContent.includes('Model: target'), 'Provider/model scope hidden');
check(view().textContent.includes('Displayed contributions match'), 'Qualified day reconciliation lost');
await navigate('#/sessions');
descriptions();
check(count() === 13 && !view().textContent.includes('Not a count of unique conversations') && !view().textContent.includes('not verified human-authored'), 'All-days identity/privacy paragraphs still mounted');
check(!view().textContent.includes('Open day page shows all sessions'), 'All-days navigation paragraph still mounted');
check(!document.querySelector('.quality, footer'), 'All-days global context still mounted');
check(view().querySelector('.page-heading').textContent.includes('As of 2026-06-02 00:15') && view().querySelector('.page-heading').textContent.includes('Timezone: Pacific/Kiritimati'), 'All-days snapshot context missing');
await threshold('invalid'); descriptions(); await clear();
await navigate('#/overview');
check(!document.querySelector('.quality, footer'), 'Overview global context still mounted');
check(document.querySelector('.page-heading').textContent.includes('Pacific/Kiritimati'), 'Overview snapshot timezone missing');
for (const route of ['#/stats', '#/companies', '#/agents', '#/unknown', '#/overview/extra', '#/sessions/2026-02-30', '#/sessions/2026-06-02?provider=unknown', '#/sessions/2026-06-02?model=unknown', '#/sessions/2026-06-02?model=target&model=target', '#/sessions/2026-06-02/extra']) {
  await navigate(route); globalContext();
  if (route.startsWith('#/sessions/')) check(document.querySelector('h1').textContent === 'View not found', 'Invalid route accepted as selected day');
}
""", viewport=viewport, browser_timezone="America/Los_Angeles")

    def test_file_sessions_declutter_preserves_unknown_and_zero_filter_context(self):
        for viewport in ("1280,900", "500,900"):
            with self.subTest(viewport=viewport):
                self.browser(render_react_dashboard(session_fixture(), DIST), SESSION_FILTER_CONTROLS + r"""
window.location.hash = '#/sessions/2026-06-02'; await pause();
check(count() === 2 && owners('2026-06-02').length === 2, 'Recorded zero file excluded without threshold');
check(view().textContent.includes('Recorded zero usage is included') && view().textContent.includes('missing records and zero totals do not establish inactivity'), 'Day unknown/zero caveat removed');
await threshold('0');
check(count() === 1 && owners('2026-06-02').length === 1, 'Threshold includes zero-denominator file');
check(view().textContent.includes('Recorded zero usage may be excluded by name/share filters'), 'Filtered zero caveat hidden');
comparison();
await search('unknown name');
check(count() === 0 && group('2026-06-02').querySelector('.empty').textContent.includes('No measured session files match'), 'Unknown name silently shows all or hides empty warning');
comparison();
window.location.hash = '#/sessions/2026-06-04?provider=synthetic'; await pause();
await threshold('95');
check(!document.querySelector('.quality, footer') && count() === 0, 'Missing qualified day presentation incorrect');
check(view().textContent.includes('No recorded daily rows for this date and provider/model selection') && view().textContent.includes('missing records and zero totals do not establish inactivity'), 'Missing-date warning removed');
check(view().querySelector('.page-heading').textContent.includes('As of 2026-06-03 12:00') && !group('2026-06-04').querySelector('h2').textContent.includes('Today'), 'Future day lacks truthful snapshot context');
comparison();
check(document.documentElement.scrollWidth <= innerWidth, 'Unknown/zero warning overflows viewport');
""", viewport=viewport)

    def test_file_sessions_filters_all_days_exact_share_names_and_mobile(self):
        for viewport in ("1280,900", "500,900"):
            with self.subTest(viewport=viewport):
                self.browser(render_react_dashboard(session_filter_fixture(), DIST), SESSION_FILTER_CONTROLS + r"""
check(!document.querySelector('[aria-label="Sessions filters"]'), 'Sessions controls leaked into Overview');
window.location.hash = '#/sessions'; await pause();
check(count() === 13 && groups().length === 2 && groups().every(node => !node.open), 'Default file grouping changed');
const baseline = metrics(view().querySelector('.sessions-totals'));
check(group('2026-06-01').querySelector('.session-subtotal').textContent.includes('Cache read: 99 (99.00%)'), 'Collapsed date cache read missing');
const controls = view().querySelector('.session-controls');
check(parseFloat(getComputedStyle(controls).paddingLeft) === 14, 'Filter panel padding differs from Overview');
check(controls.getBoundingClientRect().width <= document.querySelector('main').getBoundingClientRect().width, 'Filter panel escapes main container');
for (const input of controls.querySelectorAll('input')) {
  const bounds = input.getBoundingClientRect();
  check(bounds.height === 44, 'Input height differs from Overview');
  if (innerWidth > 540) check(bounds.width <= 224 && bounds.width >= 160, 'Desktop input stretches beyond compact Overview width');
  else check(bounds.width <= controls.clientWidth / 2, 'Mobile inputs no longer use wrapped half-width layout');
}
for (const node of view().querySelectorAll('.session-controls input, .session-controls button')) {
  if (node.disabled) continue;
  node.focus(); check(document.activeElement === node && getComputedStyle(node).outlineStyle !== 'none', 'Control lacks visible keyboard focus');
  check(node.getBoundingClientRect().height >= 44, 'Control target under 44px');
  check(node.labels?.length === 1, 'Input lacks native label');
  check(node.getAttribute('aria-describedby').split(' ').every(id => document.getElementById(id)), 'Help relationship missing');
}
await threshold('95');
const clearButton = controls.querySelector('button');
check(clearButton.textContent === 'Clear' && clearButton.getAttribute('aria-label') === 'Clear name/share filters', 'Compact Clear action lacks accessible scope');
check(clearButton.getBoundingClientRect().width <= 120 && clearButton.getBoundingClientRect().height === 44, 'Clear button not compact like Overview Reset');
clearButton.focus(); check(document.activeElement === clearButton && getComputedStyle(clearButton).outlineStyle !== 'none', 'Clear button lacks visible keyboard focus');
check(document.documentElement.scrollWidth <= innerWidth, 'Filter controls overflow viewport');
check(count() === 7 && groups().length === 2, 'Strict 95 count includes equality or multiplies multiday file');
check(group('2026-06-02').querySelector('.session-group-count').textContent.startsWith('6 measured'), 'Day count uses lifetime threshold');
await openGroups();
check(owners('2026-06-01').join() === 'Alpha spanning', 'High earlier day lost');
check(owners('2026-06-02').join('|') === 'Alpha high|Alpha decimal|Above rounding|Perfect|ID unavailable|ID unavailable', 'Strict threshold rounded, included output, omitted cache-write or included N/A');
const cards = [...group('2026-06-02').querySelectorAll('.session-file')];
check(cards[2].querySelector('summary').textContent.includes('95.00%'), 'Exact above-95 fixture must display rounded equality');
check(cards[0].querySelector('summary').textContent.includes('Cache read: 96 (96.00%)') && cards[0].querySelector('summary').textContent.includes('10,100 tokens'), 'Card cache share includes output or loses amount');
await search(' aLpHa ');
check(count() === 3 && owners('2026-06-02').join('|') === 'Alpha high|Alpha decimal', 'Name/share intersection or substring case folding failed');
check(metrics(view().querySelector('.sessions-totals')) === '11,200|27|10,000|1,150 (95.83%)|23|$0.3750', 'Subset totals include rejected file/day contributions');
check(metrics(group('2026-06-02').querySelector('.session-group-totals')) === '11,100|26|10,000|1,051 (95.55%)|23|$0.2500', 'Day subset metrics disagree');
check(group('2026-06-02').querySelector('.session-subtotal').textContent.includes('Cache read: 1,051 (95.55%)'), 'Day header differs from subset metrics');
await threshold('');
check(count() === 3 && owners('2026-06-02').length === 3, 'Empty threshold failed to restore name-only spanning day');
await search('worker');
check(count() === 0 && !groups().length, 'Search matched child role rather than explicit name');
await search('synthetic');
check(count() === 0, 'Search matched provider rather than explicit name');
await clear();
check(count() === 13 && metrics(view().querySelector('.sessions-totals')) === baseline, 'Clear failed to restore exact default totals');
check(control('Session name').value === '' && control('Cache-read share above (%)').value === '', 'Clear retained inputs');
check(!view().textContent.includes('not verified human-authored') && !view().textContent.includes('PRIVATE_'), 'Verbose privacy copy or forbidden metadata rendered');
check(document.documentElement.scrollWidth <= innerWidth, 'Sessions controls or summaries overflow viewport');
window.location.hash = '#/overview'; await pause();
check(!document.querySelector('.session-controls'), 'Sessions controls leaked after navigation');
""", viewport=viewport)

    def test_file_sessions_threshold_decimals_edges_selected_day_and_reconciliation(self):
        self.browser(render_react_dashboard(session_filter_fixture(), DIST), SESSION_FILTER_CONTROLS + r"""
window.location.hash = '#/sessions/2026-06-02'; await pause();
const baseline = metrics(view().querySelector('.sessions-totals'));
const rowMetrics = metrics(view().querySelector('.session-reconciliation'));
check(baseline === rowMetrics && view().textContent.includes('Displayed contributions match'), 'Default reconciliation changed');
await threshold('95.5');
check(count() === 4 && owners('2026-06-02').join('|') === 'Alpha high|Perfect|ID unavailable|ID unavailable', 'Strict decimal threshold includes exact equality');
comparison();
check(metrics(view().querySelector('.session-reconciliation')) === rowMetrics, 'Comparison rows filtered by share');
await threshold('95.499999');
check(count() === 5 && owners('2026-06-02').includes('Alpha decimal'), 'Six-place decimal threshold not exact');
await threshold('0');
check(count() === 11 && !owners('2026-06-02').includes('Output only') && !owners('2026-06-02').includes('No cache'), 'Zero threshold includes N/A or equal zero');
await threshold('100');
check(count() === 0 && group('2026-06-02').querySelector('.empty').textContent.includes('match the name/share'), '100 threshold must exclude every contribution');
check(metrics(view().querySelector('.sessions-totals')) === '0|0|0|0 (N/A)|0|$0.0000', 'Empty subset fabricated totals');
comparison();
await threshold(''); await search('Output only');
check(count() === 1 && metrics(view().querySelector('.sessions-totals')) === '7|0|7|0 (N/A)|0|$0.1250', 'No-threshold output-only N/A lost');
comparison();
await clear();
check(count() === 13 && metrics(view().querySelector('.sessions-totals')) === baseline && view().textContent.includes('Displayed contributions match'), 'Clearing did not restore exact reconciliation');
check(!view().querySelector('[role="alert"]'), 'Clearing left false discrepancy');
window.location.hash = '#/sessions'; await pause();
check(count() === 13 && control('Cache-read share above (%)').value === '', 'Route remount retained stale filters');
await threshold('95.5');
check(count() === 5 && groups().length === 2, 'Decimal threshold used file lifetime instead of day scope');
""")

    def test_file_sessions_invalid_thresholds_fail_closed_in_both_views(self):
        for route in ("#/sessions", "#/sessions/2026-06-02"):
            with self.subTest(route=route):
                setup = "window.location.hash = " + json.dumps(route) + "; await pause();\n"
                self.browser(render_react_dashboard(session_filter_fixture(), DIST), SESSION_FILTER_CONTROLS + setup + r"""
await search('Alpha');
for (const value of ['-1', '100.000001', '101', 'junk', '95x', '95.', '.', '-', '+95', '1e2', 'NaN', 'Infinity', ' ', ' 95', '95 ', '095', '.5', '95.1234567']) {
  await threshold(value);
  const input = control('Cache-read share above (%)');
  check(input.value === value && input.getAttribute('aria-invalid') === 'true', 'Invalid edit sanitized into empty/all: ' + value);
  check(view().querySelector('#session-share-error[role="alert"]') && input.getAttribute('aria-describedby').includes('session-share-error'), 'Accessible error missing');
  check(view().querySelector('[role="status"]').textContent.includes('UNAVAILABLE'), 'Invalid threshold does not report unavailable');
  check(!view().querySelector('.sessions-totals, .sessions-count, .session-day-group, .session-file'), 'Invalid threshold shows stale or widened results');
  if (view().querySelector('.session-reconciliation')) comparison();
}
await threshold('95');
check(count() === (location.hash === '#/sessions' ? 3 : 2) && !view().querySelector('[role="alert"]'), 'Correcting invalid threshold lost intersection');
await clear();
check(count() === 13 && !view().querySelector('.sessions-unavailable'), 'Clearing invalid input did not restore normal results');
if (location.hash !== '#/sessions') check(view().textContent.includes('Displayed contributions match'), 'Reconciliation not restored');
check(document.documentElement.scrollWidth <= innerWidth, 'Invalid-input help overflows mobile');
""", viewport="500,900")

    def test_file_sessions_threshold_after_provider_model_matching_and_honest_rows(self):
        self.browser(render_react_dashboard(session_filter_fixture(), DIST), SESSION_FILTER_CONTROLS + r"""
window.location.hash = '#/sessions/2026-06-02'; await pause();
await search('Label scope'); await threshold('95');
check(count() === 0, 'Per-model share used before file/day aggregation');
window.location.hash = '#/sessions/2026-06-02?provider=' + encodeURIComponent('provider +β&%?#') + '&model=target'; await pause();
await search('Label scope'); await threshold('95');
check(count() === 1 && owners('2026-06-02').join() === 'Label scope', 'Threshold not applied after exact provider/model matching');
check(metrics(view().querySelector('.sessions-totals')) === '100|1|0|99 (99.00%)|0|$0.1250', 'Other labels leaked into scoped totals');
check(group('2026-06-02').querySelector('.session-subtotal').textContent.includes('Cache read: 99 (99.00%)'), 'Header scope differs from selected models');
comparison(); // Even coincidentally equal subset and rows must not claim reconciliation.
await clear();
check(location.hash.includes('?provider=') && view().textContent.includes('Displayed contributions match'), 'Clear removed label route or failed to reconcile');
window.location.hash = '#/sessions/2026-06-02?model=target'; await pause();
await search('Split aggregate'); await threshold('95');
check(count() === 1 && metrics(view().querySelector('.sessions-totals')) === '95|0|0|95 (100.00%)|0|$0.1250', 'Model-only threshold includes unmatched model');
comparison();
await clear();
check(view().textContent.includes('Displayed contributions match'), 'Model-only normal reconciliation lost');
window.location.hash = '#/sessions/2026-06-03'; await pause();
await threshold('95');
check(count() === 0 && view().textContent.includes('No recorded daily rows'), 'Missing-day warning hidden by active threshold');
comparison();
await clear();
check(view().textContent.includes('Displayed contributions match') && view().textContent.includes('No measured session files contributed'), 'Missing-day default state changed');
""", viewport="500,900")

    def test_file_sessions_explicit_names_as_text_and_anonymous_v3_fallback(self):
        data = session_fixture()
        name = '</script><img src=x onerror=alert(1)> Synthetic Pi name & β'
        data["sessions"][0]["name"] = name
        document = render_react_dashboard(data, DIST)
        self.assertEqual(payload(document)["sessions"][0]["name"], name)
        self.assertNotIn(name, document)
        for version in (5, 4, 3):
            with self.subTest(version=version):
                changed = document.replace('"version": 5', f'"version": {version}')
                setup = "const expectedName = " + json.dumps(name if version != 3 else "ID unavailable") + ";\n"
                self.browser(changed, setup + SESSION_FILTER_CONTROLS + r"""
window.location.hash = '#/sessions/2026-06-02'; await pause();
check(view().querySelector('.session-owner').textContent === expectedName, 'Explicit name or anonymous v3 fallback incorrect');
check(!view().textContent.includes('not verified human-authored') && !view().textContent.includes('sensitive text'), 'Selected-day name privacy paragraphs still mounted');
check(!document.querySelector('img') && !view().textContent.includes('PRIVATE_'), 'Name injection or private extra rendered');
check(view().querySelectorAll('.session-file')[1].querySelector('.session-name-source').textContent === 'Non-SDD role: worker', 'Unnamed child role lost');
check(!document.querySelector('[role="alert"]'), 'Named session changed usage reconciliation');
await search('synthetic pi NAME');
check(count() === (expectedName === 'ID unavailable' ? 0 : 1), 'Name search inferred anonymous v3 name or lost explicit v4 substring');
check(!document.querySelector('img'), 'Filtered explicit name became markup');
check(document.documentElement.scrollWidth <= innerWidth, 'Name overflows narrow viewport');
""", viewport="500,900")

    def test_file_sessions_header_ids_shared_snapshot_scope_and_long_token(self):
        data = session_filter_fixture()
        long_id = "A" + "z._-" * 31 + "end"
        data["sessions"][0]["sessionId"] = "synthetic-single-multiday"
        data["sessions"][1]["sessionId"] = "synthetic-cross-day"
        data["sessions"][1]["startDay"] = data["sessions"][1]["endDay"] = "2026-06-01"
        data["sessions"][1]["days"][0]["day"] = "2026-06-01"
        data["sessions"][2]["sessionId"] = "synthetic-cross-day"
        data["sessions"][3]["sessionId"] = "synthetic-cross-day"
        data["sessions"][9]["sessionId"] = long_id
        data["sessions"][10]["sessionId"] = "synthetic-child"
        data["rows"] = [{**model, "day": day["day"], "role": session["role"]}
                        for session in data["sessions"] for day in session["days"]
                        for model in day["models"]]
        document = render_react_dashboard(data, DIST)
        # A JSON Unicode escape must decode to the exact valid ASCII token.
        document = document.replace('"' + long_id + '"', '"\\u0041' + long_id[1:] + '"')
        for viewport in ("1280,900", "500,900"):
            with self.subTest(viewport=viewport):
                self.browser(document, SESSION_FILTER_CONTROLS + r"""
window.location.hash = '#/sessions'; await pause(); await openGroups();
check(count() === 13, 'Header IDs merged measured files');
const cards = () => [...view().querySelectorAll('.session-file')];
const named = title => cards().find(card => card.querySelector('.session-owner').textContent === title);
check(named('Alpha spanning') && !named('Alpha spanning').querySelector('.session-shared-id'), 'One multiday file mistaken for shared ID');
check(view().querySelectorAll('.session-shared-id').length === 3, 'Same-day and cross-day repeated files not marked separately');
const longTitle = 'Session A' + 'z._-'.repeat(31) + 'end';
check(named(longTitle)?.querySelector('.session-name-source').textContent === 'Parent', 'Long genuine parent ID truncated or escaped incorrectly');
check(named('Session synthetic-child')?.querySelector('.session-name-source').textContent === 'Non-SDD role: worker', 'Child ID missing primary title or role');
check(named('Output only')?.querySelector('.session-name-source').textContent === 'Explicit Pi name · Parent', 'Explicit name no longer primary');
check(document.documentElement.scrollWidth <= innerWidth, 'Long ID overflows viewport');
window.location.hash = '#/sessions/2026-06-02'; await pause();
check(count() === 12 && view().querySelectorAll('.session-shared-id').length === 2, 'Day filter lost snapshot-wide shared ID evidence');
await search('Alpha high'); await threshold('95');
check(count() === 1 && named('Alpha high')?.querySelector('.session-shared-id')?.textContent === 'Shared Pi ID across measured files', 'Name/share filter hid cross-day shared ID warning');
comparison();
await clear(); await search('synthetic-child');
check(count() === 0, 'Name search started searching IDs');
await clear();
check(!view().querySelector('[role="alert"]') && view().textContent.includes('Displayed contributions match'), 'IDs changed usage accounting');
check(!view().textContent.includes('PRIVATE_') && !document.querySelector('img'), 'Forbidden metadata or markup rendered');
check(document.documentElement.scrollWidth <= innerWidth, 'Selected-day ID overflows viewport');
""", viewport=viewport)

    def test_file_sessions_older_versions_never_render_injected_ids(self):
        data = session_fixture()
        for session in data["sessions"]:
            session["sessionId"] = "INJECTED_OLD_HEADER_ID"
        document = render_react_dashboard(data, DIST)
        for version in (3, 4):
            with self.subTest(version=version):
                self.browser(document.replace('"version": 5', f'"version": {version}'), r"""
window.location.hash = '#/sessions/2026-06-02'; await pause();
const view = document.querySelector('.sessions-view');
check([...view.querySelectorAll('.session-owner')].every(node => node.textContent === 'ID unavailable'), 'Older snapshot rendered injected ID');
check(!view.textContent.includes('INJECTED_OLD_HEADER_ID') && !view.querySelector('.session-shared-id'), 'Older snapshot ID affected display');
check(view.querySelectorAll('.session-name-source')[0].textContent === 'Parent' && view.querySelectorAll('.session-name-source')[1].textContent === 'Non-SDD role: worker', 'Unavailable ID lost role');
""")

    def test_file_sessions_day_page_links_separate_accessible_and_reset_filters(self):
        for viewport in ("1280,900", "500,900"):
            with self.subTest(viewport=viewport):
                self.browser(render_react_dashboard(session_filter_fixture(), DIST), SESSION_FILTER_CONTROLS + r"""
window.location.hash = '#/sessions'; await pause();
await search('Alpha'); await threshold('95');
check(count() === 3 && groups().every(node => !node.open), 'Filter setup expanded groups');
const links = [...view().querySelectorAll('.session-day-link')];
check(links.length === groups().length, 'Not every all-days heading has day navigation');
for (const link of links) {
  const entry = link.closest('.session-day-entry'), disclosure = entry.querySelector('details');
  const heading = disclosure.querySelector('summary'), date = heading.querySelector('time').dateTime;
  check(!link.closest('summary, details'), 'Day link nested in disclosure');
  check(link.textContent === 'Open day page' && link.getAttribute('href') === '#/sessions/' + date, 'Day link mislabeled or carries local filters');
  check(link.getAttribute('aria-label').includes(date) && link.getAttribute('aria-label').includes('All sessions'), 'Navigation scope inaccessible');
  link.focus(); check(document.activeElement === link && link.tabIndex === 0 && getComputedStyle(link).outlineStyle !== 'none', 'Day link not separately keyboard accessible');
  const bounds = link.getBoundingClientRect(), headingBounds = heading.getBoundingClientRect();
  check(bounds.height >= 44 && bounds.width >= 44, 'Day link touch target too small');
  check(bounds.left >= headingBounds.right && bounds.right <= innerWidth, 'Day link overlaps heading or escapes viewport');
  check(!disclosure.open && bounds.height > 0, 'Link requires expansion');
  heading.click(); await pause(); check(disclosure.open && location.hash === '#/sessions', 'Summary navigates instead of toggling');
  heading.click(); await pause(); check(!disclosure.open, 'Summary failed to collapse');
}
links[0].click(); await pause();
check(location.hash === '#/sessions/2026-06-02' && group('2026-06-02').open, 'Collapsed link failed to open selected-day route');
check(count() === 13 && owners('2026-06-02').length === 13, 'Day navigation retained hidden subset');
check(control('Session name').value === '' && control('Cache-read share above (%)').value === '', 'Day route retained local filters');
check(!view().querySelector('.session-day-link') && view().textContent.includes('Displayed contributions match'), 'Selected-day navigation/reconciliation incorrect');
check(document.documentElement.scrollWidth <= innerWidth, 'Day navigation overflows viewport');
""", viewport=viewport)

    def test_file_sessions_snapshot_relative_days_and_large_collapsed_group(self):
        data = session_fixture()
        data["stats"].update(asOf="2026-06-02T00:15:00+14:00", timezone="Pacific/Kiritimati")
        data["sessions"].extend(deepcopy(data["sessions"][1]) for _ in range(1000))
        data["rows"].extend(deepcopy(data["rows"][1]) for _ in range(1000))
        self.browser(render_react_dashboard(data, DIST), r"""
window.location.hash = '#/sessions'; await pause();
const groups = [...document.querySelectorAll('.session-day-group')];
check(groups[0].querySelector('h2').textContent === 'Tuesday · 2026-06-02 · Today', 'Today converted to UTC/browser date');
check(groups[1].querySelector('h2').textContent === 'Monday · 2026-06-01 · Yesterday', 'Yesterday not anchored to snapshot-local day');
check(document.querySelector('.sessions-count').textContent.startsWith('1002 measured'), 'Global count multiplied multiday files');
check(groups[0].querySelector('.session-group-count').textContent.startsWith('1002 measured'), 'Day file count wrong');
check(!document.querySelector('.session-file'), 'Large history mounted a thousand collapsed cards');
window.location.hash = '#/sessions/2026-06-02'; await pause();
check(document.querySelector('.session-day-group').open && document.querySelectorAll('.session-file').length === 1002, 'Selected day omitted contributing files');
check(!document.querySelector('[role="alert"]'), 'Large selected day did not reconcile');
check(document.querySelector('.sessions-view').textContent.includes('do not establish inactivity'), 'Zero/missing context absent');
""", browser_timezone="America/Los_Angeles")

    def test_file_sessions_day_all_files_actual_models_and_reconciliation(self):
        data = session_fixture()
        extra = {**data["rows"][1], "role": None, "provider": "other-provider",
                 "model": "day-two-model", "input": 5, "output": 2, "cacheRead": 1,
                 "cacheWrite": 3, "cost": 0.125}
        data["rows"].extend([extra, deepcopy(data["rows"][1])])
        data["sessions"][0]["days"][1]["models"].append(extra)
        # Identical anonymous summaries still represent separate measured files.
        data["sessions"].append(deepcopy(data["sessions"][1]))
        self.browser(render_react_dashboard(data, DIST), r"""
window.location.hash = '#/sessions/2026-06-02'; await pause();
const view = document.querySelector('.sessions-view');
check(document.querySelector('h1').textContent === 'Sessions · 2026-06-02', 'Day route missing');
check(document.querySelector('nav [aria-current="page"]').textContent === 'Sessions', 'Day nav inactive');
check(view.querySelector('.sessions-count').textContent === '3 measured session files with attributed usage', 'Contributing files merged');
const files = [...view.querySelectorAll('.session-file')];
check(files.length === 3 && view.querySelector('.session-day-group').open, 'Selected-day group or contributors not visible');
check(files.every(node => !node.querySelector('details').open), 'Selected-day cards should remain compact');
const metrics = node => [...node.querySelectorAll(':scope > .session-metrics dd')].map(item => item.textContent).join('|');
check(metrics(view.querySelector('.sessions-totals')) === '33|19|4|7 (24.14%)|3|$0.6250', 'Selected-day totals wrong');
check(metrics(view.querySelector('.session-reconciliation')) === '33|19|4|7 (24.14%)|3|$0.6250', 'Daily rows wrong');
check(view.textContent.includes('Displayed contributions match') && !view.querySelector('[role="alert"]'), 'Reconciliation failed');
check(files[0].querySelectorAll('.session-models > li').length === 2, 'Actual model contributions omitted');
check(files[0].textContent.includes('other-provider') && files[0].textContent.includes('day-two-model') && files[0].textContent.includes('zero-synthetic'), 'Selected-day models inferred');
check(files.every(node => node.querySelectorAll('.session-day').length === 1), 'Other days retained');
check(!view.textContent.includes('2026-06-01') && !view.textContent.includes('9,007,199') && !view.textContent.includes('</script>'), 'Multiday lifetime usage leaked into selected day');
check(!view.textContent.includes('PRIVATE_'), 'Private source fields rendered');
view.querySelector('.sessions-back').click(); await pause();
check(window.location.hash === '#/sessions' && document.querySelector('h1').textContent === 'Sessions', 'Clear filter failed');
check(document.querySelectorAll('.session-day-group').length === 2 && !document.querySelector('.session-file'), 'Index did not restore collapsed day groups');
window.location.hash = '#/overview'; await pause();
check(document.querySelectorAll('.trend a').length === 7, 'Daily links missing');
""")

    def test_file_daily_links_all_contributors_navigation_and_mobile(self):
        for viewport in ("1280,900", "500,900"):
            with self.subTest(viewport=viewport):
                self.browser(render_react_dashboard(drilldown_fixture(), DIST), OVERVIEW_CONTROLS + r"""
const links = [...document.querySelectorAll('.trend a')];
check(links.length === 7, 'Every daily row must link, including missing days');
for (const link of links) {
  check(link.getAttribute('href') === '#/sessions/' + link.querySelector('time').dateTime, 'Unfiltered link carries unexpected data');
  link.focus(); check(document.activeElement === link, 'Daily link not keyboard focusable');
  check(getComputedStyle(link).outlineStyle !== 'none', 'Daily link focus invisible');
  check(link.getBoundingClientRect().height >= 44, 'Daily link touch target too small');
  check(link.getAttribute('aria-label').includes('View sessions for'), 'Daily link purpose missing');
}
check(document.documentElement.scrollWidth <= window.innerWidth, 'Daily links overflow mobile');
check(links.at(-1).textContent.includes('9,007,199,254,741,060 tokens'), 'All-day amount incorrect');
links.at(-1).click(); await pause();
check(location.hash === '#/sessions/2026-06-02', 'Day click did not navigate');
check(document.querySelector('nav [aria-current="page"]').textContent === 'Sessions', 'Day navigation inactive');
check(document.querySelectorAll('.session-file').length === 3, 'Identical files merged or contributors omitted');
const amounts = selector => [...document.querySelectorAll(selector + ' .session-metrics dd')].map(node => node.textContent).join('|');
const expected = '9,007,199,254,741,060|9,007,199,254,741,043|5|10 (0.00%)|2|$1.8750';
check(amounts('.sessions-totals') === expected && amounts('.session-reconciliation') === expected, 'Day categories/cost not exact');
check(!document.querySelector('[role="alert"]'), 'Day failed reconciliation');
check(!document.querySelector('.sessions-view').textContent.includes('2026-06-01'), 'Other-day contribution leaked');
check(document.querySelectorAll('.session-models > li').length === 6, 'Actual day models omitted');
check(!document.querySelector('img, .sessions-view [data-session-id], .session-file [id]'), 'Markup or identity leaked');
check(!document.querySelector('.sessions-view').textContent.includes('PRIVATE_'), 'Private metadata leaked');
check(document.documentElement.scrollWidth <= window.innerWidth, 'Day view overflows mobile');
document.querySelector('.session-actions a[href="#/overview"]').click(); await pause();
check(document.querySelector('h1').textContent === 'Overview', 'Back to Overview failed');
// Controls are remounted on navigation; use the current Period field.
const period = document.querySelector('.overview-selects select');
period.value = 'all'; period.dispatchEvent(new Event('change', {bubbles: true})); await pause();
const allLinks = [...document.querySelectorAll('.trend a')];
check(allLinks.length === 2, 'All-time daily context not linked');
allLinks[0].click(); await pause();
check(location.hash === '#/sessions/2026-06-01' && document.querySelector('.sessions-totals dd').textContent === '9,007,199,254,741,002', 'All-time day link widened totals');
document.querySelector('.sessions-back').click(); await pause();
check(document.querySelector('h1').textContent === 'Sessions', 'All sessions action failed');
document.querySelector('nav a[href="#/stats"]').click(); await pause();
check(document.querySelector('.stats-view') && !document.querySelector('.placeholder'), 'Stats route missing');
""", viewport=viewport)

    def test_file_filtered_daily_links_exact_labels_files_and_totals(self):
        for provider, model, amount, files, cost in (
            ("synthetic", None, "22", 3, "$0.5000"),
            ("synthetic", "test-model", "22", 2, "$0.5000"),
            ("broker +β&%?#", None, "9,007,199,254,741,019", 1, "$0.6250"),
            (None, "</script><img src=x onerror=alert(1)>", "9,007,199,254,741,021", 1, "$0.8750"),
            ("broker +β&%?#", "</script><img src=x onerror=alert(1)>", "9,007,199,254,741,002", 1, "$0.1250"),
        ):
            with self.subTest(provider=provider, model=model):
                setup = "const selection = " + json.dumps(dict(provider=provider, model=model, amount=amount, files=files, cost=cost)) + ";\n"
                self.browser(render_react_dashboard(drilldown_fixture(), DIST), setup + OVERVIEW_CONTROLS + r"""
if (selection.provider !== null) await choose('Provider', JSON.stringify(selection.provider));
if (selection.model !== null) await choose('Model', JSON.stringify(selection.model));
const link = trend().at(-1).querySelector('a');
check(link.textContent.includes(selection.amount + ' tokens'), 'Filtered bar amount wrong');
check(document.querySelector('[aria-labelledby="trend-title"]').getAttribute('aria-description').includes('Filtered amounts are not all-day totals'), 'Accessible filtered bar scope misleading');
const params = new URLSearchParams();
for (const key of ['provider', 'model']) if (selection[key] !== null) params.set(key, selection[key]);
check(link.getAttribute('href') === '#/sessions/2026-06-02?' + params.toString(), 'Link contains non-selected labels or identity');
link.click(); await pause();
const view = document.querySelector('.sessions-view');
check(view.querySelectorAll('.session-file').length === selection.files, 'Filtered contributing files incorrect');
for (const selector of ['.sessions-totals', '.session-reconciliation']) {
  check(view.querySelector(selector + ' dd').textContent === selection.amount, 'Filtered total does not match clicked amount');
  check(view.querySelector(selector + ' dd:last-child') !== null, 'Metrics missing');
  check([...view.querySelectorAll(selector + ' dd')].at(-1).textContent === selection.cost, 'Filtered cost includes unrelated models/dates');
}
for (const key of ['provider', 'model']) if (selection[key] !== null) check(view.querySelector('.session-filter-scope').textContent.includes(selection[key]), 'Applied label not visible');
check(view.textContent.includes('Filtered day contributions only, not the all-day total'), 'Filtered Sessions scope misleading');
check(!view.querySelector('[role="alert"]') && view.textContent.includes('Displayed contributions match'), 'Filtered rows and files differ');
check(!view.textContent.includes('2026-06-01') && !document.querySelector('img'), 'Other day or executable label leaked');
check(document.documentElement.scrollWidth <= window.innerWidth, 'Filtered labels overflow mobile');
const clear = view.querySelector('a[href="#/sessions/2026-06-02"]');
clear.focus(); check(document.activeElement === clear && getComputedStyle(clear).outlineStyle !== 'none', 'Clear filter action lacks visible focus');
clear.click(); await pause();
check(document.querySelectorAll('.session-file').length === 3 && document.querySelector('.sessions-totals dd').textContent === '9,007,199,254,741,060', 'Clear filters did not keep day and restore all contributions');
check(!document.querySelector('.session-filter-scope').textContent.includes('Filtered day'), 'Clear retained filter labels');
""", viewport="500,900")

    def test_file_daily_click_empty_zero_and_unspecified_labels(self):
        data = session_fixture()
        data["rows"][2].update(provider="", model="")
        data["sessions"][0]["days"][1]["models"][0].update(provider="", model="")
        self.browser(render_react_dashboard(data, DIST), OVERVIEW_CONTROLS + r"""
await choose('Provider', '""'); await choose('Model', '""');
const zero = trend().at(-1).querySelector('a');
check(zero.getAttribute('href') === '#/sessions/2026-06-02?provider=&model=' && zero.textContent.includes('0 tokens'), 'Explicit empty labels lost');
zero.click(); await pause();
check(document.querySelectorAll('.session-file').length === 1 && document.querySelector('.sessions-totals dd').textContent === '0', 'Accepted zero file omitted');
check(document.querySelector('.session-filter-scope').textContent.includes('(unspecified provider)') && document.querySelector('.session-filter-scope').textContent.includes('(unspecified model)'), 'Empty labels confused with all');
check(!document.querySelector('[role="alert"]'), 'Zero filtered contributions mismatch');
window.location.hash = '#/overview'; await pause();
const missing = document.querySelector('.trend li[data-recorded="false"] a');
check(missing.textContent.includes('No recorded usage'), 'Gap not distinguished from zero');
missing.click(); await pause();
check(document.querySelector('.sessions-count').textContent.startsWith('0 measured') && document.querySelector('.sessions-totals dd').textContent === '0', 'Missing day invented files');
check(document.querySelector('.empty').textContent.includes('No measured session files contributed'), 'Missing day not explained');
window.location.hash = '#/sessions/2026-06-03?provider=&model='; await pause();
check(document.querySelector('h1').textContent === 'Sessions · 2026-06-03' && !document.querySelector('.session-file'), 'Known filter on empty day rejected');
check(!document.querySelector('[role="alert"]'), 'Missing-day zero reconciliation failed');
""")

    def test_file_sessions_reject_unknown_duplicate_and_malformed_queries(self):
        self.browser(render_react_dashboard(drilldown_fixture(), DIST), r"""
for (const query of ['?', '?provider', '?provider=missing', '?model=missing', '?unknown=synthetic',
  '?provider=synthetic&provider=synthetic', '?model=test-model&model=test-model',
  '?provider=synthetic&model=test-model&model=test-model', '?provider=synthetic&',
  '?&provider=synthetic', '?provider=synthetic&&model=test-model', '?provider=synthetic?model=test-model',
  '?provider=%', '?provider=%GG', '?model=%E0%A4%A', '?provider=synthetic%00',
  '?provider=synthetic&model=other', '?provider=', '?model=', '?Provider=synthetic',
  '?sessionPath=%2Fprivate', '?id=1', '?provider=synthetic=extra', '?provider=%2525']) {
  window.location.hash = '#/sessions/2026-06-02' + query; await pause();
  check(document.querySelector('h1').textContent === 'View not found', 'Malformed/unknown query accepted: ' + query);
  check(!document.querySelector('.sessions-view, nav [aria-current]'), 'Rejected query kept Sessions active');
}
window.location.hash = '#/sessions/2026-02-30?provider=synthetic'; await pause();
check(document.querySelector('h1').textContent === 'View not found', 'Query bypassed date validation');
""")

    def test_file_sessions_discrepancies_each_metric_and_missing_summaries(self):
        for field in ("input", "output", "cacheRead", "cacheWrite", "cost", "missing"):
            with self.subTest(field=field):
                data = session_fixture()
                if field == "missing":
                    data["sessions"] = []
                else:
                    data["rows"][1][field] += 1
                self.browser(render_react_dashboard(data, DIST), r"""
window.location.hash = '#/sessions/2026-06-02'; await pause();
const view = document.querySelector('.sessions-view');
check(view.querySelector('[role="alert"]')?.textContent.includes('Discrepancy'), 'Discrepancy hidden');
check(!view.textContent.includes('Displayed contributions match'), 'False reconciliation success');
check(view.querySelectorAll('.session-reconciliation .session-metrics dd').length === 6, 'Expected totals missing');
check(view.querySelectorAll('.sessions-totals .session-metrics dd').length === 6, 'Displayed totals missing');
""")

    def test_file_sessions_empty_dates_zero_files_and_calendar_routes(self):
        data = session_fixture()
        # June 2 now contains only the parent's accepted zero contribution.
        data["rows"] = [data["rows"][0], data["rows"][2]]
        data["sessions"] = data["sessions"][:1]
        self.browser(render_react_dashboard(data, DIST), r"""
const visit = async hash => { window.location.hash = hash; await pause(); };
await visit('#/sessions/2026-06-02');
check(document.querySelector('.sessions-count').textContent.startsWith('1 measured'), 'Zero-usage file omitted');
check(document.querySelector('.session-models').textContent.includes('zero-synthetic'), 'Zero model omitted');
check(document.querySelector('.sessions-totals dd').textContent === '0', 'Zero total incorrect');
check(!document.querySelector('[role="alert"]'), 'Zero contribution falsely mismatched');
for (const day of ['2026-06-03', '2024-02-29', '0001-01-01', '9999-12-31']) {
  await visit('#/sessions/' + day);
  check(document.querySelector('h1').textContent === 'Sessions · ' + day, 'Valid calendar route rejected: ' + day);
  check(document.querySelector('.sessions-count').textContent.startsWith('0 measured'), 'Missing date inferred files');
  check(document.querySelector('.empty').textContent.includes('No measured session files contributed'), 'Empty day dishonest');
  check(document.querySelector('.session-reconciliation').textContent.includes('No recorded daily rows'), 'Missing rows not distinguished');
  check(!document.querySelector('.session-file') && !document.querySelector('[role="alert"]'), 'Empty day invented contributions');
}
""")

    def test_file_sessions_reject_unsafe_and_noncalendar_routes(self):
        self.browser(render_react_dashboard(session_fixture(), DIST), r"""
for (const path of ['2026-02-30', '2025-02-29', '0000-01-01', '2026-13-01', '2026-06-00',
  '2026-6-02', '2026-06-02/extra', '2026-06-02T00:00:00Z', '2026-06-02?x=1',
  '%32%30%32%36-06-02', '../overview', '%2e%2e%2foverview', '%E0%A4%A', '', '2026-06-02%0A']) {
  window.location.hash = '#/sessions/' + path; await pause();
  check(document.querySelector('h1').textContent === 'View not found', 'Unsafe or invalid date accepted: ' + path);
  check(!document.querySelector('.sessions-view') && !document.querySelector('nav [aria-current]'), 'Invalid route retained Sessions');
}
""")

    def test_file_sessions_cost_rounding_tolerance_and_mobile_long_labels(self):
        data = session_fixture()
        data["rows"][1].update(cost=0.3, provider="provider-" + "x" * 200, model="model-" + "y" * 200)
        model = data["sessions"][1]["days"][0]["models"][0]
        model.update(data["rows"][1], cost=0.1 + 0.2)
        self.browser(render_react_dashboard(data, DIST), r"""
window.location.hash = '#/sessions/2026-06-02'; await pause();
check(!document.querySelector('[role="alert"]'), 'Floating-point noise reported as discrepancy');
check(document.querySelector('.session-reconciliation').textContent.includes('Token categories are compared exactly'), 'Reconciliation precision undisclosed');
check(document.documentElement.scrollWidth <= window.innerWidth, 'Long labels overflow mobile');
for (const node of document.querySelectorAll('.session-file summary, .sessions-back')) {
  node.focus(); check(document.activeElement === node, 'Sessions control not focusable');
  check(node.getBoundingClientRect().height >= 44, 'Sessions touch target too small');
}
""", viewport="500,900")

    def test_file_overview_custom_inclusive_five_ten_fifteen_days(self):
        for viewport, zone in (("1280,900", "UTC"), ("500,900", "America/Los_Angeles")):
            with self.subTest(viewport=viewport, zone=zone):
                self.browser(render_react_dashboard(custom_range_fixture(), DIST), CUSTOM_RANGE_CHECKS + r"""
await pickPeriod('Custom range');
for (const [start, end, count] of [
  ['2025-09-03', '2025-09-07', 5], ['2025-08-28', '2025-09-06', 10],
  ['2025-09-25', '2025-10-09', 15], ['2025-09-03', '2025-09-10', 8],
  ['2025-09-03', '2025-09-09', 7], ['2025-09-03', '2025-09-03', 1],
]) {
  await chooseRange(start, end); checkRange(start, end, count);
}
check(!select('Calendar span') && !dayInput(), 'Custom range exposed irrelevant controls');
for (const label of ['Start', 'End']) {
  const input = calendar(label);
  check(input.required && input.min === '0001-01-01' && input.max === '9999-12-31' && input.labels.length === 1, 'Native date boundary/label incorrect');
  input.focus(); check(document.activeElement === input && getComputedStyle(input).outlineStyle !== 'none', 'Date calendar lacks keyboard focus');
}
await reset();
check(select('Period').value === 'day' && resetButton.disabled && !calendar('Start'), 'Reset did not leave custom range');
""", viewport=viewport, browser_timezone=zone)

    def test_file_overview_custom_leap_year_dst_and_future_days(self):
        self.browser(render_react_dashboard(custom_range_fixture(), DIST), CUSTOM_RANGE_CHECKS + r"""
await pickPeriod('Custom range');
for (const [start, end, count] of [
  ['2024-02-27', '2024-03-02', 5], ['2025-12-28', '2026-01-06', 10],
  ['2026-01-01', '2026-01-15', 15], ['2024-03-08', '2024-03-12', 5],
  ['0001-01-01', '0001-01-05', 5], ['9999-12-27', '9999-12-31', 5],
]) {
  await chooseRange(start, end); checkRange(start, end, count);
}
""", viewport="500,900", browser_timezone="America/Los_Angeles")

    def test_file_overview_custom_bounded_trend_keeps_full_range_cards(self):
        self.browser(render_react_dashboard(custom_range_fixture(), DIST), CUSTOM_RANGE_CHECKS + r"""
await pickPeriod('Custom range');
for (const [start, end, count] of [
  ['2025-08-28', '2025-09-27', 31], ['2025-08-28', '2025-09-28', 32],
  ['2023-11-01', '2026-01-08', 800], ['0001-01-01', '9999-12-31', 3652059],
]) {
  await chooseRange(start, end); checkRange(start, end, count);
}
check(total() !== '0' && trend().every(node => node.dataset.state === 'future'), 'Bounded empty future window replaced full-range cards');
await choose('Provider', JSON.stringify('alpha')); await choose('Model', JSON.stringify('shared'));
checkRange('0001-01-01', '9999-12-31', 3652059);
""", viewport="500,900")

    def test_file_overview_custom_invalid_empty_and_recovery(self):
        self.browser(render_react_dashboard(custom_range_fixture(), DIST), CUSTOM_RANGE_CHECKS + r"""
await pickPeriod('Custom range');
await chooseRange('2025-09-03', '2025-09-10'); checkRange('2025-09-03', '2025-09-10', 8);
for (const [start, end, message] of [
  ['', '2025-09-10', 'Choose valid Start and End'], ['2025-09-03', '', 'Choose valid Start and End'],
  ['', '', 'Choose valid Start and End'], ['2025-09-11', '2025-09-10', 'Start must be on or before End'],
  ['2025-02-29', '2025-09-10', 'Choose valid Start and End'],
  ['0000-01-01', '2025-09-10', 'Choose valid Start and End'],
  ['2025-09-03', '10000-01-01', 'Choose valid Start and End'],
]) {
  await chooseRange(start, end);
  check(scope().includes('Invalid range') && document.querySelector('[role="alert"]').textContent.includes(message), 'Invalid range reason missing');
  check(metrics().every(value => value === 'Unavailable') && !trend().length && !modelNames().length, 'Invalid range displayed zero, all time or stale data');
  check(!scope().includes('All recorded dates') && !scope().includes('No recorded usage'), 'Invalid range presented as coverage');
  check(calendar('Start').getAttribute('aria-invalid') === 'true' && calendar('End').getAttribute('aria-describedby') === 'range-error', 'Invalid fields lack accessible error');
}
await choose('Provider', JSON.stringify('alpha')); await choose('Model', JSON.stringify('shared'));
check(metrics().every(value => value === 'Unavailable'), 'Filter restored totals for invalid range');
await chooseRange('2025-09-03', '2025-09-10'); checkRange('2025-09-03', '2025-09-10', 8);
check(calendar('Start').getAttribute('aria-invalid') === 'false', 'Corrected range retained error');
""")

    def test_file_overview_custom_filtered_sessions_reconciliation(self):
        for provider, model in ((None, None), ("alpha", None), (None, "shared"),
                                ("alpha", "shared"), ("", ""),
                                ("broker +β&%?#", "</script><img src=x onerror=alert(1)>")):
            with self.subTest(provider=provider, model=model):
                setup = "const selection = " + json.dumps(dict(provider=provider, model=model)) + ";\n"
                self.browser(render_react_dashboard(custom_range_fixture(), DIST), setup + CUSTOM_RANGE_CHECKS + r"""
await pickPeriod('Custom range'); await chooseRange('2025-12-28', '2026-01-11');
if (selection.provider !== null) await choose('Provider', JSON.stringify(selection.provider));
if (selection.model !== null) await choose('Model', JSON.stringify(selection.model));
checkRange('2025-12-28', '2026-01-11', 15);
const link = document.querySelector('.trend [data-recorded="true"] a');
const day = link.querySelector('time').dateTime, target = link.getAttribute('href');
const rows = filteredRows().filter(row => row.day === day);
const amount = tokenTotal(rows).toLocaleString('en-US'), cost = '$' + costTotal(rows).toFixed(4);
link.click(); await pause();
check(location.hash === target && document.querySelectorAll('.session-file').length === rows.length, 'Custom day link lost matching measured files');
for (const selector of ['.sessions-totals', '.session-reconciliation']) {
  const values = [...document.querySelectorAll(selector + ' dd')].map(node => node.textContent);
  check(values[0] === amount && values.at(-1) === cost, 'Custom filtered daily link does not reconcile');
}
check(!document.querySelector('[role="alert"]') && document.querySelector('.sessions-view').textContent.includes('Displayed contributions match'), 'Custom link reconciliation warning');
check(!document.querySelector('img') && !document.querySelector('.sessions-view').textContent.includes('PRIVATE_'), 'Custom range leaked private or executable labels');
""", viewport="500,900")

    def test_file_overview_native_week_anchor_accepts_unrecorded_days(self):
        self.browser(render_react_dashboard(overview_fixture(), DIST), OVERVIEW_CONTROLS + r"""
await pickPeriod('Weekly');
check(!select('Ending week') && calendar('Week containing').value === '2026-01-05', 'Recorded-only weekly selector remains');
for (const [anchor, start, end, amount] of [
  ['2026-01-03', '2025-12-29', '2026-01-04', '108'],
  ['2024-02-29', '2024-02-26', '2024-03-03', '0'],
  ['2027-09-10', '2027-09-06', '2027-09-12', '0'],
]) {
  await chooseDate('Week containing', anchor);
  check(calendar('Week containing').value === anchor && scope().includes(start + ' to ' + end) && total() === amount, 'Arbitrary anchor did not select Monday–Sunday');
  check(dates().length === 5 && dates().at(-1) === start, 'Native anchor lost five-week trend');
}
await chooseDate('Week containing', '2026-01-03'); await choose('Calendar span', '4');
check(scope().includes('2025-12-08 to 2026-01-04') && total() === '144', 'Native week anchor lost backward span');
await chooseDate('Week containing', '');
check(scope().includes('Invalid range') && metrics().every(value => value === 'Unavailable') && !trend().length, 'Empty week anchor retained stale data');
await chooseDate('Week containing', '9999-12-31');
check(scope().includes('9999-12-31') && dates().length === 5 && !document.querySelector('[role="alert"]'), 'Upper week boundary failed');
await chooseDate('Week containing', '0001-01-01');
check(scope().includes('0001-01-01 to 0001-01-07') && dates().join() === '0001-01-01', 'Lower week boundary generated invalid dates');
await pickPeriod('Last Week');
check(!calendar('Week containing') && total() === '108', 'Quick week range changed');
""", viewport="500,900", browser_timezone="America/Los_Angeles")

    def test_file_overview_visible_snapshot_as_of_survives_filters(self):
        for viewport in ("1280,900", "500,900"):
            with self.subTest(viewport=viewport):
                self.browser(render_react_dashboard(overview_fixture(), DIST), OVERVIEW_CONTROLS + r"""
const checkSnapshot = () => {
  const badge = document.querySelector('.overview-view .page-heading .badge');
  const time = badge?.querySelector('time');
  check(!document.querySelector('.quality, footer'), 'Global footer or quality unexpectedly present');
  check(badge?.checkVisibility({visibilityProperty: true, opacityProperty: true}) && time?.checkVisibility({visibilityProperty: true, opacityProperty: true}), 'Snapshot badge or timestamp not visible');
  check(badge.innerText.includes('Snapshot as of') && badge.innerText.includes('2026-01-06 00:30') && badge.innerText.includes('Pacific/Kiritimati'), 'Visible snapshot context missing or converted to browser timezone');
  check(time.getAttribute('datetime') === '2026-01-06T00:30:00+14:00', 'Machine-readable snapshot instant lost');
  check(time.innerText === '2026-01-06 00:30', 'Timestamp label must retain snapshot-local minutes');
  const bounds = badge.getBoundingClientRect();
  check(bounds.left >= 0 && bounds.right <= innerWidth && document.documentElement.scrollWidth <= innerWidth, 'Snapshot badge overflows viewport');
};
checkSnapshot();
await choose('Provider', JSON.stringify('alpha'));
await choose('Model', JSON.stringify('shared'));
checkSnapshot();
await pickPeriod('Weekly');
checkSnapshot();
await reset();
checkSnapshot();
""", viewport=viewport, browser_timezone="America/Los_Angeles")

    def test_file_overview_bucket_cache_share_type_states_and_filter_scope(self):
        for viewport in ("1280,900", "390,844", "320,844"):
            with self.subTest(viewport=viewport):
                self.browser(render_react_dashboard(expansion_fixture(), DIST), r"""
document.querySelector('nav a[href="#/agents"]').click(); await pause();
document.querySelector('a[href="#/agents/worker"]').click(); await pause();
const agentShare = document.querySelector('.agent-trend-cache-share');
const mutedColor = getComputedStyle(agentShare).color;
const agentModelShare = document.querySelector('.agent-panels .model-cache-share');
const modelShareColor = getComputedStyle(agentModelShare).color, modelShareSize = getComputedStyle(agentModelShare).fontSize;
check(mutedColor === 'rgb(164, 173, 191)' && getComputedStyle(agentShare).opacity === '1', 'Agent reference is not legible muted text');
document.querySelector('nav a[href="#/overview"]').click(); await pause();
""" + OVERVIEW_CONTROLS + EXPANSION_CHECKS + r"""
const shareOf = rows => {
  const read = rows.reduce((sum, row) => sum + BigInt(row.cacheRead), 0n);
  const denominator = rows.reduce((sum, row) => sum + BigInt(row.input) + BigInt(row.cacheRead) + BigInt(row.cacheWrite), 0n);
  return denominator ? (Number((read * 10000n + denominator / 2n) / denominator) / 100).toFixed(2) + '%' : 'N/A';
};
const rem = parseFloat(getComputedStyle(document.documentElement).fontSize);
const visibleText = node => {
  const copy = node.cloneNode(true);
  for (const hidden of copy.querySelectorAll('.visually-hidden')) hidden.remove();
  for (const hidden of node.querySelectorAll('.visually-hidden')) {
    check(getComputedStyle(hidden).clipPath === 'inset(50%)' && hidden.getBoundingClientRect().width <= 1, 'Unit helper is not visually clipped');
  }
  return copy.textContent;
};
let sawDifferentShare = false, sawZero = false, sawMissing = false, sawFuture = false, sawHuge = false;
let sawPairDifference = false, sawPairNA = false;
const modes = [
  ['Daily', '2026-01-08', '2026-01-08'], ['Weekly', '2026-01-05', '2026-01-11'],
  ['Monthly', '2026-01-01', '2026-01-31'], ['Custom range', '2025-12-29', '2026-01-08'],
  ['All time', null, null], ['Today', '2026-01-06', '2026-01-06'],
  ['Yesterday', '2026-01-05', '2026-01-05'], ['Last 7 Days', '2025-12-31', '2026-01-06'],
  ['Last Week', '2025-12-29', '2026-01-04'], ['This Month', '2026-01-01', '2026-01-06'],
  ['Last Month', '2025-12-01', '2025-12-31'],
];
for (const filters of [[], ['alpha'], ['alpha', 'shared'], ['broker +β&%?#', '</script><img src=x onerror=alert(1)>']]) {
  await reset();
  if (filters.length) await choose('Provider', JSON.stringify(filters[0]));
  if (filters.length > 1) await choose('Model', JSON.stringify(filters[1]));
  for (const [mode, start, end] of modes) {
    await pickPeriod(mode);
    if (mode === 'Custom range') await chooseDate('Start', start);
    const selectedRows = filteredRows().filter(row => !start || row.day >= start && row.day <= end);
    check(total() === tokenTotal(selectedRows).toLocaleString('en-US') && metrics()[2] === shareOf(selectedRows) &&
      metrics()[3] === '$' + costTotal(selectedRows).toFixed(4), 'Trend changed selected-row cards: ' + mode);
    for (const bucket of trend()) {
      const target = bucket.querySelector(':scope > .trend-day');
      const day = target.querySelector('time').dateTime;
      const weekly = mode === 'Weekly' || mode === 'Last Week';
      const monthly = ['Monthly', 'This Month', 'Last Month'].includes(mode);
      const bucketEnd = weekly ? new Date(Date.parse(day) + 6 * 86400000).toISOString().slice(0, 10) :
        monthly ? new Date(Date.UTC(Number(day.slice(0, 4)), Number(day.slice(5, 7)), 0)).toISOString().slice(0, 10) : day;
      const rows = filteredRows().filter(row => row.day >= day && row.day <= bucketEnd);
      const amount = target.querySelector('.trend-amount'), share = target.querySelector('.trend-cache-share');
      const label = target.getAttribute('aria-label');
      check(!/\btokens\b/i.test(visibleText(target)) && !visibleText(target).includes('Cache-read share:'), 'Top-level units/prefix still visible');
      check(bucket.dataset.recorded === String(!!rows.length), 'Bucket existence ignores filters');
      if (rows.length) {
        const tokens = tokenTotal(rows), expectedShare = shareOf(rows);
        check(amount && amount.textContent === tokens.toLocaleString('en-US') + ' tokens', 'Bucket amount lost BigInt precision or unit');
        check(label.includes(tokens.toLocaleString('en-US') + ' tokens') && label.includes('Cache-read share: ' + expectedShare), 'Navigation AX name lost units/share');
        check(share?.innerText === expectedShare && share.getAttribute('aria-label') === 'Cache-read share: ' + expectedShare, 'Share is not bucket-local');
        check(getComputedStyle(share).color === mutedColor && getComputedStyle(share).opacity === '1', 'Share lost muted Agent color');
        check(share.getBoundingClientRect().top >= amount.getBoundingClientRect().bottom && getComputedStyle(share).textAlign === 'right', 'Share not below amount');
        sawDifferentShare ||= expectedShare !== metrics()[2];
        sawHuge ||= tokens > 9007199254740991n;
        if (tokens === 0n) {
          sawZero = true;
          check(expectedShare === 'N/A' && target.innerText.includes('Recorded zero'), 'Recorded zero is dishonest');
        }
      } else {
        sawMissing = true;
        check(!amount && !share && target.querySelector('.trend-missing')?.innerText === 'No recorded usage', 'Missing bucket has false amount/share');
      }
      check(label.includes('future dates after snapshot') === (day <= '2026-01-06' && bucketEnd > '2026-01-06'), 'Partial future period status lost');
      if (day > '2026-01-06') {
        sawFuture = true;
        check(bucket.dataset.state === 'future' && target.innerText.includes('Future date after snapshot'), 'Future bucket status hidden');
      }
      for (const node of target.querySelectorAll('time, .trend-amount, .trend-cache-share, .trend-missing')) {
        check(parseFloat(getComputedStyle(node).fontSize) === rem, 'Bucket type not exactly 1rem');
      }
      if (weekly || monthly) {
        check(target.tagName === 'BUTTON' && target.getAttribute('aria-expanded') === 'false', 'Period disclosure broken');
        check(label.includes('$' + costTotal(rows).toFixed(4) + ' recorded cost estimate'), 'Bucket cost silently lost');
      } else check(target.tagName === 'A' && target.getAttribute('href') === '#/sessions/' + day + suffix(), 'Date/amount Sessions href changed');
    }
    for (const button of document.querySelectorAll('.models .model-toggle')) {
      const model = button.querySelector('.model-identity strong').textContent;
      const provider = button.querySelector('.model-identity > span').textContent;
      const rows = selectedRows.filter(row => (row.model || '(unspecified model)') === model && (row.provider || '(unspecified provider)') === provider);
      const amount = tokenTotal(rows), expected = shareOf(rows), share = button.querySelector('.model-cache-share');
      check(rows.length && share?.textContent === expected && share.getAttribute('aria-label') === 'Cache-read share: ' + expected, 'Model percentage is not selected provider/model pair');
      check(button.getAttribute('aria-label').includes(amount.toLocaleString('en-US') + ' tokens · Cache-read share: ' + expected), 'Model accessible units/share lost');
      check(!/\btokens\b/i.test(visibleText(button.querySelector('.model-total'))) && button.querySelector('.model-token-amount').textContent === amount.toLocaleString('en-US'), 'Model number/unit visibility wrong');
      check(getComputedStyle(share).color === modelShareColor && getComputedStyle(share).fontSize === modelShareSize, 'Overview model share does not match Agent styling');
      check(share.getBoundingClientRect().top >= button.querySelector('.model-token-amount').getBoundingClientRect().bottom, 'Model share not below number');
      sawPairDifference ||= expected !== metrics()[2]; sawPairNA ||= expected === 'N/A';
    }
    check(!document.querySelector('.model-empty-slot .model-cache-share, .ranked-providers .model-cache-share'), 'Empty note/provider gained cache percentage');
    const left = document.querySelector('.trend-panel'), right = document.querySelector('.models-panel');
    check(document.documentElement.scrollWidth <= innerWidth && left.scrollWidth <= left.clientWidth + 1, 'Overview overflow');
    if (innerWidth > 1050) check(Math.abs(left.getBoundingClientRect().height - right.getBoundingClientRect().height) <= 1, 'Desktop panel heights differ');
  }
}
check(sawDifferentShare && sawZero && sawMissing && sawFuture && sawHuge && sawPairDifference && sawPairNA, 'Synthetic fixture did not exercise every contract');
check(!document.querySelector('img') && !document.querySelector('#root').textContent.includes('PRIVATE_'), 'Privacy/escaping regression');
""", viewport=viewport, virtual_time=30000)

    def test_file_overview_trend_compact_visible_copy_and_accessible_context(self):
        for viewport in ("1280,900", "500,900"):
            with self.subTest(viewport=viewport):
                self.browser(render_react_dashboard(expansion_fixture(), DIST),
                             OVERVIEW_CONTROLS + EXPANSION_CHECKS + r"""
const panel = () => document.querySelector('[aria-labelledby="trend-title"]');
const compact = context => {
  check(panel().querySelector('h2').textContent === 'Usage trend', 'Trend heading removed');
  check(!panel().querySelector(':scope > p:not(.empty)'), 'Explanatory paragraphs still mounted');
  check(panel().querySelector('.panel-heading').nextElementSibling?.matches('ol.trend, p.empty'), 'Copy inserted between heading and chart');
  const description = panel().getAttribute('aria-description');
  for (const text of [context, 'Same provider/model filters', 'cards and top models keep their selected range',
    'Missing records are gaps', 'recorded zero (0 tokens)', 'snapshot date 2026-01-06, not inactivity',
    'measured session files', 'Filtered amounts are not all-day totals']) {
    check(description?.includes(text), 'Accessible context missing: ' + text);
  }
  for (const text of ['7 consecutive calendar days', 'Missing records are shown as gaps',
    'to see every contributing measured session file', description]) {
    check(!panel().innerText.includes(text), 'Explanatory wall still visibly rendered');
  }
  check(document.querySelectorAll('.metric').length === 4 && modelNames().join(',') === 'shared', 'Cards or top models removed');
  check(select('Provider').value === JSON.stringify('alpha') && select('Model').value === JSON.stringify('shared'), 'Filters lost');
  check(!document.querySelector('img') && !document.querySelector('#root').textContent.includes('PRIVATE_'), 'Private/hostile data rendered');
};
await choose('Provider', JSON.stringify('alpha'));
await choose('Model', JSON.stringify('shared'));
for (const [mode, context] of [['Daily', '7 consecutive calendar days'], ['Weekly', '5 Monday-start calendar weeks'],
  ['Monthly', 'Up to 12 calendar months'], ['Custom range', 'All 7 selected calendar days'], ['All time', 'Latest 14 recorded days']]) {
  await pickPeriod(mode);
  if (mode === 'Custom range') await chooseDate('Start', '2026-01-02');
  compact(context);
  if (mode === 'Weekly' || mode === 'Monthly') {
    const before = metrics().join('|');
    await expand(mode === 'Weekly' ? '2025-12-29' : '2026-01-01', mode === 'Weekly' ? '2026-01-04' : '2026-01-31');
    check(openPanel().querySelector('[data-state="zero"]').innerText.includes('Recorded zero'), 'Expanded zero label hidden');
    check(openPanel().querySelector('[data-state="missing"]').innerText.includes('No recorded usage'), 'Expanded missing label hidden');
    if (mode === 'Weekly') await expand('2026-01-05', '2026-01-11');
    check(openPanel().querySelector('[data-state="future"]').innerText.includes('Future date after snapshot'), 'Expanded future label hidden');
    await closeExpansion();
    check(metrics().join('|') === before, 'Expansion changed cards');
    compact(context);
  } else {
    for (const [state, label] of [['zero', 'Recorded zero'], ['future', 'Future date after snapshot']]) {
      check(trend().some(node => node.dataset.state === state && node.innerText.includes(label)), 'Daily state label hidden: ' + state);
    }
    if (mode !== 'All time') check(trend().some(node => node.dataset.state === 'missing' && node.innerText.includes('No recorded usage')), 'Missing day label hidden');
    check(trend().every(node => node.querySelector('a').getAttribute('href').endsWith(suffix())), 'Date links lost filter scope');
  }
}
await pickPeriod('Daily');
check(total() === '86' && dates().length === 7, 'Selected-day cards or context changed');
trend().at(-1).querySelector('a').click(); await pause();
check(location.hash === '#/sessions/2026-01-08' + suffix(), 'Filtered date link navigation failed');
check(document.querySelector('.sessions-view')?.textContent.includes('86'), 'Measured sessions missing');
""", viewport=viewport)

    def test_file_overview_daily_selection_gaps_and_bigint(self):
        self.browser(render_react_dashboard(overview_fixture(), DIST), OVERVIEW_CONTROLS + r"""
check(select('Period').value === 'day' && resetButton.disabled, 'Daily default controls unclear');
check(total() === '31' && dates().length === 7, 'Default latest-day cards/context incorrect');
check(select('Period').labels[0].firstChild.textContent === 'Period', 'Period needs an accessible label');
await pickPeriod('All time');
check(total() === '9,007,199,254,741,192', 'All-time BigInt total incorrect');
check(!resetButton.disabled, 'All time must allow reset to Daily');
await pickPeriod('Daily');
check(total() === '31' && dayInput().value === '2026-01-05', 'Daily cards must use one latest recorded day');
check(dates().join(',') === '2025-12-30,2025-12-31,2026-01-01,2026-01-02,2026-01-03,2026-01-04,2026-01-05', 'Daily context must include seven consecutive days');
check(trend().filter(node => node.dataset.recorded === 'false').length === 2, 'Missing days were hidden or invented');
check(trend()[3].textContent.includes('0 tokens') && trend()[3].dataset.recorded === 'true', 'Recorded zero confused with absent data');
check(trend()[4].textContent.includes('No recorded usage'), 'Gap not explained');
check(scope().includes('Selected day · 2026-01-05'), 'Card scope missing');
check(modelNames().join(',') === 'shared', 'Top models include other dates');
await chooseDay('2025-12-29');
check(metrics().join('|') === '55|5|60.00%|$0.1250', 'Older day metrics incorrect');
check(dates()[0] === '2025-12-23' && dates()[6] === '2025-12-29', 'Older day trend not anchored');
await chooseDay('2026-01-02');
check(total() === '0' && metrics()[2] === 'N/A' && scope().includes('Recorded usage is present'), 'Zero event not distinguished');
check(dayInput().value === '2026-01-02', 'Calendar did not retain recorded zero day');
await chooseDay('2023-11-01');
check(total() === '9,007,199,254,740,993', 'Selected-day BigInt lost precision');
check(modelNames().join(',') === 'ancient', 'Older model selection incorrect');
check(!resetButton.disabled, 'Older manual day must allow reset');
await reset();
check(dayInput().value === '2026-01-05' && resetButton.disabled, 'Reset did not restore latest recorded day');
await pickPeriod('Today');
check(total() === '0' && scope().includes('2026-01-06') && scope().includes('No recorded usage'), 'Snapshot-local empty today incorrect');
check(dayInput().value === '2026-01-06', 'Today calendar must show the snapshot-local day');
check(trend().at(-1).dataset.recorded === 'false' && !modelNames().length, 'Empty day fabricated activity');
check(trend().at(-1).querySelector('a').getAttribute('href') === '#/sessions/2026-01-06', 'Missing-day link absent');
await reset();
check(total() === '31' && select('Period').value === 'day' && resetButton.disabled, 'Reset did not restore Daily');
""")

    def test_file_overview_calendar_input_gaps_zero_and_quick_ranges(self):
        for viewport in ("1280,900", "500,900"):
            with self.subTest(viewport=viewport):
                self.browser(render_react_dashboard(overview_fixture(), DIST), OVERVIEW_CONTROLS + r"""
check(dayInput().labels[0].firstChild.textContent === 'Day', 'Calendar needs the Day accessible label');
check(dayInput().value === '2026-01-05' && dayInput().getBoundingClientRect().height >= 44, 'Calendar default/touch target incorrect');
dayInput().focus(); check(document.activeElement === dayInput(), 'Calendar is not keyboard focusable');
for (const [day, amount, start, recorded] of [
  ['2026-01-03', '0', '2025-12-28', false],
  ['2026-01-02', '0', '2025-12-27', true],
  ['2025-12-31', '27', '2025-12-25', true],
  ['2023-10-31', '0', '2023-10-25', false],
  ['2027-01-01', '0', '2026-12-26', false],
]) {
  await chooseDay(day);
  check(dayInput().value === day && dayInput().validity.valid, 'Valid unrecorded day was restricted: ' + day);
  check(total() === amount && scope().includes('Selected day · ' + day), 'Calendar cards/scope incorrect: ' + day);
  check(dates().length === 7 && dates()[0] === start && dates().at(-1) === day, 'Calendar context crossed timezone or year incorrectly');
  check(trend().at(-1).dataset.recorded === String(recorded), 'Recorded zero confused with a gap');
  check(scope().includes(recorded ? 'Recorded usage is present' : 'No recorded usage'), 'Calendar coverage not explicit');
}
for (const name of ['Today', 'Yesterday', 'Last 7 Days']) {
  await pickPeriod(name);
  check(dayInput().value === (name === 'Yesterday' ? '2026-01-05' : '2026-01-06'), 'Quick range calendar ignored snapshot-local date');
  await chooseDay('2026-01-01');
  check(select('Period').value === 'day' && total() === '3' && scope().includes('Selected day · 2026-01-01'), 'Calendar retained quick preset');
  check(dates().length === 7 && dates()[0] === '2025-12-26' && dates().at(-1) === '2026-01-01', 'Calendar did not restore daily trend');
}
await chooseDay('');
check(scope().includes('Invalid range') && metrics().every(value => value === 'Unavailable') && !trend().length, 'Incomplete calendar edit retained stale usage');
await choose('Provider', JSON.stringify('alpha'));
await choose('Model', JSON.stringify('shared'));
await pickPeriod('Last 7 Days');
await chooseDay('2025-12-29');
check(metrics().join('|') === '55|5|60.00%|$0.1250' && modelNames().join(',') === 'shared', 'Calendar change cleared or ignored provider/model filters');
await pickPeriod('Last Month');
check(!dayInput() && !select('Ending month'), 'Quick monthly range exposed manual controls');
await pickPeriod('Monthly');
check(!dayInput() && select('Ending month') && select('Calendar span'), 'Manual monthly controls missing');
await reset();
check(dayInput().value === '2026-01-05' && total() === '31' && resetButton.disabled && !select('Provider').value && !select('Model').value, 'Reset did not restore the default calendar and filters');
check(document.documentElement.scrollWidth <= window.innerWidth, 'Calendar overflows viewport');
""", viewport=viewport)

    def test_file_overview_calendar_weeks_months_and_spans(self):
        self.browser(render_react_dashboard(overview_fixture(), DIST), OVERVIEW_CONTROLS + r"""
await pickPeriod('Weekly');
check(total() === '31' && select('Calendar span').value === '1', 'Weekly default must be one calendar week');
check(document.querySelectorAll('.trend-period').length === 5 && !document.querySelector('.trend a'), 'Weekly bars must expand before linking days');
check(dates().join(',') === '2025-12-08,2025-12-15,2025-12-22,2025-12-29,2026-01-05', 'Five Monday weeks or year boundary incorrect');
await chooseDate('Week containing', '2025-12-31');
check(total() === '108' && scope().includes('2025-12-29 to 2026-01-04'), 'Week crossing year incorrect');
// Fixture weeks: Dec 29 = 55 + 27 + 3 + 0 + 23; Dec 22 = 17 + 19.
for (const [span, expected, start] of [
  ['1', '108', '2025-12-29'], ['2', '144', '2025-12-22'],
  ['3', '144', '2025-12-15'], ['4', '144', '2025-12-08'],
]) {
  await choose('Calendar span', span);
  check(total() === expected && scope().includes(start + ' to 2026-01-04'), 'Weekly backward span incorrect: ' + span);
  check(dates().join(',') === '2025-12-01,2025-12-08,2025-12-15,2025-12-22,2025-12-29', 'Weekly context moved beyond selected ending week');
}
check(scope().includes('4 calendar weeks · 2025-12-08 to 2026-01-04'), 'Weekly backward span not explicit');
check(controls.textContent.includes('run backward') && !controls.textContent.includes('run forward'), 'Range direction instructions incorrect');
await chooseDate('Week containing', '2025-12-22');
check(total() === '49' && scope().includes('2025-12-01 to 2025-12-28'), 'Older ending week span incorrect');
check(dates().at(-1) === '2025-12-22', 'Older selection includes subsequent week');
await pickPeriod('Monthly');
check(total() === '57' && select('Calendar span').value === '1', 'Monthly default must be one calendar month');
check(document.querySelectorAll('.trend-period').length === 12 && !document.querySelector('.trend a'), 'Monthly bars must expand before linking days');
check(trend().length === 12 && dates()[0] === '2025-02-01' && dates()[11] === '2026-01-01', 'Monthly context must cap at twelve calendar months');
check(trend()[0].dataset.recorded === 'false', 'Missing calendar month fabricated activity');
// Fixture months: January = 3 + 0 + 23 + 31; December = 13 + 17 + 19 + 55 + 27.
for (const [span, expected, start] of [
  ['1', '57', '2026-01-01'], ['2', '188', '2025-12-01'],
  ['3', '199', '2025-11-01'], ['4', '199', '2025-10-01'],
]) {
  await choose('Calendar span', span);
  check(total() === expected && scope().includes(start + ' to 2026-01-31'), 'Monthly backward year boundary incorrect: ' + span);
  check(dates().at(-1) === '2026-01-01' && dates().every(day => day <= '2026-01-01'), 'Monthly chart includes subsequent months');
}
await choose('Ending month', '2025-11-01');
// November has 11 tokens; August through October contain no recorded rows.
for (const [span, start] of [['1', '2025-11-01'], ['2', '2025-10-01'], ['3', '2025-09-01'], ['4', '2025-08-01']]) {
  await choose('Calendar span', span);
  check(total() === '11' && scope().includes(start + ' to 2025-11-30'), 'Monthly backward span incorrect: ' + span);
  check(trend().length === 12 && dates().at(-1) === '2025-11-01' && dates().every(day => day <= '2025-11-01'), 'Monthly context moved beyond selected ending month');
}
check(scope().includes('4 calendar months · 2025-08-01 to 2025-11-30'), 'Monthly backward span not explicit');
await choose('Ending month', '2023-11-01');
await choose('Calendar span', '1');
check(total() === '9,007,199,254,740,993' && trend().length === 1, 'Earliest month context should not invent earlier history');
""")

    def test_file_overview_week_expansion_year_boundary_context_and_future(self):
        for viewport, browser_timezone in (("1280,900", "UTC"), ("500,900", "America/Los_Angeles")):
            with self.subTest(viewport=viewport):
                self.browser(render_react_dashboard(expansion_fixture(), DIST),
                             OVERVIEW_CONTROLS + EXPANSION_CHECKS + r"""
await pickPeriod('Weekly');
check(dates().join(',') === '2025-12-08,2025-12-15,2025-12-22,2025-12-29,2026-01-05', 'Weekly context must retain five Mondays');
await expand('2025-12-29', '2026-01-04');
check(!scope().includes('2025-12-29'), 'Expanding prior context widened one-week cards');
await expand('2026-01-05', '2026-01-11');
check(barFor('2025-12-29').getAttribute('aria-expanded') === 'false', 'Prior week stayed expanded');
check(openPanel().querySelector('a[href="#/sessions/2026-01-08"]').textContent.includes('86 tokens'), 'Recorded future row silently omitted');
check(!openPanel().querySelector('a[href="#/sessions/2026-01-06"]').textContent.includes('Future'), 'Snapshot-local day treated as UTC future');
await closeExpansion();
await expand('2025-12-15', '2025-12-21');
check([...openPanel().querySelectorAll('.trend-contributions li')].every(node => node.dataset.state === 'missing'), 'Empty week invented usage');
barFor('2025-12-15').click(); await pause();
check(!openPanel(), 'Expanded bar must toggle closed');
await choose('Calendar span', '4');
await expand('2025-12-29', '2026-01-04');
check(scope().includes('4 calendar weeks'), 'Expansion changed backward span');
""", viewport=viewport, browser_timezone=browser_timezone)

    def test_file_overview_five_weeks_35_links_filters_and_selected_scope(self):
        for viewport in ("1280,900", "390,844"):
            for provider, model in ((None, None), ("alpha", None), ("alpha", "shared")):
                with self.subTest(viewport=viewport, provider=provider, model=model):
                    setup = 'const selection = ' + json.dumps([provider, model]) + ';\n'
                    self.browser(render_react_dashboard(expansion_fixture(), DIST),
                                 setup + PROVIDER_CHECKS + r"""
await pickPeriod('Weekly');
if (selection[0]) await choose('Provider', JSON.stringify(selection[0]));
if (selection[1]) await choose('Model', JSON.stringify(selection[1]));
check(dates().join() === '2025-12-08,2025-12-15,2025-12-22,2025-12-29,2026-01-05', 'Five consecutive Mondays not anchored to selected week');
checkProviders('2026-01-05', '2026-01-11');
const before = metrics().join('|') + modelNames().join('|') + providerPanel().innerText;
const visited = [];
for (const start of dates()) {
  const end = new Date(Date.parse(start) + 6 * 86400000).toISOString().slice(0, 10);
  const panel = await expand(start, end);
  const links = [...panel.querySelectorAll('.trend-all-sessions')];
  check(links.length === 7, 'Week does not expose seven accessible day links');
  visited.push(...links.map(node => node.querySelector('time').dateTime));
  check(metrics().join('|') + modelNames().join('|') + providerPanel().innerText === before, 'Context contaminated selected cards/models/provider totals');
  await closeExpansion();
}
check(visited.length === 35 && new Set(visited).size === 35 && visited[0] === '2025-12-08' && visited.at(-1) === '2026-01-11', 'Missing or duplicated context days');
await chooseDate('Week containing', '2026-02-03');
check(dates().join() === '2026-01-05,2026-01-12,2026-01-19,2026-01-26,2026-02-02', 'Month boundary lost five weeks');
checkProviders('2026-02-02', '2026-02-08');
await expand('2026-02-02', '2026-02-08');
check(openPanel().querySelectorAll('[data-state="future"]').length === 7 && barFor('2026-02-02').textContent.includes('Future date after snapshot'), 'Future week mislabeled as inactivity');
""", viewport=viewport, browser_timezone="America/Los_Angeles")

    def test_file_overview_week_context_clips_supported_calendar_limits(self):
        for anchor, expected, end in (
            ("0001-01-01", ["0001-01-01"], "0001-01-07"),
            ("0001-01-29", ["0001-01-01", "0001-01-08", "0001-01-15", "0001-01-22", "0001-01-29"], "0001-02-04"),
            ("9999-12-31", ["9999-11-29", "9999-12-06", "9999-12-13", "9999-12-20", "9999-12-27"], "9999-12-31"),
        ):
            with self.subTest(anchor=anchor):
                setup = 'const boundary = ' + json.dumps(dict(anchor=anchor, expected=expected, end=end)) + ';\n'
                self.browser(render_react_dashboard(expansion_fixture(), DIST),
                             setup + PROVIDER_CHECKS + r"""
await pickPeriod('Weekly'); await chooseDate('Week containing', boundary.anchor);
check(dates().join() === boundary.expected.join(), 'Calendar limits duplicated or invented weeks');
const description = document.querySelector('.trend-panel').getAttribute('aria-description');
check(description.includes(boundary.expected.length + ' Monday-start calendar weeks'), 'Accessible week count is not truthful');
if (boundary.anchor !== '0001-01-29') check(description.includes('clipped to supported dates'), 'Calendar clipping undisclosed');
checkProviders(boundary.expected.at(-1), boundary.end);
await expand(boundary.expected.at(-1), boundary.end);
if (boundary.anchor === '9999-12-31') {
  check(openPanel().querySelectorAll('.trend-all-sessions').length === 5 && openPanel().textContent.includes('Calendar week clipped'), 'Upper boundary invented year 10000 links or claimed a full week');
}
check(!document.querySelector('[role="alert"]'), 'Valid boundary rejected');
""", viewport="390,844")

    def test_file_overview_month_expansion_complete_calendar_lengths(self):
        for viewport in ("1280,900", "500,900"):
            with self.subTest(viewport=viewport):
                self.browser(render_react_dashboard(expansion_fixture(), DIST),
                             OVERVIEW_CONTROLS + EXPANSION_CHECKS + r"""
await pickPeriod('Monthly');
check(dates().length === 12 && dates()[0] === '2025-02-01' && dates().at(-1) === '2026-01-01', 'Monthly context must cap at twelve');
for (const [start, end] of [['2026-01-01', '2026-01-31'], ['2025-04-01', '2025-04-30'], ['2025-02-01', '2025-02-28'], ['2024-02-01', '2024-02-29']]) {
  await choose('Ending month', start);
  await expand(start, end);
  await closeExpansion();
}
await choose('Ending month', '2026-01-01');
await expand('2025-12-01', '2025-12-31');
check(scope().includes('2026-01-01 to 2026-01-31'), 'Older month expansion widened cards');
await choose('Calendar span', '4');
check(!openPanel(), 'Span change kept stale expansion');
await expand('2025-12-01', '2025-12-31');
check(scope().includes('2025-10-01 to 2026-01-31'), 'Expansion changed backward month span');
""", viewport=viewport, browser_timezone="America/Los_Angeles")

    def test_file_overview_expansion_filtered_links_reconcile_with_files(self):
        for period in ("Weekly", "Monthly"):
            for provider, model in ((None, None), ("alpha", None), (None, "shared"),
                                    ("alpha", "shared"), ("", ""),
                                    ("broker +β&%?#", "</script><img src=x onerror=alert(1)>")):
                with self.subTest(period=period, provider=provider, model=model):
                    setup = "const selection = " + json.dumps(dict(period=period, provider=provider, model=model)) + ";\n"
                    self.browser(render_react_dashboard(expansion_fixture(), DIST),
                                 setup + OVERVIEW_CONTROLS + EXPANSION_CHECKS + r"""
await pickPeriod(selection.period);
if (selection.provider !== null) await choose('Provider', JSON.stringify(selection.provider));
if (selection.model !== null) await choose('Model', JSON.stringify(selection.model));
if (selection.period === 'Weekly') {
  await chooseDate('Week containing', '2025-12-29');
  await expand('2025-12-29', '2026-01-04');
} else await expand('2026-01-01', '2026-01-31');
const item = openPanel().querySelector('.trend-contributions [data-recorded="true"]');
const dateLink = item.querySelector('.trend-all-sessions');
const day = dateLink.querySelector('time').dateTime;
const rows = filteredRows().filter(row => row.day === day);
const expected = tokenTotal(rows).toLocaleString('en-US'), cost = '$' + costTotal(rows).toFixed(4);
const target = '#/sessions/' + day + suffix();
check(dateLink.getAttribute('href') === '#/sessions/' + day, 'Weekly/monthly date must open all sessions');
const filteredLink = item.querySelector('.trend-filtered-sessions');
check((filteredLink ?? dateLink).getAttribute('href') === target, 'Separate filtered link lost provider/model query');
// First exercise the date link even with active filters: all contributing files.
dateLink.click(); await pause();
const allRows = snapshot.rows.filter(row => row.day === day);
check(document.querySelectorAll('.session-file').length === allRows.length, 'Date link retained provider/model filtering');
for (const selector of ['.sessions-totals', '.session-reconciliation']) {
  const values = [...document.querySelectorAll(selector + ' dd')].map(node => node.textContent);
  check(values[0] === tokenTotal(allRows).toLocaleString('en-US') && values.at(-1) === '$' + costTotal(allRows).toFixed(4), 'Unfiltered date totals did not reconcile');
}
check(!document.querySelector('[role="alert"]'), 'All-day reconciliation failed');
if (filteredLink) { filteredLink.click(); await pause(); }
check(location.hash === target && document.querySelector('h1').textContent === 'Sessions · ' + day, 'Expanded link did not navigate');
check(document.querySelectorAll('.session-file').length === rows.length, 'Matching measured file contributions lost');
for (const selector of ['.sessions-totals', '.session-reconciliation']) {
  const values = [...document.querySelectorAll(selector + ' dd')].map(node => node.textContent);
  check(values[0] === expected && values.at(-1) === cost, 'Expanded daily amount/cost does not reconcile with files');
}
check(!document.querySelector('[role="alert"]') && document.querySelector('.sessions-view').textContent.includes('Displayed contributions match'), 'Expansion reconciliation failed');
check(document.documentElement.scrollWidth <= innerWidth, 'Expanded-link Sessions overflow mobile');
check(!document.querySelector('img') && !document.querySelector('.sessions-view').textContent.includes('PRIVATE_'), 'Private content or executable label rendered');
document.querySelector('.session-actions a[href="#/overview"]').click(); await pause();
check(!document.querySelector('.trend-expansion:not([hidden])') && document.querySelectorAll('.trend-day[href]').length === 7, 'Back lost Daily links or kept stale expansion');
document.querySelector('nav a[href="#/stats"]').click(); await pause();
check(document.querySelector('.stats-view') && document.querySelectorAll('.heatmap-week').length === 52, 'Stats route changed');
""", viewport="500,900")

    def test_file_overview_expansion_missing_day_link_and_empty_snapshot(self):
        for empty_snapshot in (False, True):
            with self.subTest(empty_snapshot=empty_snapshot):
                data = expansion_fixture()
                if empty_snapshot:
                    data["rows"] = data["sessions"] = []
                    data["stats"].update(oldestDay=None, newestDay=None)
                self.browser(render_react_dashboard(data, DIST),
                             OVERVIEW_CONTROLS + EXPANSION_CHECKS + r"""
await pickPeriod('Monthly');
await expand('2026-01-01', '2026-01-31');
const link = openPanel().querySelector('.trend-contributions [data-recorded="false"] a');
const target = link.getAttribute('href');
link.click(); await pause();
check(location.hash === target && document.querySelector('.sessions-count').textContent.startsWith('0 measured'), 'Missing day invented files');
check(document.querySelector('.session-reconciliation').textContent.includes('No recorded daily rows') && !document.querySelector('[role="alert"]'), 'Missing-day reconciliation incorrect');
""", viewport="500,900")

    def test_file_overview_expansion_resets_on_all_selection_changes(self):
        for action in ("period", "week", "month", "span", "provider", "model", "reset", "preset"):
            with self.subTest(action=action):
                setup = "const action = " + json.dumps(action) + ";\n"
                self.browser(render_react_dashboard(expansion_fixture(), DIST),
                             setup + OVERVIEW_CONTROLS + EXPANSION_CHECKS + r"""
await pickPeriod(action === 'month' ? 'Monthly' : 'Weekly');
if (action === 'month') await expand('2026-01-01', '2026-01-31');
else await expand('2026-01-05', '2026-01-11');
if (action === 'period') await pickPeriod('All time');
if (action === 'week') await chooseDate('Week containing', '2025-12-29');
if (action === 'month') await choose('Ending month', '2025-12-01');
if (action === 'span') await choose('Calendar span', '2');
if (action === 'provider') await choose('Provider', JSON.stringify('alpha'));
if (action === 'model') await choose('Model', JSON.stringify('shared'));
if (action === 'reset') await reset();
if (action === 'preset') await pickPeriod('This Month');
check(!openPanel() && !document.querySelector('.trend-period[aria-expanded="true"]'), 'Selection retained stale expansion: ' + action);
if (action === 'period' || action === 'reset') check(document.querySelector('.trend a[href^="#/sessions/"]'), 'Direct Daily/All-time links lost');
""")

    def test_file_overview_expansion_quick_ranges_keep_card_scope(self):
        self.browser(render_react_dashboard(expansion_fixture(), DIST),
                     OVERVIEW_CONTROLS + EXPANSION_CHECKS + r"""
for (const [preset, start, end] of [['Last Week', '2025-12-29', '2026-01-04'], ['Last Month', '2025-12-01', '2025-12-31'], ['This Month', '2026-01-01', '2026-01-31']]) {
  await pickPeriod(preset);
  check(!openPanel(), 'Quick range kept stale expansion');
  const cardAmount = total();
  await expand(start, end);
  check(total() === cardAmount && select('Period').value === preset, 'Expansion cleared quick range');
  if (preset === 'This Month') {
    check(scope().includes('2026-01-01 to 2026-01-06'), 'This Month cards extended past snapshot');
    check(openPanel().querySelectorAll('.trend-contributions a').length === 31, 'This Month expansion truncated future days');
    check(!barFor(start).textContent.includes(cardAmount + ' tokens'), 'Future recorded contribution must remain in complete-month trend, not cards');
  }
}
""")

    def test_file_overview_quick_ranges_use_snapshot_calendar(self):
        self.browser(render_react_dashboard(overview_fixture(), DIST), OVERVIEW_CONTROLS + r"""
for (const [name, amount, start, end, contextEnd] of [
  ['Today', '0', '2026-01-06', '2026-01-06', '2026-01-06'],
  ['Yesterday', '31', '2026-01-05', '2026-01-05', '2026-01-05'],
  ['Last 7 Days', '84', '2025-12-31', '2026-01-06', '2026-01-06'],
  ['Last Week', '108', '2025-12-29', '2026-01-04', '2025-12-29'],
  ['This Month', '57', '2026-01-01', '2026-01-06', '2026-01-01'],
  ['Last Month', '131', '2025-12-01', '2025-12-31', '2025-12-01'],
]) {
  await pickPeriod(name);
  check(total() === amount && scope().includes(start) && scope().includes(end), 'Preset dates/totals incorrect: ' + name);
  check(select('Period').value === name && dates().at(-1) === contextEnd, 'Preset selection/context incorrect: ' + name);
  check(controls.querySelectorAll('select').length === 3, 'Quick range exposed irrelevant manual controls');
}
check(trend().length === 12 && dates()[0] === '2025-01-01', 'Last Month must use monthly context across year boundary');
await pickPeriod('Last 7 Days');
check(metrics().join('|') === '84|10|0.00%|$1.7500', 'Quick range metrics not summed together');
check(trend().length === 7 && dates()[0] === '2025-12-31' && dates()[6] === '2026-01-06', 'Quick range daily context incorrect');
await pickPeriod('Daily');
check(total() === '31' && select('Period').value === 'day', 'Manual mode retained quick range');
await pickPeriod('This Month');
await pickPeriod('Monthly');
await choose('Calendar span', '2');
check(scope().includes('2 calendar months · 2025-12-01 to 2026-01-31') && total() === '188' && select('Period').value === 'month', 'Manual backward span retained preset range');
check(select('Ending month').value === '2026-01-01' && dates().at(-1) === '2026-01-01', 'Manual preset span advanced ending month');
await pickPeriod('Today');
await pickPeriod('Daily');
await chooseDay('2025-12-29');
check(total() === '55' && select('Period').value === 'day', 'Recorded date retained quick range');
""")

    def test_file_overview_provider_model_filters_and_route_isolation(self):
        self.browser(render_react_dashboard(overview_fixture(), DIST), OVERVIEW_CONTROLS + r"""
await pickPeriod('All time');
await choose('Model', JSON.stringify('shared'));
check(total() === '136' && modelNames().length === 2, 'Actual shared model/provider pairs were merged or lost');
await choose('Provider', JSON.stringify('alpha'));
check(total() === '89' && select('Model').value === '' && select('Model').options.length === 3, 'Provider must reset and scope models');
await pickPeriod('Last Week');
check(metrics().join('|') === '58|8|60.00%|$0.6250', 'Provider/range metrics inconsistent');
await choose('Model', JSON.stringify('shared'));
check(metrics().join('|') === '55|5|60.00%|$0.1250' && modelNames().join(',') === 'shared', 'Combined model/range metrics inconsistent');
check(trend().at(-1).textContent.includes('55 tokens'), 'Trend ignored provider/model filter');
await choose('Provider', JSON.stringify('beta'));
check(total() === '50' && select('Model').value === '' && modelNames().join(',') === 'shared', 'Provider metadata inferred from launch model');
await choose('Model', JSON.stringify('ancient'));
check(total() === '0' && !modelNames().length && trend().every(node => node.dataset.recorded === 'false'), 'Empty combined selection leaked data');
await pickPeriod('Last Month');
await choose('Provider', JSON.stringify('alpha'));
await choose('Model', JSON.stringify('shared'));
check(total() === '55' && select('Period').value === 'Last Month', 'Last Month lost filter isolation');
await reset();
check(total() === '31' && select('Period').value === 'day' && !select('Provider').value && !select('Model').value, 'Reset filters failed');
await choose('Provider', JSON.stringify('alpha'));
window.location.hash = '#/companies/OpenAI'; await pause();
check(document.querySelector('h1').textContent === 'OpenAI' && total() === '0', 'Overview filter leaked into Companies');
window.location.hash = '#/agents/worker'; await pause();
check(document.querySelector('h1').textContent === 'worker' && total() === '0', 'Overview filter leaked into Agents');
window.location.hash = '#/overview'; await pause();
check(total() === '31' && document.querySelector('.overview-selects select').value === 'day', 'Overview remount did not restore Daily');
check(!document.querySelectorAll('.overview-selects select')[1].value, 'Overview remount retained provider');
""")

    def test_file_overview_responsive_controls_and_leap_day(self):
        data = fixture()
        data["rows"] = [{**data["rows"][1], "day": "2024-02-29"}]
        data["stats"].update(asOf="2024-03-01T00:15:00+14:00", timezone="Pacific/Kiritimati",
                             oldestDay="2024-02-29", newestDay="2024-02-29")
        self.browser(render_react_dashboard(data, DIST), OVERVIEW_CONTROLS + r"""
await pickPeriod('Yesterday');
check(total() === '11' && scope().includes('2024-02-29'), 'Leap-day preset or timezone drift');
check(dates().join(',') === '2024-02-23,2024-02-24,2024-02-25,2024-02-26,2024-02-27,2024-02-28,2024-02-29', 'Leap-day daily context incorrect');
await chooseDay('2024-02-28');
check(total() === '0' && scope().includes('Selected day · 2024-02-28'), 'Calendar cannot select leap-month gap');
await chooseDay('2024-02-29');
check(total() === '11' && dayInput().value === '2024-02-29', 'Calendar rejected leap day');
await pickPeriod('Last Month');
check(select('Period').value === 'Last Month' && scope().includes('2024-02-01 to 2024-02-29') && total() === '11', 'Last Month must include leap day using snapshot-local March');
check(dates().join(',') === '2024-02-01', 'Last Month did not use monthly context');
await pickPeriod('Monthly');
check(scope().includes('2024-02-01 to 2024-02-29') && total() === '11', 'Leap calendar month incorrect');
await choose('Calendar span', '4');
check(scope().includes('2023-11-01 to 2024-02-29') && total() === '11', 'Backward span lost leap day');
check(dates().join(',') === '2024-02-01', 'Leap month chart invented earlier history or later months');
await pickPeriod('Weekly');
await choose('Calendar span', '4');
check(scope().includes('2024-02-05 to 2024-03-03') && total() === '11', 'Backward weekly span lost leap day');
check(dates().join(',') === '2024-01-29,2024-02-05,2024-02-12,2024-02-19,2024-02-26', 'Leap week context must include five weeks without subsequent weeks');
check(document.documentElement.scrollWidth <= window.innerWidth, 'Overview overflows narrow viewport');
for (const node of controls.querySelectorAll('button, select, input')) {
  const rect = node.getBoundingClientRect();
  check(rect.width > 0 && rect.left >= 0 && rect.right <= window.innerWidth, 'Control clipped in narrow viewport');
  check(rect.height >= 44, 'Overview control below 44px touch target: ' + node.tagName);
  if (!node.disabled) { node.focus(); check(document.activeElement === node, 'Control not focusable'); }
}
""", viewport="500,900")

    def test_file_overview_compact_dropdown_layout_and_accessibility(self):
        for viewport in ("1280,900", "500,900"):
            with self.subTest(viewport=viewport):
                observed = self.browser(render_react_dashboard(overview_fixture(), DIST), OVERVIEW_CONTROLS + r"""
check(!controls.querySelector('.period-buttons, .preset-buttons, [aria-pressed]'), 'Obsolete pill controls remain');
check(controls.querySelectorAll('button').length === 1 && resetButton.textContent === 'Reset filters', 'Only Reset filters should be a button');
check([...select('Period').options].map(node => node.textContent).join('|') === 'All time|Daily|Weekly|Monthly|Custom range|Today|Yesterday|Last 7 Days|Last Week|This Month|Last Month', 'Period select lost manual or quick ranges');
const help = controls.querySelector('details');
check(!help.open && help.querySelector('summary').textContent === 'Range help', 'Help must start collapsed with a named disclosure');
evidence.viewport = window.innerWidth;
evidence.states = [];
for (const name of ['Daily', 'All time', 'Weekly', 'Monthly', 'Custom range', 'Last Month']) {
  await pickPeriod(name);
  const names = [...controls.querySelectorAll('label')].map(node => node.firstChild.textContent);
  const expected = name === 'Daily' ? ['Period', 'Day', 'Provider', 'Model'] : ['Period', 'Provider', 'Model'];
  if (name === 'Weekly') expected.push('Week containing', 'Calendar span');
  if (name === 'Monthly') expected.push('Ending month', 'Calendar span');
  if (name === 'Custom range') expected.push('Start', 'End');
  check(names.join('|') === expected.join('|'), 'Duplicate, misplaced, or irrelevant controls: ' + name);
  check(document.documentElement.scrollWidth <= window.innerWidth, 'Page overflow: ' + name);
  const bounds = [];
  for (const node of controls.querySelectorAll('button, select, input, summary')) {
    const rect = node.getBoundingClientRect();
    check(rect.height >= 44 && rect.width > 0 && rect.width <= 224, 'Control not compact/touchable: ' + name + ' ' + node.tagName);
    check(rect.left >= 0 && rect.right <= window.innerWidth, 'Control clipped: ' + name);
    if (node.matches('select, input')) check(node.labels.length === 1, 'Field must have one native accessible label');
    if (!node.disabled) {
      node.focus();
      check(document.activeElement === node && getComputedStyle(node).outlineStyle !== 'none', 'Keyboard focus not visible');
    }
    bounds.push([node.tagName, Math.round(rect.width), Math.round(rect.height)]);
  }
  const height = controls.getBoundingClientRect().height;
  check(height <= (window.innerWidth > 1000 ? 200 : 430), 'Oversized control bar: ' + JSON.stringify({name, height, children: [...controls.children].map(node => ({className: node.className, width: node.getBoundingClientRect().width, height: node.getBoundingClientRect().height}))}));
  if (window.innerWidth > 1000) {
    const first = select('Period').getBoundingClientRect();
    check(select('Provider').getBoundingClientRect().top === first.top && select('Model').getBoundingClientRect().top === first.top && resetButton.getBoundingClientRect().top === first.top, 'Primary filters must share a desktop row');
    if (name === 'Daily') {
      const day = dayInput().getBoundingClientRect();
      check(day.top === first.top && day.left > first.right && day.right < select('Provider').getBoundingClientRect().left, 'Day calendar must sit directly after Period');
    }
  }
  evidence.states.push({name, height: Math.round(height), bounds});
}
help.querySelector('summary').click(); await pause();
check(help.open && help.textContent.includes('snapshot date 2026-01-06'), 'Help disclosure failed');
check(document.documentElement.scrollWidth <= window.innerWidth, 'Expanded help overflows');
""", viewport=viewport)
                print("Overview CSS bounds: " + json.dumps(observed["evidence"], sort_keys=True))

    def test_file_overview_empty_labels_and_short_month_context(self):
        data = fixture()
        data["rows"][0].update(provider="", model="")
        self.browser(render_react_dashboard(data, DIST), OVERVIEW_CONTROLS + r"""
await pickPeriod('All time');
await choose('Provider', '""');
await choose('Model', '""');
check(total() === '9,007,199,254,741,002', 'Unspecified provider/model confused with all');
check(select('Provider').selectedOptions[0].textContent === '(unspecified provider)', 'Unspecified provider not labeled');
await pickPeriod('Monthly');
check(total() === '9,007,199,254,741,002' && trend().length === 1, 'Short monthly history incorrect');
await pickPeriod('Today');
check(total() === '0' && metrics()[2] === 'N/A' && metrics()[3] === '$0.0000', 'Empty day metrics incorrect');
check(trend().length === 7 && scope().includes('No recorded usage'), 'Empty day context missing');
""")

    def test_file_empty_snapshot_has_honest_zero_and_na(self):
        data = fixture()
        data["rows"] = []
        data["stats"]["oldestDay"] = data["stats"]["newestDay"] = None
        self.browser(render_react_dashboard(data, DIST), OVERVIEW_CONTROLS + r"""
check(select('Period').value === 'day' && dayInput().value === '2026-06-03' && scope().includes('No recorded usage'), 'Empty daily default not labeled');
check(document.querySelector('.metric strong')?.textContent === '0', 'Empty total incorrect');
check(document.querySelectorAll('.metric strong')[2]?.textContent === 'N/A', 'Zero-denominator share incorrect');
check(trend().length === 7 && trend().every(node => node.dataset.recorded === 'false'), 'Empty daily trend dishonest');
await pickPeriod('All time');
check(document.querySelector('main')?.textContent.includes('No recorded usage yet.'), 'Empty all-time trend dishonest');
check(!document.querySelector('.quality, footer'), 'Empty Overview global context still mounted');
for (const [name, count] of [['Daily', 7], ['Weekly', 5], ['Monthly', 1]]) {
  await pickPeriod(name);
  check(document.querySelectorAll('.trend li').length === count, 'Empty calendar context missing: ' + name);
  check([...document.querySelectorAll('.trend li')].every(node => node.dataset.recorded === 'false'), 'Empty snapshot invented records');
  check(document.querySelector('.overview-scope').textContent.includes('No recorded usage'), 'Empty selection not explained');
}
window.location.hash = '#/companies'; await pause();
check(document.querySelectorAll('.company-card').length === 5, 'Empty snapshot lost fixed slots');
check(document.querySelector('footer')?.textContent.includes('No dates available'), 'Empty dates dishonest');
check([...document.querySelectorAll('.company-card strong')].every(node => node.textContent === '0 tokens'), 'Empty snapshot fabricated company totals');
window.location.hash = '#/companies/OpenAI'; await pause();
check(document.querySelector('.metric strong')?.textContent === '0', 'Empty company total incorrect');
check(document.querySelectorAll('.metric strong')[2]?.textContent === 'N/A', 'Empty company cache share incorrect');
check(!document.querySelector('.usage-buckets li'), 'Empty company fabricated dates');
""")

    def test_file_company_routes_filters_buckets_and_radix_controls(self):
        self.browser(render_react_dashboard(company_fixture(), DIST), r"""
const visit = async hash => { window.location.hash = hash; await pause(); };
const total = () => document.querySelector('.metric strong')?.textContent;
const select = label => [...document.querySelectorAll('.usage-filters label')].find(node => node.firstChild.textContent === label)?.querySelector('select');
const choose = async (label, value) => {
  const node = select(label);
  check(!!node, 'Missing labeled control: ' + label);
  node.value = value; node.dispatchEvent(new Event('change', {bubbles: true})); await pause();
};
const buckets = () => [...document.querySelectorAll('.usage-buckets li')].map(node => node.textContent);
await visit('#/companies');
check(document.querySelectorAll('.company-card').length === 5, 'Index must have exactly five slots');
check([...document.querySelectorAll('.company-card h2')].map(node => node.textContent).join(',') === 'OpenAI,DeepSeek,xAI,Google,Anthropic', 'Slot rank incorrect');
check(document.querySelector('.rt-Card') && document.querySelector('.rt-Badge'), 'Radix cards and badges not rendered');
check(!document.querySelector('.company-grid')?.textContent.includes('Alibaba'), 'Sixth company leaked into index');
document.querySelector('.company-card a[href="#/companies/OpenAI"]').click(); await pause();
check(document.querySelector('h1')?.textContent === 'OpenAI', 'Detail route did not render');
check(document.querySelector('nav [aria-current="page"]')?.textContent === 'Companies', 'Detail navigation not active');
check(!document.querySelector('.company-grid'), 'Index and detail are not separate');
check(total() === '85', 'Parent/child or company totals incorrect');
check(document.querySelectorAll('.metric strong')[2]?.textContent === '42.86%', 'Cache denominator incorrect');
check(buckets().length === 3, 'Daily buckets incorrect');
check(!document.querySelector('main').textContent.includes('unknown/gpt-synthetic'), 'Unknown namespace misclassified');
check(!document.querySelector('main').textContent.includes('claude-synthetic'), 'Another company leaked');
check(!document.querySelector('img'), 'Hostile company label became markup');
const modelTable = document.querySelector('[aria-label="Model totals"]');
const providerTable = document.querySelector('[aria-label="Provider totals"]');
check(modelTable.querySelectorAll('tbody tr').length === 3, 'Model/provider pairs merged');
check(providerTable.querySelectorAll('tbody tr').length === 3, 'Provider totals missing');
check(modelTable.textContent.includes('gpt-5-<img src=x onerror=alert(1)>'), 'Hostile model not plain text');
check(providerTable.textContent.includes('</script><img src=x onerror=alert(1)>'), 'Hostile provider not plain text');
check(document.querySelector('.usage-filters button')?.disabled, 'Reset should start disabled');
await choose('Period', 'week');
check(buckets().length === 2 && buckets()[0].includes('Week of 2026-01-26') && buckets()[0].includes('82 tokens'), 'Monday boundary incorrect');
check(buckets()[1].includes('Week of 2026-02-02') && buckets()[1].includes('3 tokens'), 'Monday event moved backwards');
await choose('Period', 'month');
check(buckets().length === 2 && buckets()[0].includes('2026-01') && buckets()[0].includes('55 tokens') && buckets()[1].includes('30 tokens'), 'Month boundary incorrect');
await choose('Provider', JSON.stringify('openai'));
check(total() === '55', 'Provider filter incorrect');
check(document.querySelectorAll('.metric strong')[2]?.textContent === '60.00%', 'Filtered cache share incorrect');
check(select('Model').options.length === 2, 'Models not scoped to provider');
await choose('Model', JSON.stringify('gpt-synthetic'));
const reset = document.querySelector('.usage-filters button');
check(reset?.classList.contains('rt-Button') && !reset.disabled && reset.textContent === 'Reset filters', 'Radix reset control missing');
reset.focus(); check(document.activeElement === reset, 'Reset not keyboard focusable');
reset.click(); await pause();
check(total() === '85' && select('Period').value === 'day' && select('Provider').value === '' && select('Model').value === '', 'Radix reset interaction failed');
check(document.querySelector('.usage-filters button').disabled, 'Reset state not updated');
await choose('Model', JSON.stringify('gpt-5-<img src=x onerror=alert(1)>'));
check(total() === '3' && document.querySelectorAll('.metric strong')[2]?.textContent === 'N/A', 'Output-only cache share must be N/A');
await visit('#/companies/DeepSeek');
check(total() === '0' && document.querySelectorAll('.metric strong')[2]?.textContent === 'N/A', 'Empty fixed company totals incorrect');
check(select('Model').value === '' && select('Provider').value === '' && select('Period').value === 'day', 'Filters bled into another company');
check(document.querySelector('main').textContent.includes('No measured usage for DeepSeek'), 'Empty company dishonest');
await visit('#/companies/OpenAI');
check(total() === '85', 'Company filters did not reset on route change');
await choose('Provider', JSON.stringify('openrouter'));
check(total() === '27', 'Linked child omitted or duplicated');
document.querySelector('.company-back').click(); await pause();
check(document.querySelector('h1')?.textContent === 'Companies', 'Back navigation failed');
check(document.querySelector('.company-card strong')?.textContent === '85 tokens', 'Detail filter bled into index');
await visit('#/overview');
await choose('Period', 'all');
check(total() === '1,000,184', 'Company filters bled into Overview or unclassified rows omitted');
check(getComputedStyle(document.documentElement).backgroundColor === 'rgb(13, 16, 23)', 'Original dark theme lost');
await visit('#/companies/Alibaba');
check(document.querySelector('h1')?.textContent === 'View not found', 'Unselected company route accepted');
await visit('#/companies/%E0%A4%A');
check(document.querySelector('h1')?.textContent === 'View not found', 'Malformed route not handled');
""")

    def test_file_agent_six_row_trend_compact_type_and_hidden_context(self):
        data = agent_fixture()
        base = data["rows"][0]
        data["rows"] = [dict(base, day=f"2026-01-{day:02d}") for day in range(1, 7)]
        for viewport in ("1280,900", "390,844", "320,740"):
            with self.subTest(viewport=viewport):
                self.browser(render_react_dashboard(data, DIST), AGENT_PANEL_CHECKS + r"""
checkScope(); checkLayout();
const region = document.querySelector('.agent-trend-content');
check(region.querySelectorAll('.trend > li').length === 7 && region.querySelectorAll('[data-recorded="true"]').length === 6, 'Six recorded days must keep all six and gain one gap');
const gap = region.querySelector('[data-recorded="false"]');
check(gap.querySelector('time').dateTime === '2025-12-31' && gap.textContent.includes('No recorded usage'), 'Preceding seventh day is not an honest gap');
check(document.querySelector('.metric strong').textContent === '330', 'Calendar gap changed six recorded totals');
check(document.querySelectorAll('.agent-panels .models > li, .agent-panels .model-empty-slot').length === 5, 'Six-day fixture must retain five visual ranking rows');
check(region.scrollHeight <= region.clientHeight, 'Seven calendar rows have an unnecessary vertical scrollbar');
check(region.scrollWidth <= region.clientWidth, 'Larger Agent type introduced horizontal scrolling');
const context = document.getElementById('agent-trend-context');
check(document.querySelector('.agent-panels .trend-panel').getAttribute('aria-describedby') === context.id, 'Trend context not accessible');
check(context.classList.contains('visually-hidden') && context.getBoundingClientRect().width <= 1 && getComputedStyle(context).clipPath !== 'none', 'Muted context paragraph is still visible');
check(context.textContent.includes('No recorded usage, not recorded zero') && context.textContent.includes('Future means after snapshot'), 'Hidden context lost calendar honesty');
const rem = parseFloat(getComputedStyle(document.documentElement).fontSize);
for (const selector of ['.trend time', '.agent-trend-token-amount', '.agent-trend-cache-share', '.agent-trend-gap']) {
  const size = parseFloat(getComputedStyle(region.querySelector(selector)).fontSize);
  check(Math.abs(size - rem) < 0.01, 'Agent date/token/percentage/gap text must compute to 1rem');
}
check(parseFloat(getComputedStyle(document.querySelector('.model-cache-share')).fontSize) < rem, 'Trend typography changed ranking percentages');
check(context.textContent.includes('Exact role worker') && context.textContent.includes('current provider/model filters'), 'Chart scope lost accessible context');
check(!document.querySelector('.agent-panels .trend-panel [tabindex]'), 'Static chart has an unnecessary tab stop');
checkPageReachability();
""", viewport=viewport)

    def test_file_agent_every_bucket_uses_page_flow_at_all_history_lengths(self):
        for count in (6, 7, 8, 30):
            for period in ("day", "week", "month"):
                data = agent_fixture()
                base = data["rows"][0]
                # Distinct accepted buckets at each granularity, not filler dates.
                dates = [(datetime(2024, 1, 1) + timedelta(days=index * (7 if period == "week" else 1))).date().isoformat()
                         if period != "month" else f"{2024 + index // 12}-{index % 12 + 1:02d}-01"
                         for index in range(count)]
                data["rows"] = [dict(base, day=day) for day in dates]
                data["stats"].update(oldestDay=dates[0], newestDay=dates[-1])
                for viewport in ("1280,1100", "1280,900", "390,844", "320,740"):
                    with self.subTest(count=count, period=period, viewport=viewport):
                        self.browser(render_react_dashboard(data, DIST), AGENT_PANEL_CHECKS +
                                     "const expectedCount = " + str(max(7, count) if period == "day" else count) + ";\n" +
                                     "await choose('Period', " + json.dumps(period) + ");\n" + r"""
checkScope(); checkLayout();
const region = document.querySelector('.agent-trend-content');
const rows = [...region.querySelectorAll('.trend > li')];
check(rows.length === expectedCount && region.scrollTop === 0, 'History truncated or starts scrolled');
window.scrollTo(0, 0);
if (innerWidth > 1050 && select('Period').value === 'day' && rows.length >= 7) {
  // The unchanged header occupies ~560px; shorter windows need ordinary page scroll.
  if (innerHeight >= 1000) check(rows[6].getBoundingClientRect().bottom <= innerHeight, 'Seventh bucket is not visible on initial tall desktop page');
  window.scrollTo(0, window.scrollY + region.parentElement.getBoundingClientRect().top);
  check(rows[6].getBoundingClientRect().bottom <= innerHeight && region.scrollTop === 0, 'Seventh bucket is hidden when chart enters the desktop viewport');
}
const trend = region.parentElement.getBoundingClientRect();
const rowHeight = Math.min(...rows.map(row => row.getBoundingClientRect().height));
check(region.clientHeight >= rowHeight * expectedCount, 'Long list is height constrained');
if (innerWidth <= 1050 || expectedCount === 30) check(trend.bottom - rows.at(-1).getBoundingClientRect().bottom < 40, 'Unused bottom space remains below natural trend');
if (expectedCount === 30) {
  const rankingContent = document.querySelector('#agent-ranking-content').getBoundingClientRect();
  check(document.documentElement.scrollHeight > innerHeight && trend.height > rankingContent.height * 2, 'Long history does not grow page while keeping ranking content compact');
  check([...document.querySelectorAll('.model-toggle, .model-empty-slot')].every(node => node.getBoundingClientRect().height < 120), 'Ranking rows stretched to match long trend');
}
const before = document.querySelector('.metrics').innerHTML + document.querySelector('.models-panel').innerHTML;
checkPageReachability();
check(document.querySelector('.metrics').innerHTML + document.querySelector('.models-panel').innerHTML === before, 'Page scrolling changed cards or rankings');
await mode('Providers'); checkScope(); checkLayout(); checkPageReachability();
""", viewport=viewport)

    def test_file_agent_daily_calendar_bounds_gaps_zero_future_and_filters(self):
        for last_day, first_day, expected_count in (("0001-01-03", "0001-01-01", 3),
                                                     ("9999-12-31", "9999-12-25", 7)):
            data = agent_fixture()
            base = data["rows"][0]
            data["rows"] = [dict(base, day=last_day)]
            data["stats"].update(oldestDay=last_day, newestDay=last_day)
            with self.subTest(last_day=last_day):
                self.browser(render_react_dashboard(data, DIST), AGENT_PANEL_CHECKS +
                             f"const firstDay = {json.dumps(first_day)}, expectedCount = {expected_count};\n" + r"""
checkScope(); checkLayout(); checkPageReachability();
const dates = [...document.querySelectorAll('.agent-panels time')].map(node => node.dateTime);
check(dates.length === expectedCount && dates[0] === firstDay && dates.every(day => /^\d{4}-\d{2}-\d{2}$/.test(day)), 'Daily window exceeded supported calendar bounds');
""")
        data = agent_fixture()
        base = data["rows"][0]
        data["rows"] = [dict(base, day="2026-06-01"),
                        dict(base, day="2026-09-21", model="earlier"),
                        dict(base, day="2026-09-24", input=0, output=0, cacheRead=0, cacheWrite=0, cost=0),
                        dict(base, day="2026-09-26"),
                        dict(base, day="2026-09-30", role="worker-extra"),
                        dict(base, day="2026-10-01", role=None)]
        data["stats"].update(asOf="2026-09-22T12:00:00+00:00", oldestDay="2026-06-01", newestDay="2026-10-01")
        for viewport in ("1280,900", "390,844", "320,740"):
            with self.subTest(viewport=viewport):
                self.browser(render_react_dashboard(data, DIST), AGENT_PANEL_CHECKS + r"""
checkScope(); checkLayout(); checkPageReachability();
const row = day => [...document.querySelectorAll('.agent-panels .trend > li')].find(node => node.querySelector('time').dateTime === day);
check(row('2026-06-01')?.dataset.recorded === 'true', 'Earlier recorded history lost');
check(row('2026-09-20')?.dataset.state === 'missing', 'Latest seven-day window absent');
check(row('2026-09-24').textContent.includes('Recorded zero') && row('2026-09-24').dataset.recorded === 'true', 'Recorded zero confused with gap');
check(row('2026-09-25').dataset.state === 'missing' && row('2026-09-25').textContent.includes('Future date'), 'Future gap not distinguished');
check(row('2026-09-26').dataset.state === 'future' && !row('2026-09-30') && !row('2026-10-01'), 'Other owners shifted latest selected day');
await choose('Model', JSON.stringify('earlier')); checkScope(); checkLayout(); checkPageReachability();
check(row('2026-09-15')?.dataset.state === 'missing' && !row('2026-09-26'), 'Filter did not re-anchor calendar window');
for (const period of ['week', 'month']) {
  await choose('Period', period); checkScope(); checkLayout();
  check(!document.querySelector('.agent-panels [data-recorded="false"]'), 'Daily gaps leaked into weekly/monthly grouping');
}
window.location.hash = '#/agents/unused'; await pause();
checkScope(); checkLayout();
check(!document.querySelector('.agent-panels .trend li'), 'Empty role gained an invented calendar');
""", viewport=viewport)

    def test_file_agent_panels_all_dates_rankings_filters_and_responsive(self):
        for viewport in ("1280,900", "390,844", "320,740"):
            with self.subTest(viewport=viewport):
                self.browser(render_react_dashboard(agent_panels_fixture(), DIST), AGENT_PANEL_CHECKS + r"""
checkScope(); checkLayout();
const januaryFirst = [...document.querySelectorAll('.agent-panels .trend > li')].find(node => node.querySelector('time').dateTime === '2026-01-01');
check(visibleShare(januaryFirst.querySelector('.agent-trend-cache-share')) === '33.33%', 'Trend share did not use just this recorded bucket');
check(document.querySelectorAll('.metric strong')[2].textContent === '20.00%', 'Overall card share fixture changed');
check(visibleShare(document.querySelector('.model-cache-share')) === 'N/A', 'Output-only leading model borrowed bucket share');
check(document.querySelector('main').textContent.includes('2026-02-01 00:30') && document.querySelector('main').textContent.includes('Pacific/Kiritimati'), 'Agent snapshot context missing');
checkPageReachability();
check(window.scrollY > 0 && document.querySelector('.agent-trend-content li:last-child').textContent.includes('2026-02-28'), 'Last recorded day unreachable by page scroll');
check(![...document.querySelectorAll('.agent-panels time')].some(node => node.dateTime === '2026-02-15'), 'Gap fabricated');
check(document.querySelector('.agent-panels [data-state="zero"]').textContent.includes('Recorded zero'), 'Recorded zero not explicit');
check(document.querySelector('.agent-panels [data-state="future"]').textContent.includes('future recorded date'), 'Future classification lost');
for (const period of ['day', 'week', 'month']) {
  await choose('Period', period); checkScope(); checkLayout();
  await mode('Providers'); checkScope(); checkLayout();
  await choose('Provider', JSON.stringify('openai')); checkScope();
  await choose('Model', JSON.stringify('gpt-6-sol')); checkScope();
  await mode('Models'); checkScope();
  const button = document.querySelector('.model-toggle'); button.click(); await pause();
  check([...expanded().querySelectorAll('dd')].map(node => node.textContent).join('|') === '$2|$10|$5.0000', 'Agent pair cost/reference used latest day, parent or inferred charges');
  checkLayout();
  await choose('Provider', ''); check(!expanded(), 'Filter kept stale expansion'); checkScope();
}
for (const model of ['anthropic/claude-synthetic', 'deepseek/deepseek-synthetic']) {
  await choose('Model', JSON.stringify(model)); checkScope();
  const company = model.startsWith('anthropic') ? 'Anthropic' : 'DeepSeek';
  check(document.querySelector('[aria-label="Model totals"] .model-company').textContent === company && document.querySelector('[aria-label="Model totals"] tbody td').textContent === 'openai', 'Company guessed from direct provider');
  document.querySelector('.model-toggle').click(); await pause();
  check([...expanded().querySelectorAll('dd')].slice(0, 2).every(node => node.textContent === 'Unavailable'), 'Non-catalog model got fictional rates');
}
await choose('Provider', JSON.stringify('c-broker')); checkScope();
document.querySelector('.model-toggle').click(); await pause();
check([...expanded().querySelectorAll('dd')].map(node => node.textContent).join('|') === 'Unavailable|Unavailable|$7.0000', 'Same-name model pair borrowed direct-provider rates/cost');
await mode('Providers'); check(!expanded(), 'Provider mode kept model expansion'); checkScope();
await mode('Models'); check(!expanded(), 'Disclosure resurrected after switch');
await choose('Provider', JSON.stringify('')); checkScope();
check(document.querySelector('[aria-label="Model totals"] .model-company').textContent === 'Company unavailable', 'Blank labels inferred company');
await choose('Provider', JSON.stringify('zero')); checkScope();
check(!document.querySelector('.model-empty-slot'), 'Zero usage has filler');
await mode('Providers'); checkScope();
check(document.querySelector('#agent-ranking-content').textContent.includes('Recorded zero tokens'), 'Zero provider state wrong');
for (const [role, model, expected] of [['explore', 'gpt-6-sol', '47.50%'], ['verify', 'gpt-6-sol', '50.00%'], ['verify', 'output-only', 'N/A'], ['verify', 'zero', 'N/A'], ['worker-extra', 'gpt-6-sol', '0.00%']]) {
  window.location.hash = '#/agents/' + role; await pause();
  checkScope(); checkLayout();
  await choose('Model', JSON.stringify(model));
  for (const period of ['day', 'week', 'month']) {
    await choose('Period', period); checkScope(); checkLayout();
    check(visibleShare(document.querySelector('.model-cache-share')) === expected, 'Cache-read share mixed roles or interpreted granularity as range');
    await mode('Providers'); checkScope(); await mode('Models'); checkScope();
  }
  await choose('Provider', JSON.stringify('openai')); checkScope();
}
window.location.hash = '#/agents/unused'; await pause();
check(!document.querySelector('.agent-panels li, .model-empty-slot'), 'Unused role fabricated history or models');
check(document.querySelector('.agent-panels').textContent.includes('No recorded dates'), 'Unused role missing state');
checkLayout();
""", viewport=viewport, browser_timezone="America/Los_Angeles", virtual_time=20000)

    def test_file_agent_company_conflicts_are_disclosed_without_inference(self):
        data = agent_fixture()
        data["rows"] = [dict(data["rows"][0], model="gpt-6-sol"),
                        dict(data["rows"][0], model="gpt-6-sol", day="2026-02-01")]
        document = render_react_dashboard(data, DIST)
        wire = payload(document)
        # Defensive browser boundary: a future wire may carry contradictory
        # Python-owned enum values for a pair. Never choose the first one.
        wire["rows"][0]["company"] = "Anthropic"
        wire["rows"][1]["company"] = None
        document = re.sub(r'(<script type="application/json" id="usage-data">).*?(</script>)',
                          lambda match: match[1] + json.dumps(wire) + match[2], document, flags=re.S)
        self.browser(document, AGENT_PANEL_CHECKS + r"""
checkScope();
const label = document.querySelector('[aria-label="Model totals"] .model-company').textContent;
check(label === 'Company unavailable/mixed', 'Contradictory company classifications arbitrarily resolved');
check(!label.includes('OpenAI'), 'Provider inferred a replacement company');
""")

    def test_file_agent_detail_header_hidden_context_and_route_regressions(self):
        data = agent_fixture()
        data["stats"].update(asOf="2026-06-03T00:15:00+14:00", timezone="Pacific/Kiritimati")
        document = render_react_dashboard(data, DIST)
        self.assertNotIn("PRIVATE_", document)
        for viewport in ("1280,900", "390,844", "320,844"):
            with self.subTest(viewport=viewport):
                self.browser(document, r"""
const visit = async hash => { location.hash = hash; await pause(); };
// innerText alone includes clipped offscreen text; collect only painted text.
const visibleText = node => {
  if (node.nodeType === Node.TEXT_NODE) return node.textContent;
  if (node.nodeType !== Node.ELEMENT_NODE) return '';
  const style = getComputedStyle(node);
  if (style.display === 'none' || style.visibility === 'hidden' || style.clipPath === 'inset(50%)') return '';
  return [...node.childNodes].map(visibleText).join(' ').replace(/\s+/g, ' ').trim();
};
const globalContext = () => {
  check(document.querySelector('.quality') && document.querySelector('footer'), 'Agent/Company quality or footer removed');
  const footer = visibleText(document.querySelector('footer'));
  check(footer.includes('2026-06-03T00:15:00+14:00') && footer.includes('Pacific/Kiritimati'), 'Global snapshot context changed');
};
const noDanglingDescriptions = () => {
  for (const node of document.querySelectorAll('[aria-describedby]')) {
    for (const id of node.getAttribute('aria-describedby').split(/\s+/)) {
      check([...document.querySelectorAll('[id]')].filter(target => target.id === id).length === 1, 'Dangling or duplicate description: ' + id);
    }
  }
};
for (const role of ['worker', 'unused', 'verify #β%']) {
  await visit('#/agents/' + encodeURIComponent(role));
  const view = document.querySelector('.agents-view'), heading = view.querySelector('h1');
  const first = 'Actual usage-event providers and models for this exact role, not task launch metadata or task token totals.';
  const limitation = 'Historical child attribution may be incomplete; parent, SDD, and unlinked usage are excluded.';
  const painted = visibleText(view);
  check(!painted.includes(first) && !painted.includes('Snapshot as of') && !painted.includes(limitation), 'Removed header paragraphs remain visible');
  check(heading.textContent === role && visibleText(heading) === role, 'Role title hidden or changed');
  check(view.querySelector('.agent-back').getAttribute('href') === '#/agents' && visibleText(view.querySelector('.agent-back')).includes('Back to Agents'), 'Back anchor removed');
  check(visibleText(view.querySelector('.page-heading .rt-Badge')) === 'Linked children only', 'Scope badge hidden or changed');
  const context = document.getElementById(heading.getAttribute('aria-describedby'));
  check(!!context && context.textContent.includes(first) && context.textContent.includes(limitation), 'Exact-role or attribution context lost');
  check(context.textContent.includes('Snapshot as of 2026-06-03 00:15') && context.textContent.includes('Timezone: Pacific/Kiritimati'), 'As-of or timezone converted to browser clock');
  check(context.querySelector('time').dateTime === '2026-06-03T00:15:00+14:00', 'Machine-readable timestamp offset lost');
  const style = getComputedStyle(context), box = context.getBoundingClientRect();
  check(style.position === 'absolute' && style.clipPath === 'inset(50%)' && box.width <= 1 && box.height <= 1, 'Context still occupies visible header space');
  check(style.display !== 'none' && style.visibility === 'visible' && !context.closest('[hidden], [aria-hidden="true"]'), 'Context hidden from assistive technology');
  check(view.querySelector('.usage-filters') && view.querySelector('.metrics') && view.querySelector('.agent-panels') && view.querySelector('[aria-label="Model totals"]') && view.querySelector('[aria-label="Provider totals"]'), 'Detail controls/cards/panels/tables removed');
  if (role === 'worker') check(view.querySelector('.metric strong').textContent === '85', 'Exact-role total changed');
  const header = view.querySelector('.page-heading').getBoundingClientRect();
  for (const node of [heading, view.querySelector('.agent-back'), view.querySelector('.page-heading .rt-Badge')]) {
    const rect = node.getBoundingClientRect();
    check(rect.width > 1 && rect.height > 1 && rect.left >= 0 && rect.right <= innerWidth, 'Header content clipped horizontally');
  }
  check(header.right <= innerWidth && document.documentElement.scrollWidth <= innerWidth, 'Responsive header overflows');
  globalContext(); noDanglingDescriptions();
}
document.querySelector('.agent-back').click(); await pause();
check(document.querySelector('h1').textContent === 'Agents' && document.querySelectorAll('.agent-card').length === 4, 'Agent index/back navigation changed');
check(visibleText(document.querySelector('.agents-view')).includes('Safely linked child usage only. Parent, SDD, and unlinked child usage are excluded. Historical attribution may be incomplete.'), 'Agent index visible scope removed');
check(!document.getElementById('agent-detail-context'), 'Detail description leaked to index');
globalContext(); noDanglingDescriptions();
await visit('#/companies');
check(document.querySelectorAll('.company-card').length === 5 && visibleText(document.querySelector('.companies-view')).includes('Parent + linked non-SDD child usage.'), 'Company index changed');
globalContext();
await visit('#/companies/OpenAI');
check(visibleText(document.querySelector('.page-heading')).includes("Models attributed by Python classification, not the provider's display name."), 'Company detail explanation removed');
check(document.querySelector('.usage-buckets') && document.querySelector('.metric strong').textContent === '3,082', 'Company detail scope changed');
globalContext();
for (const route of ['#/overview', '#/sessions', '#/sessions/2026-02-02']) {
  await visit(route);
  const header = visibleText(document.querySelector('.page-heading'));
  check(header.includes('2026-06-03 00:15') && header.includes('Pacific/Kiritimati'), 'Other route lost visible snapshot cue');
  check(!document.querySelector('.quality, footer'), 'Overview/Sessions quality/footer regression');
  check(!document.getElementById('agent-detail-context'), 'Agent description leaked to another route');
  noDanglingDescriptions();
}
""", viewport=viewport, browser_timezone="America/Los_Angeles")

    def test_file_agents_exact_role_event_models_and_periods(self):
        self.browser(render_react_dashboard(agent_fixture(), DIST), r"""
window.location.hash = '#/agents'; await pause();
const cards = [...document.querySelectorAll('.agent-card')];
check(cards.map(node => node.querySelector('h2').textContent).join(',') === 'worker,worker-extra,unused,verify #β%', 'Not all configured roles shown');
check(cards[0].querySelector('strong').textContent === '85 tokens', 'Index included parent or similar role');
check(cards[2].textContent.includes('0 tokens') && cards[2].textContent.includes('N/A') && cards[2].textContent.includes('No measured usage'), 'Unused role not honest');
check(document.querySelector('.rt-Card') && document.querySelector('.rt-Badge'), 'Radix agent cards missing');
cards[0].querySelector('a').click(); await pause();
check(document.querySelector('h1').textContent === 'worker', 'Agent detail missing');
check(document.querySelector('nav [aria-current="page"]').textContent === 'Agents', 'Agent detail nav not active');
check(!document.querySelector('.agent-grid'), 'Index leaked into detail');
const metrics = () => [...document.querySelectorAll('.metric strong')].map(node => node.textContent);
check(metrics().join(',') === '85,15,42.86%,$0.8750', 'Role metrics inferred or mixed');
const tableRows = title => [...document.querySelectorAll(`[aria-label="${title}"] tbody tr`)].map(row => [...row.cells].map(cell => cell.textContent));
check(JSON.stringify(tableRows('Model totals')) === JSON.stringify([
  ['broker', 'claude-event', 'Anthropic', '3', '0', '3', '0', '0', 'N/A', '$0.5000'],
  ['broker', 'gpt-event', 'OpenAI', '27', '20', '7', '0', '0', '0.00%', '$0.2500'],
  ['openai', 'gpt-event', 'OpenAI', '55', '10', '5', '30', '10', '60.00%', '$0.1250']
]), 'Actual model/provider accounting incorrect');
check(JSON.stringify(tableRows('Provider totals')) === JSON.stringify([
  ['broker', '30', '20', '10', '0', '0', '0.00%', '$0.7500'],
  ['openai', '55', '10', '5', '30', '10', '60.00%', '$0.1250']
]), 'Provider accounting incorrect');
check(document.querySelector('caption').textContent.includes('Selected role'), 'Company caption leaked into Agents');
check(!document.querySelector('.usage-buckets') && !document.querySelector('main').textContent.includes('Tokens by date'), 'Old agent date list remains');
const buckets = () => [...document.querySelectorAll('.agent-panels .trend > li[data-recorded="true"]')].map(node => node.textContent);
check(buckets().length === 3, 'Daily role trend missing');
const period = document.querySelector('.usage-filters select');
period.value = 'week'; period.dispatchEvent(new Event('change', {bubbles: true})); await pause();
check(buckets().length === 2 && buckets()[0].includes('Week of 2026-01-26') && buckets()[0].includes('82 tokens') && buckets()[1].includes('Week of 2026-02-02') && buckets()[1].includes('3 tokens'), 'Role weeks do not start Monday');
period.value = 'month'; period.dispatchEvent(new Event('change', {bubbles: true})); await pause();
check(buckets().length === 2 && buckets()[0].includes('2026-01') && buckets()[0].includes('55 tokens') && buckets()[1].includes('2026-02') && buckets()[1].includes('30 tokens'), 'Role month boundary wrong');
""")

    def test_file_agents_scoped_filters_routes_and_isolation(self):
        self.browser(render_react_dashboard(agent_fixture(), DIST), r"""
const visit = async hash => { window.location.hash = hash; await pause(); };
const select = label => [...document.querySelectorAll('.usage-filters label')].find(node => node.firstChild.textContent === label)?.querySelector('select');
const choose = async (label, value) => {
  const node = select(label); node.value = value;
  node.dispatchEvent(new Event('change', {bubbles: true})); await pause();
};
const total = () => document.querySelector('.metric strong')?.textContent;
const clean = () => select('Period').value === 'day' && select('Provider').value === '' && select('Model').value === '';
await visit('#/agents/worker');
check(select('Provider').options.length === 3 && select('Model').options.length === 3, 'Agent options not scoped');
check(document.querySelector('.usage-filters button').disabled, 'Initial reset not disabled');
await choose('Model', JSON.stringify('gpt-event'));
check(total() === '82', 'Same model across providers not counted');
await choose('Provider', JSON.stringify('openai'));
check(total() === '55' && select('Model').value === '' && select('Model').options.length === 2, 'Provider did not reset and scope models');
await choose('Provider', JSON.stringify('broker'));
await choose('Model', JSON.stringify('claude-event'));
check(total() === '3' && document.querySelectorAll('.metric strong')[2].textContent === 'N/A', 'Output-only role usage incorrect');
await choose('Period', 'week');
const reset = document.querySelector('.usage-filters button');
check(reset.classList.contains('rt-Button') && !reset.disabled, 'Radix reset missing');
reset.focus(); check(document.activeElement === reset, 'Reset not focusable');
reset.click(); await pause();
check(total() === '85' && clean(), 'Agent reset failed');
await choose('Provider', JSON.stringify('broker'));
await choose('Period', 'month');
await visit('#/agents/worker-extra');
check(total() === '2,000' && clean(), 'Filters bled between exact roles');
await visit('#/agents/unused');
check(total() === '0' && clean() && document.querySelectorAll('.metric strong')[2].textContent === 'N/A', 'Unused detail not zero/N/A');
check(!document.querySelector('.agent-panels .trend li, .models li, .model-empty-slot') && !document.querySelector('tbody tr'), 'Unused role inferred activity');
check(document.querySelector('main').textContent.includes('No measured usage for unused'), 'Unused role explanation missing');
document.querySelector('.agent-back').click(); await pause();
check(document.querySelector('h1').textContent === 'Agents', 'Agent back link failed');
const encoded = '#/agents/' + encodeURIComponent('verify #β%');
const link = [...document.querySelectorAll('.agent-card h2 a')].find(node => node.textContent === 'verify #β%');
check(link.getAttribute('href') === encoded, 'Role route not encoded');
link.click(); await pause();
check(document.querySelector('h1').textContent === 'verify #β%' && total() === '4', 'Encoded role not rendered');
check(select('Provider').options[1].value === '""' && select('Model').options[1].value === '""', 'Unspecified labels confused with all');
await choose('Provider', '""'); await choose('Model', '""');
check(total() === '4', 'Unspecified label filter failed');
await visit('#/agents/worker');
await choose('Model', JSON.stringify('claude-event'));
await visit('#/companies/OpenAI');
check(total() === '3,082' && clean(), 'Agent filters bled into Companies');
check(document.querySelector('.usage-buckets h2').textContent === 'Tokens by date' && !document.querySelector('.agent-panels'), 'Company detail was redesigned with Agents');
check(!document.querySelector('[aria-label="Model totals"] .model-company') && document.querySelectorAll('[aria-label="Model totals"] thead th').length === 9, 'Agent Company column leaked into Company detail');
await choose('Provider', JSON.stringify('broker'));
check(total() === '27', 'Company filter broken by extraction');
await visit('#/agents/worker');
check(total() === '85' && clean(), 'Company filters bled into Agents');
await choose('Provider', JSON.stringify('broker'));
document.querySelector('.agent-back').click(); await pause();
check(document.querySelector('.agent-card strong').textContent === '85 tokens', 'Agent filter bled into index');
await visit('#/overview');
await choose('Period', 'all');
check(total() === '3,089', 'Agent filter bled into Overview');
await visit('#/agents/worker');
check(total() === '85' && clean(), 'Agent filters did not reset after Overview');
for (const hash of ['#/agents/unknown', '#/agents/sdd-apply', '#/agents/%E0%A4%A', '#/agents/worker/extra']) {
  await visit(hash);
  check(document.querySelector('h1').textContent === 'View not found', 'Invalid agent view accepted: ' + hash);
  check(!document.querySelector('.metrics') && !document.querySelector('nav [aria-current]'), 'Invalid view retained selected role');
}
""")

    def test_file_agents_empty_configuration_and_bigint_usage(self):
        data = fixture()
        data["roles"] = []
        data["rows"] = []
        self.browser(render_react_dashboard(data, DIST), r"""
window.location.hash = '#/agents'; await pause();
check(document.querySelector('h1').textContent === 'Agents', 'Empty Agents route missing');
check(!document.querySelector('.agent-card'), 'Empty configuration invented roles');
check(document.querySelector('main').textContent.includes('No configured non-SDD roles'), 'Empty configuration not explained');
""")
        data = fixture()
        data["rows"][0]["role"] = "worker"
        self.browser(render_react_dashboard(data, DIST), r"""
window.location.hash = '#/agents/worker'; await pause();
check(document.querySelector('.metric strong').textContent === '9,007,199,254,741,013', 'Agent BigInt precision lost');
check(document.querySelector('[aria-label="Model totals"]').textContent.includes('9,007,199,254,740,993'), 'Agent breakdown precision lost');
check(document.querySelector('[aria-label="Model totals"]').textContent.includes('</script><img src=x onerror=alert(1)>'), 'Hostile model not preserved as text');
check(!document.querySelector('img'), 'Agent model became markup');
""")

    def test_file_company_empty_slots_and_large_metrics(self):
        data = fixture()
        data["rows"][0]["provider"] = "openai"
        data["rows"][0]["model"] = "gpt-synthetic"
        self.browser(render_react_dashboard(data, DIST), r"""
window.location.hash = '#/companies'; await pause();
check(document.querySelectorAll('.company-card').length === 5, 'Empty slots disappeared');
const cards = [...document.querySelectorAll('.company-card')];
check(cards.slice(2).every(node => !node.querySelector('a') && node.textContent.includes('No additional company with measured token usage')), 'Empty slot inferred a company');
cards[0].querySelector('a').click(); await pause();
check(document.querySelector('.metric strong')?.textContent === '9,007,199,254,741,002', 'Company BigInt precision lost');
check(document.querySelector('[aria-label="Model totals"]').textContent.includes('9,007,199,254,740,993'), 'Breakdown BigInt precision lost');
""")

    def test_file_company_filters_distinguish_empty_labels_from_all(self):
        data = company_fixture()
        data["rows"][0]["model"] = ""  # Direct provider alias still classifies this row.
        data["rows"][1]["provider"] = ""
        self.browser(render_react_dashboard(data, DIST), r"""
window.location.hash = '#/companies/OpenAI'; await pause();
const selects = document.querySelectorAll('.usage-filters select');
const provider = selects[1];
const model = selects[2];
check(provider.options[0].textContent === 'All providers', 'All-provider option missing');
check([...provider.options].some(option => option.value === '""' && option.textContent.includes('unspecified')), 'Empty provider confused with all');
provider.value = '""'; provider.dispatchEvent(new Event('change', {bubbles: true})); await pause();
check(document.querySelector('.metric strong')?.textContent === '27', 'Empty-provider filter failed');
provider.value = JSON.stringify('openai'); provider.dispatchEvent(new Event('change', {bubbles: true})); await pause();
model.value = '""'; model.dispatchEvent(new Event('change', {bubbles: true})); await pause();
check(document.querySelector('.metric strong')?.textContent === '55', 'Empty-model filter failed');
check(model.value === '""' && model.options[1].textContent.includes('unspecified'), 'Empty model confused with all');
""")

    def test_file_runtime_boundary_rejects_company_contract_mutations(self):
        document = render_react_dashboard(fixture(), DIST)
        cases = [
            ('"version": 5', '"version": 2'),
            ('"version": 5', '"version": 1'),
            ('"company": null', '"company": "Unknown"'),
            ('"company": null', '"company": 42'),
            ('"company": null', '"missingCompany": null'),
            ('"companySlots":', '"missingSlots":'),
            ('"companySlots": ["OpenAI", "DeepSeek", null, null, null]',
             '"companySlots": ["DeepSeek", "OpenAI", null, null, null]'),
            ('"companySlots": ["OpenAI", "DeepSeek", null, null, null]',
             '"companySlots": ["OpenAI", "DeepSeek", "OpenAI", null, null]'),
            ('"companySlots": ["OpenAI", "DeepSeek", null, null, null]',
             '"companySlots": ["OpenAI", "DeepSeek", null, "Google", null]'),
            ('"companySlots": ["OpenAI", "DeepSeek", null, null, null]',
             '"companySlots": ["OpenAI", "DeepSeek", null, null]'),
            ('"companySlots": ["OpenAI", "DeepSeek", null, null, null]',
             '"companySlots": ["OpenAI", "DeepSeek", "Unknown", null, null]'),
        ]
        for old, new in cases:
            with self.subTest(mutation=new):
                self.assertIn(old, document)
                self.browser(document.replace(old, new), r"""
check(document.querySelector('[role="alert"]')?.textContent.includes('could not be read safely'), 'Invalid company contract accepted');
check(!document.querySelector('.company-card'), 'Invalid company snapshot rendered');
""")

    def test_file_runtime_boundary_rejects_session_contract_mutations(self):
        document = render_react_dashboard(session_fixture(), DIST)
        source = payload(document)
        for label, mutated in invalid_session_cases(source, wire=True):
            with self.subTest(mutation=label):
                encoded = json.dumps(mutated).replace("<", "\\u003c").replace(">", "\\u003e")
                changed = re.sub(
                    r'(<script type="application/json" id="usage-data">).*?(</script>)',
                    lambda match: match[1] + encoded + match[2], document, count=1, flags=re.S)
                self.browser(changed, r"""
check(document.querySelector('[role="alert"]')?.textContent.includes('could not be read safely'), 'Invalid session contract accepted');
check(!document.querySelector('.metrics'), 'Invalid session snapshot rendered');
""")
        # Valid JSON may still decode a number that is not finite in JavaScript.
        # Change only a summary metric so the rows cannot mask this rejection.
        source["sessions"][0]["days"][0]["models"][0]["cost"] = "OVERFLOW"
        encoded = json.dumps(source).replace('"OVERFLOW"', '1e999').replace("<", "\\u003c")
        changed = re.sub(r'(<script type="application/json" id="usage-data">).*?(</script>)',
                         lambda match: match[1] + encoded + match[2], document, count=1, flags=re.S)
        self.browser(changed, r"""
check(document.querySelector('[role="alert"]')?.textContent.includes('could not be read safely'), 'Infinite cost accepted');
""")

    @unittest.skipUnless(shutil.which("node"), "Node unavailable; direct parser proof pending")
    def test_file_parser_rebuilds_private_nested_shapes_and_preserves_bigint(self):
        # Transpile the exact boundary module in memory; do not expose a test API
        # from the production bundle or write another build entry.
        compiled = subprocess.run([shutil.which("node"), "--input-type=module", "-e", """
import ts from 'typescript';
import { readFileSync } from 'node:fs';
const source = readFileSync('src/data.ts', 'utf8');
process.stdout.write(ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS }
}).outputText);
"""], cwd=ROOT / "web", capture_output=True, text=True, timeout=20)
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        parser = "const parse = (() => { const exports = {};\n" + compiled.stdout + "\nreturn exports.parseSnapshot; })();\n"
        self.browser(render_react_dashboard(session_fixture(), DIST), parser + r"""
const source = JSON.parse(document.getElementById('usage-data').textContent);
const session = source.sessions[0], day = session.days[0], model = day.models[0];
for (const target of [source, session, day, model]) {
  for (const key of ['id', 'sessionPath', 'cwd', 'title', 'prompts', 'messages', 'threads', 'task', 'stableKey']) {
    target[key] = 'PRIVATE_SENTINEL';
  }
}
for (const target of [source, day, model, source.stats, source.stats.coverage, ...source.rows]) {
  target.sessionId = 'PRIVATE_SENTINEL';
}
const parsed = parse(source), summary = parsed.sessions[0];
check(summary !== session && summary.days !== session.days && summary.days[0] !== day && summary.days[0].models[0] !== model, 'Source objects reused');
check(summary.days[0].models[0].input === 9007199254740993n, 'Session BigInt precision lost');
check(Object.keys(summary).sort().join(',') === 'days,endDay,name,role,sessionId,startDay', 'Session allowlist incomplete');
check(Object.keys(summary.days[0]).sort().join(',') === 'day,models', 'Day allowlist incomplete');
check(Object.keys(summary.days[0].models[0]).sort().join(',') === 'cacheRead,cacheWrite,cost,input,model,output,provider', 'Model allowlist incomplete');
check(!JSON.stringify(parsed, (_, value) => typeof value === 'bigint' ? String(value) : value).includes('PRIVATE_SENTINEL'), 'Private extra exported');
check(source.sessions[0].id === 'PRIVATE_SENTINEL', 'Parser mutated source');
check(parsed.sessions[1].role === 'worker' && summary.role === null, 'Session owners changed');
check(summary.days[1].models[0].input === 0n, 'Valid zero event omitted');
check(summary.days[0].models[0].model === '</script><img src=x onerror=alert(1)>', 'Hostile model changed');
const invalid = value => { try { parse(value); return false; } catch { return true; } };
for (const value of [NaN, Infinity, -Infinity]) {
  const bad = structuredClone(source); bad.sessions[0].days[0].models[0].cost = value;
  check(invalid(bad), 'Nonfinite session cost accepted');
}
for (const version of [1, 2, 6, '3', '4', '5', null]) check(invalid({...source, version}), 'Invalid version accepted');
for (const name of ['INJECTED_V3_NAME', 42, {prompt: 'PRIVATE_SENTINEL'}]) {
  const legacy = structuredClone(source); legacy.version = 3; legacy.sessions[0].name = name;
  check(parse(legacy).sessions.every(session => session.name === null), 'Injected v3 name trusted');
}
for (const version of [3, 4]) {
  for (const id of ['INJECTED_ID', null, 42, {prompt: 'PRIVATE_SENTINEL'}]) {
    const older = structuredClone(source); older.version = version;
    for (const session of older.sessions) session.sessionId = id;
    check(parse(older).sessions.every(session => session.sessionId === null), 'Older injected ID trusted');
  }
  const older = structuredClone(source); older.version = version;
  for (const session of older.sessions) delete session.sessionId;
  check(parse(older).sessions.every(session => session.sessionId === null), 'Older missing ID rejected');
}
for (const id of [null, 'a', '0', 'Z._-09', 'A'.repeat(128)]) {
  const valid = structuredClone(source); valid.sessions[0].sessionId = id;
  check(parse(valid).sessions[0].sessionId === id, 'Valid ID changed or rejected');
}
for (const value of [NaN, Infinity, undefined]) {
  const bad = structuredClone(source); bad.sessions[0].sessionId = value;
  check(invalid(bad), 'Invalid ID type accepted');
}
const legacy = structuredClone(source); legacy.version = 3;
for (const session of legacy.sessions) delete session.name;
check(parse(legacy).sessions.every(session => session.name === null), 'Anonymous v3 compatibility lost');
const named = structuredClone(source); named.sessions[0].name = 'Explicit synthetic Pi name';
named.sessions[1].name = 'INJECTED_CHILD_NAME';
for (const version of [4, 5]) {
  check(parse({...named, version}).sessions[0].name === 'Explicit synthetic Pi name' && parse({...named, version}).sessions[1].name === null, 'Parent name or neutral child boundary lost');
}
check(parse({...source, sessions: []}).sessions.length === 0, 'Empty session list rejected');
const leap = structuredClone(source);
leap.sessions = [{startDay: '2024-02-29', endDay: '2024-02-29', role: null, name: null, sessionId: null,
  days: [{day: '2024-02-29', models: [model, {...model, provider: 'other-synthetic'}]}]}];
check(parse(leap).sessions[0].days[0].models.length === 2, 'Leap day or multiple models rejected');
check(document.querySelector('h1')?.textContent === 'Overview', 'Valid sessions blocked existing view');
""")

    def test_file_runtime_boundary_rejects_numeric_tokens(self):
        document = render_react_dashboard(fixture(), DIST)
        document = document.replace('"input": "9007199254740993"', '"input": 9007199254740993')
        self.browser(document, r"""
check(document.querySelector('[role="alert"]')?.textContent.includes('could not be read safely'), 'Unsafe data was accepted');
check(!document.querySelector('.metrics'), 'Invalid snapshot rendered metrics');
""")

    def test_file_runtime_boundary_rejects_unconfigured_role(self):
        document = render_react_dashboard(fixture(), DIST).replace('"role": "worker"', '"role": "unknown"')
        self.browser(document, r"""
check(document.querySelector('[role="alert"]')?.textContent.includes('could not be read safely'), 'Unknown role was accepted');
""")


if __name__ == "__main__":
    unittest.main()
