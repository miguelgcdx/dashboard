"""Generate a private, offline usage dashboard from aggregated Pi session metadata."""

import argparse
import base64
import hashlib
from datetime import date, datetime
import json
import math
import os
import re
from pathlib import Path
import stat
import tempfile

from codex_usage import collect_codex_usage
from pi_usage import (
    collect_combined_usage,
    collect_usage,
    sanitize_session_name,
    validated_session_id,
)


DEFAULT_SESSIONS = Path(
    os.environ.get("PI_CODING_AGENT_SESSION_DIR") or "~/.pi/agent/sessions"
).expanduser()
DEFAULT_OUTPUT = Path("~/.local/share/pi-usage-dashboard/index.html").expanduser()

# Match complete namespaces and anchored model families, not substrings of
# unrelated names (e.g. "not-gpt-5"). Brokers are not company aliases.
_MODEL_NAMESPACES = {
    "openai": "OpenAI",
    "deepseek": "DeepSeek",
    "anthropic": "Anthropic",
    "google": "Google",
    "google-ai": "Google",
    "xai": "xAI",
    "x-ai": "xAI",
    "moonshotai": "Moonshot",
    "moonshot": "Moonshot",
    "qwen": "Alibaba",
    "alibaba": "Alibaba",
    "meta": "Meta",
    "mistralai": "Mistral",
    "mistral": "Mistral",
    "zai-org": "Z.ai",
    "z-ai": "Z.ai",
}
_MODEL_FAMILIES = (
    (re.compile(r"(?:gpt-|chatgpt-|o[134](?:-|$))"), "OpenAI"),
    (re.compile(r"deepseek(?:-|$)"), "DeepSeek"),
    (re.compile(r"claude(?:-|$)"), "Anthropic"),
    (re.compile(r"gemini(?:-|$)"), "Google"),
    (re.compile(r"grok(?:-|$)"), "xAI"),
    (re.compile(r"kimi(?:-|$)"), "Moonshot"),
    (re.compile(r"qwen(?:-|[0-9]+(?:[.-]|$))"), "Alibaba"),
    (re.compile(r"llama(?:-|[0-9]+(?:[.-]|$))"), "Meta"),
    (re.compile(r"mistral(?:-|$)"), "Mistral"),
)
_PROVIDER_ALIASES = {
    "openai": "OpenAI",
    "openai-codex": "OpenAI",
    "deepseek": "DeepSeek",
    "anthropic": "Anthropic",
    "google": "Google",
    "google-gemini": "Google",
    "xai": "xAI",
    "x-ai": "xAI",
    "moonshot": "Moonshot",
    "moonshotai": "Moonshot",
    "qwen": "Alibaba",
    "alibaba": "Alibaba",
    "meta": "Meta",
    "mistral": "Mistral",
    "mistralai": "Mistral",
}
_TOKEN_KEYS = ("input", "output", "cacheRead", "cacheWrite")


def company_for(provider, model):
    """Classify a model before trying a direct provider alias; return None if unknown."""
    name = model.strip().lower()
    if name.startswith("openrouter/"):
        name = name[len("openrouter/") :]
    namespace, separator, family = name.partition("/")
    if separator:
        if namespace in _MODEL_NAMESPACES:
            return _MODEL_NAMESPACES[namespace]
        # An unknown namespace is explicit evidence against provider attribution.
        return None
    else:
        for pattern, company in _MODEL_FAMILIES:
            if pattern.match(name):
                return company
    return _PROVIDER_ALIASES.get(provider.strip().lower())


def company_slots(rows):
    """Five fixed/volume-ranked slots; None denotes an honest empty slot."""
    volumes = {}
    for row in rows:
        company = company_for(row["provider"], row["model"])
        if company and company not in ("OpenAI", "DeepSeek"):
            volumes[company] = volumes.get(company, 0) + sum(
                int(row[key]) for key in _TOKEN_KEYS
            )
    ranked = sorted(
        (name for name, volume in volumes.items() if volume > 0),
        key=lambda name: (-volumes[name], name),
    )
    return ["OpenAI", "DeepSeek", *(ranked[:3] + [None] * (3 - len(ranked[:3])))]


# All dynamic text is assigned through textContent. There are no network assets.
SCRIPT = r"""
'use strict';
const data = JSON.parse(document.getElementById('usage-data').textContent);
const rows = data.rows;
const stats = data.stats;
const companySlots = data.companySlots;
const metrics = ['input', 'output', 'cacheRead', 'cacheWrite'];
const fmt = value => value.toLocaleString();
const dollars = value => '$' + value.toFixed(4);
const share = totals => {
  const denominator = totals.input + totals.cacheRead + totals.cacheWrite;
  if (denominator === 0n) return 'N/A';
  const hundredths = (totals.cacheRead * 10000n + denominator / 2n) / denominator;
  return (Number(hundredths) / 100).toFixed(2) + '%';
};
const text = (tag, value, className) => {
  const node = document.createElement(tag);
  node.textContent = value;
  if (className) node.className = className;
  return node;
};
const empty = () => ({input: 0n, output: 0n, cacheRead: 0n, cacheWrite: 0n, cost: 0});
function accumulate(target, row) {
  for (const key of metrics) target[key] += BigInt(row[key]);
  target.cost += row.cost;
}
function periodStart(day, period) {
  if (period === 'month') return day.slice(0, 7);
  if (period === 'day') return day;
  const date = new Date(day + 'T00:00:00Z');
  const weekday = (date.getUTCDay() + 6) % 7;
  date.setUTCDate(date.getUTCDate() - weekday);
  return date.toISOString().slice(0, 10);
}
function option(select, value, label) {
  const node = document.createElement('option');
  node.value = value;
  node.textContent = label;
  select.append(node);
}
const provider = document.getElementById('provider');
const model = document.getElementById('model');
const period = document.getElementById('period');
const providers = [...new Set(rows.map(row => row.provider))].sort();
option(provider, '', 'All providers');
for (const name of providers) option(provider, name, name || '(unspecified)');
function updateModels() {
  const selected = model.selectedIndex > 0 ? model.value : null;
  model.replaceChildren();
  option(model, '', 'All models');
  const names = [...new Set(rows.filter(row => provider.selectedIndex === 0 || row.provider === provider.value)
                                  .map(row => row.model))].sort();
  for (const name of names) option(model, name, name || '(unspecified)');
  if (selected !== null && names.includes(selected)) model.selectedIndex = names.indexOf(selected) + 1;
}
const volume = value => value.input + value.output + value.cacheRead + value.cacheWrite;
function renderBars(bars, dates, missing) {
  bars.replaceChildren();
  const entries = [...dates].sort(([a], [b]) => a.localeCompare(b));
  const max = entries.reduce((highest, [, value]) =>
    volume(value) > highest ? volume(value) : highest, 0n);
  for (const [day, value] of entries) {
    const line = text('li', '', 'bar-row');
    const amount = volume(value);
    const label = period.value === 'week' ? 'Week of ' + day : day;
    const track = text('div', '', 'track');
    const fill = text('div', '', 'fill');
    fill.style.width = max === 0n ? '0%' : Number(amount * 100n / max) + '%';
    track.append(fill);
    line.append(text('span', label), track, text('span', fmt(amount) + ' tokens'));
    line.setAttribute('aria-label', label + ': ' + fmt(amount) + ' tokens');
    bars.append(line);
  }
  if (!entries.length) bars.append(text('li', missing));
}
function renderCompanies() {
  const container = document.getElementById('companies');
  container.replaceChildren();
  for (let index = 0; index < companySlots.length; index++) {
    const company = companySlots[index];
    const section = text('section', '', 'company-section');
    section.append(text('h3', company || 'Unfilled company slot ' + (index - 1)));
    if (company === null) {
      section.append(text('p', 'No additional company with measured token usage for this slot.'));
      container.append(section);
      continue;
    }
    const totals = empty();
    const dates = new Map();
    const models = new Map();
    for (const row of rows) {
      if (row.company !== company) continue;
      accumulate(totals, row);
      const date = periodStart(row.day, period.value);
      if (!dates.has(date)) dates.set(date, empty());
      accumulate(dates.get(date), row);
      if (!models.has(row.model)) models.set(row.model, empty());
      accumulate(models.get(row.model), row);
    }
    const overview = text('p', 'Total: ' + fmt(volume(totals)) + ' tokens · Cache-read share: ' + share(totals));
    section.append(overview);
    if (volume(totals) === 0n) section.append(text('p', 'No measured usage for ' + company + '.'));
    section.append(text('h4', 'Tokens by date'));
    const bars = text('ul', '', 'bars');
    renderBars(bars, dates, 'No usage for ' + company + '.');
    section.append(bars, text('h4', 'Model breakdown'));
    const wrap = text('div', '', 'table-wrap');
    const table = document.createElement('table');
    const head = document.createElement('thead');
    const heading = document.createElement('tr');
    for (const label of ['Model', 'Total tokens', 'Cache-read share', 'Cost']) {
      const th = text('th', label);
      th.scope = 'col';
      heading.append(th);
    }
    head.append(heading);
    table.append(head);
    const body = document.createElement('tbody');
    for (const [name, value] of [...models].sort(([a], [b]) => a.localeCompare(b))) {
      const tr = document.createElement('tr');
      for (const cell of [name || '(unspecified)', fmt(volume(value)), share(value), dollars(value.cost)]) {
        tr.append(text('td', cell));
      }
      body.append(tr);
    }
    if (!models.size) {
      const tr = document.createElement('tr');
      const td = text('td', 'No models for ' + company + '.');
      td.colSpan = 4;
      tr.append(td);
      body.append(tr);
    }
    table.append(body);
    wrap.append(table);
    section.append(wrap);
    container.append(section);
  }
}
function render() {
  const selected = rows.filter(row => (provider.selectedIndex === 0 || row.provider === provider.value) &&
                                     (model.selectedIndex === 0 || row.model === model.value));
  const totals = empty();
  const dates = new Map();
  const models = new Map();
  for (const row of selected) {
    accumulate(totals, row);
    const key = periodStart(row.day, period.value);
    if (!dates.has(key)) dates.set(key, empty());
    accumulate(dates.get(key), row);
    const identity = JSON.stringify([row.provider, row.model]);
    if (!models.has(identity)) models.set(identity, empty());
    accumulate(models.get(identity), row);
  }
  const summary = document.getElementById('summary');
  summary.replaceChildren();
  for (const [label, value] of [
    ['Input', fmt(totals.input)], ['Output', fmt(totals.output)],
    ['Cache read', fmt(totals.cacheRead)], ['Cache write', fmt(totals.cacheWrite)],
    ['Cache-read share', share(totals)], ['Cost', dollars(totals.cost)]
  ]) {
    const card = text('div', '', 'card');
    card.append(text('span', label), text('strong', value));
    summary.append(card);
  }
  renderBars(document.getElementById('bars'), dates, 'No usage for this selection.');
  const table = document.getElementById('models');
  table.replaceChildren();
  for (const [identity, value] of [...models].sort(([a], [b]) => a.localeCompare(b))) {
    const [company, name] = JSON.parse(identity);
    const tr = document.createElement('tr');
    for (const cell of [company || '(unspecified)', name || '(unspecified)',
      fmt(value.input), fmt(value.output), fmt(value.cacheRead), fmt(value.cacheWrite),
      share(value), dollars(value.cost)]) tr.append(text('td', cell));
    table.append(tr);
  }
  if (!models.size) {
    const tr = document.createElement('tr');
    const td = text('td', 'No usage for this selection.');
    td.colSpan = 8;
    tr.append(td);
    table.append(tr);
  }
  renderCompanies();
}
const coverage = stats.oldestDay && stats.newestDay ?
  stats.oldestDay + ' to ' + stats.newestDay : 'No dates available';
document.getElementById('context').textContent =
  'As of ' + stats.asOf + ' · Timezone: ' + stats.timezone + ' · Coverage: ' + coverage;
const warnings = document.getElementById('warnings');
for (const [key, label] of [
  ['malformed', 'malformed or incomplete lines skipped'],
  ['unreadable', 'unreadable files skipped'],
  ['future', 'events with future timestamps included'],
  ['outOfRange', 'events with out-of-range timestamps included']
]) {
  if (stats[key]) warnings.append(text('li', fmt(stats[key]) + ' ' + label));
}
if (!warnings.childElementCount) warnings.append(text('li', 'No data quality warnings.'));
provider.addEventListener('change', () => { updateModels(); render(); });
model.addEventListener('change', render);
period.addEventListener('change', render);
updateModels();
render();
"""

STYLE = """
:root { color-scheme: light dark; font: 16px/1.5 system-ui, sans-serif; }
body { max-width: 72rem; margin: auto; padding: clamp(1rem, 4vw, 3rem); }
h1, h2 { line-height: 1.2; }
.muted { opacity: .8; }
.controls, .summary { display: flex; gap: 1rem; flex-wrap: wrap; }
.controls label { display: grid; gap: .25rem; flex: 1 1 10rem; }
select { font: inherit; padding: .5rem; width: 100%; }
.summary { margin-block: 1.5rem; }
.card { border: 1px solid currentColor; border-radius: .5rem; padding: .75rem 1rem;
        flex: 1 1 9rem; display: grid; }
.card strong { font-size: 1.4rem; overflow-wrap: anywhere; }
.company-section { border-top: 1px solid #8888; padding-block: 1rem; }
.company-section h4 { margin-bottom: .5rem; }
.bars { list-style: none; padding: 0; }
.bar-row { display: grid; grid-template-columns: minmax(7rem, 9rem) 1fr auto;
           gap: .75rem; align-items: center; margin: .6rem 0; }
.track { height: 1rem; background: #75757555; border-radius: .3rem; overflow: hidden; }
.fill { height: 100%; background: #277bc0; }
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; text-align: left; }
th, td { border-bottom: 1px solid #8888; padding: .5rem; }
@media (max-width: 38rem) {
  .bar-row { grid-template-columns: 1fr auto; }
  .bar-row .track { grid-column: 1 / -1; grid-row: 2; }
}
"""


def render_dashboard(usage):
    """Embed only aggregate rows and stats, with script-safe JSON escaping."""
    rows = [
        {
            "day": row["day"],
            "provider": row["provider"],
            "model": row["model"],
            "company": company_for(row["provider"], row["model"]),
            **{
                key: str(row[key])
                for key in ("input", "output", "cacheRead", "cacheWrite")
            },
            "cost": row["cost"],
        }
        for row in usage["rows"]
    ]
    stats = {
        key: usage["stats"][key]
        for key in (
            "asOf",
            "timezone",
            "oldestDay",
            "newestDay",
            "malformed",
            "unreadable",
            "future",
            "outOfRange",
        )
    }
    payload = (
        json.dumps(
            {
                "rows": rows,
                "stats": stats,
                "companySlots": company_slots(usage["rows"]),
            },
            ensure_ascii=True,
            allow_nan=False,
        )
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    digest = hashlib.sha256(SCRIPT.encode("utf-8")).digest()
    import base64

    script_hash = base64.b64encode(digest).decode("ascii")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="300">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'sha256-{script_hash}'; style-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; form-action 'none'; base-uri 'none'">
<title>Pi usage dashboard</title><style>{STYLE}</style></head><body>
<main><h1>Pi usage</h1><p id="context" class="muted"></p>
<p class="muted">Offline snapshot · This page reloads every 5 minutes. Regeneration is separate.</p>
<section aria-labelledby="warnings-title"><h2 id="warnings-title">Data quality</h2><ul id="warnings"></ul></section>
<section aria-label="Usage filters" class="controls">
<label>Period<select id="period"><option value="day">Daily</option><option value="week">Weekly (Monday)</option><option value="month">Monthly</option></select></label>
<label>Provider<select id="provider"></select></label><label>Model<select id="model"></select></label>
</section>
<section aria-label="Usage totals" id="summary" class="summary"></section>
<section aria-labelledby="dates-title"><h2 id="dates-title">Tokens by date</h2><ul id="bars" class="bars"></ul></section>
<section aria-labelledby="models-title"><h2 id="models-title">Model totals</h2><div class="table-wrap"><table>
<thead><tr><th scope="col">Provider</th><th scope="col">Model</th><th scope="col">Input</th><th scope="col">Output</th><th scope="col">Cache read</th><th scope="col">Cache write</th><th scope="col">Cache-read share</th><th scope="col">Cost</th></tr></thead>
<tbody id="models"></tbody></table></div></section>
<section aria-labelledby="companies-title"><h2 id="companies-title">Company usage</h2>
<p class="muted">Company sections include all matching models, regardless of provider and model filters above.</p>
<div id="companies"></div></section></main>
<script type="application/json" id="usage-data">{payload}</script><script>{SCRIPT}</script>
</body></html>"""


_COVERAGE_KEYS = (
    "taskFiles",
    "malformedTasks",
    "unreadableTasks",
    "invalidTaskPaths",
    "missingChildLinks",
    "duplicateTaskLinks",
    "sddTasks",
    "unknownRoleTasks",
    "childFiles",
    "linkedChildFiles",
    "excludedChildFiles",
    "unlinkedChildFiles",
    "conflictingChildFiles",
    "parentFiles",
    "parentFilesExcluded",
    "unsafePaths",
    "unreadableDirectories",
    "unavailableRoots",
    "conflictingEntries",
)


def react_snapshot_json(usage):
    """Serialize the private v5 allowlist for offline HTML and local development.

    Rebuild every nested aggregate; preserve decimal-string token precision and
    script-safe escaping. This boundary never needs build assets or an output file.
    """

    def text(value):
        if not isinstance(value, str):
            raise ValueError("Expected aggregate text")
        return value

    def count(value):
        if type(value) is not int or value < 0:
            raise ValueError("Expected a nonnegative integer")
        return value

    def safe_count(value):
        # Warning/coverage counts become JS numbers; tokens use count() and strings.
        value = count(value)
        if value > 2**53 - 1:
            raise ValueError("Expected a nonnegative safe integer")
        return value

    roles = [text(role) for role in usage["roles"]]
    if any(
        not role
        or role.lower().startswith("sdd-")
        or re.search(r"[/\\\\\x00-\x1f]", role)
        for role in roles
    ):
        raise ValueError("Expected configured non-SDD role names")

    def owner(value):
        if value is not None and (not isinstance(value, str) or value not in roles):
            raise ValueError("Usage role is not configured")
        return value

    def calendar_day(value):
        value = text(value)
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError("Expected an ISO calendar day")
        return date.fromisoformat(value).isoformat()

    def record(value, keys):
        if not isinstance(value, dict) or not set(keys).issubset(value):
            raise ValueError("Missing aggregate fields")
        return value

    def items(value):
        if not isinstance(value, list):
            raise ValueError("Expected an aggregate list")
        return value

    def model_metrics(value):
        value = record(value, ("provider", "model", *_TOKEN_KEYS, "cost"))
        cost = value["cost"]
        if (
            type(cost) not in (int, float)
            or cost < 0
            or cost > 1.7976931348623157e308
            or not math.isfinite(cost)
        ):
            raise ValueError("Expected a finite nonnegative cost")
        return {
            "provider": text(value["provider"]),
            "model": text(value["model"]),
            **{key: str(count(value[key])) for key in _TOKEN_KEYS},
            "cost": cost,
        }

    rows = [
        {
            "day": calendar_day(row["day"]),
            "role": owner(row["role"]),
            **model_metrics(row),
        }
        for row in usage["rows"]
    ]
    sessions = []
    # Summaries originate in accepted collector events, not task/file metadata.
    # Rebuild every nesting level: injected private fields must never cross over.
    for item in items(usage.get("sessions")):
        session = record(
            item, ("startDay", "endDay", "role", "name", "sessionId", "days")
        )
        role = owner(session["role"])
        session_id = session["sessionId"]
        if session_id is not None and validated_session_id(session_id) is None:
            raise ValueError("Expected a valid session ID token or null")
        name = session["name"]
        if name is not None and not isinstance(name, str):
            raise ValueError("Expected a session name string or null")
        name = sanitize_session_name(name) if role is None else None
        start, end = calendar_day(session["startDay"]), calendar_day(session["endDay"])
        days = []
        previous = None
        for item in items(session["days"]):
            source_day = record(item, ("day", "models"))
            day = calendar_day(source_day["day"])
            if not start <= day <= end or (previous is not None and day <= previous):
                raise ValueError("Session days must be ascending and within bounds")
            models = [model_metrics(model) for model in items(source_day["models"])]
            if not models:
                raise ValueError("Session day requires accepted model usage")
            days.append({"day": day, "models": models})
            previous = day
        if not days or days[0]["day"] != start or days[-1]["day"] != end:
            raise ValueError("Session bounds must match accepted usage days")
        sessions.append(
            {
                "startDay": start,
                "endDay": end,
                "role": role,
                "name": name,
                "sessionId": session_id,
                "days": days,
            }
        )
    warnings = ("malformed", "unreadable", "future", "outOfRange")
    source = record(
        usage["stats"],
        ("asOf", "timezone", "oldestDay", "newestDay", "coverage", *warnings),
    )
    as_of = text(source["asOf"])
    if not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
        r"(?:\.[0-9]+)?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])",
        as_of,
    ):
        raise ValueError("Expected a timezone-aware ISO snapshot time")
    # Validate calendar/time fields without normalizing the exported timestamp.
    datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    stats = {"asOf": as_of, "timezone": text(source["timezone"])}
    stats.update(
        {
            key: None if source[key] is None else calendar_day(source[key])
            for key in ("oldestDay", "newestDay")
        }
    )
    stats.update({key: safe_count(source[key]) for key in warnings})
    coverage = record(source["coverage"], _COVERAGE_KEYS)
    stats["coverage"] = {key: safe_count(coverage[key]) for key in _COVERAGE_KEYS}
    # Version 5 opts into validated Pi header IDs ONLY in private file summaries.
    # Never export entry/task IDs, source paths, inferred titles or author provenance.
    # Enrich each disjoint parent/child row once; never append company totals.
    for row in rows:
        row["company"] = company_for(row["provider"], row["model"])
    payload = json.dumps(
        {
            "version": 5,
            "rows": rows,
            "roles": roles,
            "stats": stats,
            "sessions": sessions,
            "companySlots": company_slots(rows),
        },
        ensure_ascii=True,
        allow_nan=False,
    )
    return (
        payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    )


_CODEX_WARNINGS = (
    "duplicateFiles", "conflictingThreads", "invalidCounters",
    "ambiguousInitialCounters", "forkedThreads", "unsupportedLineage",
    "invalidIdentity", "invalidTimestamps", "missingModels",
)


def workspace_json(pi, codex):
    """Versioned source envelope; never extend or reinterpret the Pi v5 schema."""
    sources = {}
    for name, usage in (("pi", pi), ("codex", codex)):
        if usage is None:
            sources[name] = {"snapshot": None, "error": "Source unavailable."}
            continue
        snapshot = json.loads(react_snapshot_json(usage))
        entry = {"snapshot": snapshot}
        if name == "codex":
            metadata = usage["codex"]
            def status(value):
                if value not in ("available", "missing", "unavailable"):
                    raise ValueError("Invalid source status")
                return value
            def boolean(key):
                value = metadata[key]
                if type(value) is not bool:
                    raise ValueError("Invalid source capability")
                return value
            if boolean("costAvailable") or boolean("namesAvailable"):
                raise ValueError("Unsupported Codex capability")
            if (metadata["lineage"] != "explicit-thread-spawn-only" or
                    metadata["accounting"] != "validated-cumulative-deltas"):
                raise ValueError("Unsupported Codex accounting")
            warnings = {}
            for key in _CODEX_WARNINGS:
                value = metadata["warnings"][key]
                if type(value) is not int or not 0 <= value <= 2**53 - 1:
                    raise ValueError("Invalid source warning")
                warnings[key] = value
            if (any(row["cost"] != 0 for row in snapshot["rows"]) or
                    any(session["name"] is not None for session in snapshot["sessions"]) or
                    any(model["cost"] != 0 for session in snapshot["sessions"]
                        for day in session["days"] for model in day["models"])):
                raise ValueError("Unsupported Codex cost or name")
            entry["metadata"] = {
                "status": status(metadata["status"]),
                "roots": {key: status(metadata["roots"][key])
                          for key in ("sessions", "archived_sessions")},
                "costAvailable": False, "namesAvailable": False,
                "modelAvailable": boolean("modelAvailable"),
                "providerAvailable": boolean("providerAvailable"),
                "lineage": metadata["lineage"], "accounting": metadata["accounting"],
                "warnings": warnings,
            }
        sources[name] = entry
    return (json.dumps({"workspaceVersion": 1, "sources": sources},
                       ensure_ascii=True, allow_nan=False)
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def collect_workspace(pi_agent_home=None, codex_home=None, sessions=None):
    """Resolve source config only here; pass every root explicitly to collectors."""
    agent = Path(pi_agent_home or os.environ.get("PI_CODING_AGENT_DIR") or
                 "~/.pi/agent").expanduser()
    codex_home = Path(codex_home or os.environ.get("CODEX_HOME") or "~/.codex").expanduser()
    parents = Path(sessions or os.environ.get("PI_CODING_AGENT_SESSION_DIR") or
                   agent / "sessions").expanduser()
    try:
        pi = collect_combined_usage(parents, task_dir=agent / "gentle-agents/tasks",
                                    child_session_dir=agent / "gentle-agents/sessions",
                                    agent_dir=agent / "agents")
    except Exception:
        pi = None
    try:
        codex = collect_codex_usage(codex_home)
    except Exception:
        codex = None
    # Invalid returned payloads are hard errors, not a fabricated empty source.
    return workspace_json(pi, codex)


def render_react_dashboard(usage, assets_dir):
    """Embed the local IIFE/CSS and shared snapshot; fail before publication."""
    return _render_react_payload(react_snapshot_json(usage), assets_dir)


def render_workspace_dashboard(payload, assets_dir):
    """Render only bridge-generated envelopes; no refresh that loses captures."""
    return _render_react_payload(payload, assets_dir, workspace=True)


def _render_react_payload(payload, assets_dir, workspace=False):
    assets = Path(assets_dir)
    try:
        script = (assets / "dashboard.js").read_text(encoding="utf-8")
        style = (assets / "dashboard.css").read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(
            "React assets unavailable; run npm run build in web first"
        ) from exc
    if not script.strip() or not style.strip():
        raise ValueError("React build assets must not be empty")
    if re.search(r"</script", script, re.I) or re.search(r"</style", style, re.I):
        raise ValueError("React build contains an unsafe inline closing tag")
    def digest(value):
        return base64.b64encode(hashlib.sha256(value.encode("utf-8")).digest()).decode(
            "ascii"
        )

    refresh = '' if workspace else '<meta http-equiv="refresh" content="300">'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
{refresh}
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'sha256-{digest(script)}'; style-src 'sha256-{digest(style)}'; connect-src 'none'; img-src 'none'; font-src 'none'; object-src 'none'; form-action 'none'; base-uri 'none'">
<title>Pi usage · Offline preview</title><style>{style}</style></head><body>
<div id="root">Loading offline Pi usage preview…</div>
<noscript>JavaScript is required for the React preview. The legacy dashboard remains unchanged.</noscript>
<script type="application/json" id="usage-data">{payload}</script><script>{script}</script>
</body></html>"""


def write_dashboard(output, html):
    """Write a private file in the destination directory, then atomically publish."""
    output = Path(output).expanduser()
    parent = output.parent
    if not parent.exists() and not parent.is_symlink():
        parent.mkdir(mode=0o700, parents=True)
        # Only the directory created for this dashboard may be corrected for umask.
        parent.chmod(0o700)
    parent_info = parent.lstat()
    if (
        not stat.S_ISDIR(parent_info.st_mode)
        or parent_info.st_uid != os.getuid()
        or stat.S_IMODE(parent_info.st_mode) != 0o700
    ):
        raise ValueError(
            "output parent must be an owned, private (0700) directory, not a symlink"
        )
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=parent, prefix=".dashboard-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            stream.write(html)
        os.replace(temporary, output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate a private offline Pi usage dashboard"
    )
    parser.add_argument(
        "--sessions",
        type=Path,
        default=None,
        help="Pi JSONL session directory (legacy default unchanged)",
    )
    parser.add_argument(
        "--output", type=Path, help="destination HTML file (required for React preview)"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--preview-react",
        action="store_true",
        help="opt into the offline React build; requires a separate --output",
    )
    mode.add_argument(
        "--dev-json",
        action="store_true",
        help="emit private v5 usage JSON with explicit Pi names/header IDs; no files written",
    )
    mode.add_argument('--workspace-json', action='store_true',
                      help='emit versioned Pi/Codex workspace JSON; no files written')
    mode.add_argument('--preview-workspace', action='store_true',
                      help='offline Pi/Codex/Compare workspace without auto-refresh; requires separate --output')
    parser.add_argument('--pi-agent-home', type=Path,
                        help='Pi agent home; only with --workspace-json or --preview-workspace (otherwise PI_CODING_AGENT_DIR or ~/.pi/agent)')
    parser.add_argument('--codex-home', type=Path,
                        help='Codex home; only with --workspace-json or --preview-workspace (otherwise CODEX_HOME or ~/.codex)')
    args = parser.parse_args(argv)
    if not (args.workspace_json or args.preview_workspace) and (
            args.pi_agent_home is not None or args.codex_home is not None):
        parser.error('--pi-agent-home and --codex-home require --workspace-json or --preview-workspace')
    if args.workspace_json or args.preview_workspace:
        if args.workspace_json and args.output is not None:
            parser.error('--workspace-json cannot be combined with --output')
        if args.preview_workspace and (args.output is None or
                args.output.expanduser().resolve() == DEFAULT_OUTPUT.resolve()):
            parser.error('--preview-workspace requires --output away from the live default dashboard')
        try:
            payload = collect_workspace(args.pi_agent_home, args.codex_home, args.sessions)
        except Exception:
            raise SystemExit('Usage workspace unavailable.') from None
        if args.workspace_json:
            print(payload)
            return
        html = render_workspace_dashboard(payload, Path(__file__).resolve().parent / 'web/dist')
        write_dashboard(args.output, html)
        print('Dashboard updated (private usage workspace).')
        return
    args.sessions = args.sessions or DEFAULT_SESSIONS
    if args.dev_json:
        if args.output is not None:
            parser.error("--dev-json cannot be combined with --output")
        try:
            snapshot = react_snapshot_json(
                collect_combined_usage(args.sessions.expanduser())
            )
        except Exception:
            # Never leak collector paths, raw records, or exception details to the dev server.
            raise SystemExit("Usage snapshot unavailable.") from None
        print(snapshot)
        return
    if args.preview_react:
        if (
            args.output is None
            or args.output.expanduser().resolve() == DEFAULT_OUTPUT.resolve()
        ):
            parser.error(
                "--preview-react requires --output away from the live default dashboard"
            )
        usage = collect_combined_usage(args.sessions.expanduser())
        html = render_react_dashboard(
            usage, Path(__file__).resolve().parent / "web" / "dist"
        )
    else:
        usage = collect_usage(args.sessions.expanduser())
        html = render_dashboard(usage)
    write_dashboard(args.output or DEFAULT_OUTPUT, html)
    print(
        "Dashboard updated (private usage snapshot)."
        if args.preview_react
        else "Dashboard updated (aggregate metadata only)."
    )


if __name__ == "__main__":
    main()
