# Pi usage dashboard

A local, offline HTML snapshot of Pi usage metadata. It summarizes tokens and cost by local calendar day, ISO Monday-start week, month, provider, and model. It does not export prompts, responses, tool arguments, paths, cwd, or task threads. The private React snapshot includes explicitly opted-in Pi names and actual Pi header IDs; the legacy dashboard does not.

## Pi / Gentle Shell, Codex and Compare workspaces

The React app has separate **Pi / Gentle Shell**, **Codex**, and **Compare** source
navigation. Existing Pi links (`#/overview`, `#/companies`, `#/agents`,
`#/sessions`, `#/stats` and their detail/filter links) are unchanged. Codex uses
`#/codex/overview`, `#/codex/companies`, `#/codex/agents`, `#/codex/sessions`, and
`#/codex/stats`; its detail/day/filter links stay in that source. Source switches
reset view filters. The same focused views render each source, never merged totals.
Missing/unavailable Codex leaves Pi and Compare usable, without fabricated records.

Development now loads the additive `/__usage_workspace` endpoint, backed by
`dashboard.py --workspace-json`. Its version-1 envelope is
`{workspaceVersion: 1, sources: {pi: {snapshot}, codex: {snapshot, metadata}}}`.
Each snapshot retains the strict Pi-compatible v5 wire shape; Codex metadata is a
separate allowlisted capability/coverage object, not an extension to Pi v5. A
failed collector yields `{snapshot: null, error: "Source unavailable."}` for only
that source. Missing Codex roots carry their explicit metadata, but are unavailable
for comparison capture. Invalid returned schemas fail closed. Old standalone
v3/v4/v5 Pi snapshots still load, and `/__pi_usage_snapshot` / `--dev-json` retain
their old v5 Pi-only behavior and source defaults. Both endpoints retain the same
loopback/Host/Origin checks, GET-only policy, no-store, timeout, 16 MiB output limit
and shared overlapping collection (per endpoint). Neither endpoint writes files.

New workspace collection resolves Pi agent home from `--pi-agent-home`, then
`PI_CODING_AGENT_DIR`, then `~/.pi/agent`. Parent sessions use `--sessions`, then
`PI_CODING_AGENT_SESSION_DIR`, then that agent home's `sessions`; roles and child
linkage use the selected agent home explicitly. Codex uses `--codex-home`, then
`CODEX_HOME`, then `~/.codex`. Source homes/paths are configuration only, never
exported. **`--pi-agent-home` and `--codex-home` are accepted only with
`--workspace-json` or `--preview-workspace`.** Supplying either root option in
legacy, `--dev-json`, or `--preview-react` mode exits 2 before any collection,
rendering or output, with the argparse error:
`--pi-agent-home and --codex-home require --workspace-json or --preview-workspace`.
Without these root options, old CLI/default/timer source resolution is unchanged;
`--sessions` remains supported in both Pi-only and workspace modes.
Codex reads only supported rollout metadata under `sessions` and
`archived_sessions`; see [accounting and exclusions](docs/codex-accounting.md).
No live-source access is implied by running tests.

Codex **recorded cost and names are Unavailable**, never $0/free usage. No spend
is synthesized from tokens or API reference prices. Provider and model are
independent explicit recorded labels; unknown labels remain unspecified. Companies
still use the existing Python model classifier, not an invented provider identity.
Only supported explicit nested child roles appear under Agents; no project,
pstack or workflow attribution is inferred. Codex quality disclosures explain
partial roots, cumulative-counter exclusions, unsupported lineage and warnings.

For a private, self-contained offline workspace, after independent build verification:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 dashboard.py --preview-workspace --pi-agent-home /path/to/pi-agent --codex-home /path/to/codex-home --output /path/to/private/workspace.html
```

This is opt-in and requires a separate explicit output away from the live default.
It reuses hashed CSP (`connect-src 'none'`) and the atomic 0600 / owned-0700 writer.
It makes no network requests and **never auto-refreshes**, preserving unsaved
comparison work. Legacy HTML and `--preview-react` retain their refresh behavior;
no service/timer is added or changed.

Compare requires your own experiment/project and run/workflow labels and explicit
measured-file selection from each runtime. Parents never automatically select or
recount children. Captures freeze accepted date/model token summaries with snapshot
as-of/timezone; source navigation preserves captures during this page lifetime.
Input, output, cache read and cache write are disjoint exact integer categories;
reasoning already in output is not added again. Different models compare setups,
not harness causation; there is no automatic quality/cost winner. Export JSON to
text (copy to a private file), or import text/a JSON file to restore captures.
Invalid imports leave existing work unchanged. Exports omit source paths, session
IDs and transcript names. There is no automatic persistence: export before
closing/reloading. Use the same task/docs/model/settings/baseline and comparable
cache conditions when drawing conclusions.

## Local React development with real usage

This is one project: Python collects and sanitizes usage, and React displays the
same private snapshots in Vite development and offline production HTML. The new
workspace envelope keeps source identity/capabilities separate from Pi v5.
Version 5 adds nullable, validated actual Pi header `sessionId` values on measured
file summaries only. These are **not unique file or conversation IDs**: multiple
measured files can share one. IDs persist across snapshots and may correlate
private activity. Only the first physical `type:session` header's `id` is eligible,
never filenames, task IDs, usage-event IDs, or later metadata. Missing or unsupported
custom IDs are unavailable, not repaired: the accepted token is 1–128 ASCII
characters matching `[A-Za-z0-9][A-Za-z0-9._-]*`.
The snapshot also retains explicit stored Pi `session_info.name` display text for measured
parent files, with the user's opt-in. These names may be set by `/name`, CLI, RPC,
or extensions: **human authorship is not guaranteed**. Names may themselves contain
sensitive text; they are not a redaction boundary. There is no inferred title,
first-message, or task-content fallback. Unnamed parents and linked children keep
the actual header ID as their primary label (`Session <ID>`), or `ID unavailable`,
with parent/child role context. Explicit names remain primary when present.
Older v3/v4 snapshots ignore injected IDs; v3 also ignores injected names,
while v4 preserves explicit Pi names.
From the project root, with Python 3 and the existing web dependencies available:

```sh
pnpm dev
```

Open **http://127.0.0.1:5173/** to use your actual local Pi usage with Vite's normal
source updates/HMR. No separate snapshot generation or production build is needed.
Workspace source resolution follows the options/environment described above.
The legacy Pi-only endpoint still reads `PI_CODING_AGENT_SESSION_DIR` when set,
otherwise `~/.pi/agent/sessions`, with child metadata under `~/.pi/agent/`. Reload the page to collect fresh usage; HMR updates source code,
not the collected data. Loading and collection failures are shown explicitly,
never replaced with fabricated data.

The dev-only `/__pi_usage_snapshot` endpoint calls the stdout-only
`dashboard.py --dev-json` route. It serves only validated, allowlisted aggregates
and explicit private Pi names and actual Pi header IDs, not collected prompts,
responses, task contents, paths, or cwd. A name can still contain such sensitive text by itself. Nothing is written
under `web/` or to either private dashboard output. `web/dev-snapshot.json` remains
a fabricated test fixture, not the default data source.

**Local laptop development only:** the listener binds to `127.0.0.1:5173`, rejects
non-loopback Host and foreign Origin headers, disables CORS, and marks responses
no-store. It is not authenticated against other local programs. Do not deploy,
proxy, forward the port, widen filesystem access, or copy private snapshots into
`web/`. Collection is limited to 30 seconds and 16 MiB per subprocess output;
overlapping requests share one collection. **Ctrl+C** stops the server. If port
5173 is occupied, startup fails rather than switching ports.

The root package is only a command runner; dependency and npm lockfile ownership
remain in `web/` (no root install or lockfile needed).
Dependency setup remains `cd web && npm ci --ignore-scripts`; `pnpm dev` runs scripts only, without automatic installation.

### Real usage: PRIVATE file:// preview

After building the production assets with `cd web && npm run build`, run from
the project root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 dashboard.py --preview-react --output "$HOME/.local/share/pi-usage-dashboard/preview.html"
xdg-open "$HOME/.local/share/pi-usage-dashboard/preview.html"
```

This alternative reads real usage and writes a **private file** without a listener.
Open that file directly via `file://`; do not serve it with Vite. Production builds
remain library IIFE/CSS assets and exclude the dev HTML and synthetic fixture.
Never publish names, actual IDs, or real snapshots in web assets, source fixtures, or public
hosting. The offline preview is a 0600 file inside an owned 0700 directory; the
dev endpoint is available only on loopback `127.0.0.1`.
The live `index.html` and its existing five-minute timer still use the **legacy
dashboard**. The independent preview timer below updates only `preview.html`.

### Overview trend and rankings

The right-hand ranking panel defaults to **Models**. Use its **Models / Providers**
buttons (Tab to focus, Enter or Space to activate) to switch the same panel without
changing filters, cards, trend, or the provider donut. **Top providers** shows at
most five recorded provider identifiers ranked by exact positive token volume,
using the same selected dates and provider/model filters as the cards and donut.
Identifiers such as `openai-codex` remain source labels, not inferred brands;
blank labels show **(unspecified provider)**. One qualifying provider means one
row, with no fictional competitors or empty provider slots. The donut alone
combines providers beyond five into **Other**. Switching back restores Top models
and its eligible empty-row notes.

Weekly Usage trend shows **five consecutive Monday-start calendar weeks** ending
with the selected week. Each expands into seven day links, with missing records,
recorded zero and future dates distinguished. At the supported calendar limits
(years 0001–9999), unavailable weeks/days are clipped and disclosed, never invented.
Trend context does not widen the selected range for cards, Top models or provider
shares; provider/model filters still apply to every displayed contribution.

Across Daily, Weekly, Monthly, Custom, All-time and quick ranges, each recorded
Overview trend bucket shows its numeric token total with its own bare cache-read
share below it: `cacheRead / (input + cacheRead + cacheWrite)`, or **N/A** for a
zero denominator. Dates, totals, percentages and missing labels use `1rem` text;
percentages match Agent trend's muted color. Token units and **Cache-read share**
remain accessible without visible unit text. Missing buckets say **No recorded
usage** without a fabricated amount or percentage; recorded zero and future
status remain explicit. Weekly/monthly recorded cost remains in the button's
accessible label and expanded daily contributions, separate from API reference
prices; the card still reports its selected range's cost. Daily/All-time/Custom
rows remain day Sessions links with selected provider/model query parameters;
Weekly/Monthly remain keyboard-accessible disclosures with all-day and separate
filtered Sessions links.

Top models ranks at most five actual selected provider/model pairs. Each real
model's numeric token total has its own selected-pair cache-read percentage below
it, matching Agent Top models' muted size/color, or **N/A** at zero eligible input.
The token unit and cache-read meaning remain accessible; the raw provider label,
rank, model name and price/cost disclosure remain unchanged. Providers mode still
shows token units, and empty notes have no percentage. A single
Daily day (including Today/Yesterday) or a Weekly range (including Last Week)
with 1–4 actual pairs and positive token usage adds visibly labeled **Empty row**
notes to make five visual rows. Three real pairs therefore show two empty rows;
five real pairs show none. Each note identifies the selected day/weekly range
and filters, stays outside the real ranked list, and has no model/provider name,
rank, tokens, cost, link or historical source. Counts and totals remain unchanged.
Provider/model filters determine the real pairs and remaining empty rows together.
Monthly, Custom, All-time, Last 7 Days, zero-data and invalid selections never add
empty rows. Desktop rows share the available panel height; mobile rows use natural
sizing. The panel has no visible scope footer.

### Agent detail trend and rankings

The Agent detail header keeps the role title, **Back to Agents** link and
**Linked children only** badge without visible explanatory paragraphs. The role
heading's nonvisible accessible description retains the snapshot as-of time and
timezone, actual usage-event scope (not launch metadata/task totals), and the
limitation that historical child attribution may be incomplete; parent, SDD and
unlinked usage are excluded. Other routes' snapshot cues and Agents' global
quality/footer context are unchanged.

Each configured non-SDD Agent detail shows Usage trend beside a Models / Providers
ranking panel; unused roles remain on the Agents index. Cards, both panels and the
full totals tables use **only that exact role's accepted rows**, with
current provider/model filters across **all recorded dates**. Period selects daily,
Monday-weekly or monthly granularity, not a latest-day range. All recorded buckets
remain in normal page flow, without a chart height limit or nested scrollbar.
Long histories make the page longer: use ordinary page scrolling to reach every
bucket, with no truncation. Daily also shows the latest seven consecutive calendar
days ending on the last recorded day for the selected role/provider/model, while
keeping all earlier recorded dates. Missing days within that window explicitly say
**No recorded usage**, with no token amount, bar, cache percentage or cost; they
never change totals or rankings. Thus six consecutive recorded days gain a preceding
gap day, not invented usage. The window clips safely at year 0001; an empty selection
has no invented calendar. Weekly/monthly grouping stays recorded-only. Desktop panels
align at both top and bottom with equal outer heights, while chart and ranking
rows stay naturally sized at the top (a long trend may leave blank ranking-panel
space). On mobile, each panel has its own natural height and rankings follow the
complete trend. Other gaps
are omitted, recorded zero is labeled, and future dates are marked against the
snapshot date (including missing days in a future-ending daily window). Each trend row
also shows its own bucket's cache-read percentage beneath the token total, recomputed
from that bucket's filtered tokens rather than copied from cards or model totals.
Agent trend dates, token totals, percentages and gap messages all use `1rem` text.
Trend amounts show only the number, with the token unit retained for assistive
technology. Trend cache percentages use the same legible muted color as Top models
cache percentages, not transparency. These styles do not change Overview, ranking
or Company typography.
There are no Sessions links here because Sessions cannot currently filter by role.

Top models shows at most five actual provider/model pairs ranked by exact total
tokens, with deterministic pair-key ties. Compact rows show only rank, model,
numeric token total, cache-read percentage and the disclosure control. The accessible
row label retains token units, raw provider and Python-owned company classification;
the full Agent Model totals table also shows provider, model and a **Company** column.
Missing classification is **Company unavailable**; contradictory classifications
are **Company unavailable/mixed**, never resolved by guessing. Beneath each
real model's token total, a percentage shows `cacheRead / (input + cacheRead
+ cacheWrite)` for that same filtered pair, or **N/A** at a zero denominator. Both
panels show only the compact percentage/N/A; screen-reader descriptions retain
**Cache-read share** and explain N/A. This is a token share, not a request-level
hit rate, and never changes volume ranking.
Positive selections with 1–4 models may show non-ranked empty notes through five visual rows.
Providers mode shows only up to five actual positive-volume provider identifiers,
without filler. Real model rows expand with the same three offline reference/cost
fields described below, using this agent-filtered pair's recorded cost. Company
detail keeps its existing **Tokens by date** view. Historical linkage may be incomplete.

### Model offline price reference

Click a **real Top models row** (or focus it and press Enter/Space) to expand it
in place; activate it again to collapse. One pair opens at a time. Date/range,
provider/model filter and Models/Providers mode changes close the details. Empty
row notes and provider rows never expand.

The expansion shows **only three compact fields**: **Input · API reference
(USD / 1M tokens)** and **Output · API reference (USD / 1M tokens)**, each with its
reference price or **Unavailable**, plus **Recorded cost estimate (USD)** from
that exact selected provider/model pair's recorded total. The recorded amount
uses the same selected daily rows and filters as its ranking token total; it is
never calculated from the reference rates. No token breakdowns, source URLs,
dates or explanatory paragraphs are visible inside the expansion. The nonvisible
accessible description supplies provenance and billing limitations. Mouse hover
or activation leaves no persistent purple fill/border; keyboard focus retains a
visible ring. Ranking-row token totals and the existing **Recorded cost** card
remain unchanged.

**API reference, not actual Pi/Codex spend**: prices are USD per **1M (1,000,000)
standard short-context text tokens**, manually checked on **2026-09-24**, not
effective-dated billing tariffs. Only exact providers `openai` and `openai-codex`
and the exact IDs below qualify. Every other provider/ID, blank, alias or
ambiguous SKU shows **Unavailable** in both fields, with **Rate unavailable**
explained accessibly.

Recorded cost is a separate selected-range estimate in USD, not an invoice. A
recorded zero **may mean unavailable metadata**, not free usage. Snapshot v5
exports no component dollars; neither tokens nor total cost can establish them.
Token counts stay BigInt; parent and linked child rows are summed once, not
session summaries.

| Exact public model ID | Input USD / 1M | Output USD / 1M | Official source |
| --- | ---: | ---: | --- |
| `gpt-6-sol` | 2 | 10 | `https://developers.openai.com/api/docs/models/gpt-6-sol` |
| `gpt-6-astra` | 10 | 50 | `https://developers.openai.com/api/docs/models/gpt-6-astra` |
| `gpt-5.6-sol` | 4 | 20 | `https://developers.openai.com/api/docs/models/gpt-5.6-sol` |
| `gpt-5.6-terra` | 2 | 12 | `https://developers.openai.com/api/docs/models/gpt-5.6-terra` |

`gpt-5.6-sol` is promotional, **not guaranteed after November 21, 2026**.
`gpt-5.5` is deliberately excluded because the reviewed model-page UI ambiguously
labeled Batch API pricing. **Historical applicability unknown**: v5 has no
per-request processing tier or effective tariff. Prices may differ for selected
historical dates, prompts **>272K input tokens** (long-context multipliers),
processing tier, region, Batch discounts and subscriptions. An API reference
never establishes Pi `openai-codex` subscription pricing or actual spend, and no
listed rate is claimed to apply to collected usage. Tokens are never multiplied
by reference rates to synthesize component charges.

The small `web/src/modelPrices.ts` snapshot and caveat metadata are bundled
locally. Source URLs and checked dates are available in the nonvisible accessible
description and this README. URLs are inert text, not browser links or fetch
targets; expanding details makes no network requests. The v5 schema, legacy output and private publication boundaries are
unchanged.

### Overview provider share

**Usage by provider**, directly below Usage trend, uses the same selected daily
rows, period/date range, and provider/model filters as the cards. It sums input,
output, cache-read, and cache-write tokens exactly, including accepted parent and
linked child usage once; neither session summaries nor trend context add tokens.
The five leading positive-volume provider labels appear with exact totals and
rounded two-decimal shares; the remainder becomes **Other (N providers)**. Ties
use deterministic name order, blank labels show **(unspecified provider)**, and
the most-used provider is marked in text. Missing, recorded-zero, invalid, and
future selections remain explicit; empty selections never become a 100% chart.
The original SVG ring and text legend need no external assets or network access.

### Sessions and date drill-downs

Sessions starts with collapsed date groups, newest first. Each heading shows the
weekday and ISO date; Today/Yesterday use the snapshot's local as-of date, not
the browser clock. Clicking the heading toggles the group. A separate, keyboard-accessible
**Open day page** link beside every all-days heading works while collapsed and opens
`#/sessions/YYYY-MM-DD` with that group expanded. It shows the full day and resets
local name/share filters; it does not preserve a filtered subset.
Opening a group reveals compact named or actual-ID/unavailable cards with
parent/child role context and expandable actual model details. **Shared Pi ID across
measured files** marks repeated IDs using the entire snapshot, independent of date,
name, share, or provider/model filtering. Repeated IDs never merge cards; a single
multi-day file is not considered shared merely because it appears on several dates. Counts describe **measured session files with
attributed usage**, not unique conversations. A multi-day file appears once per
contributing event-local day, with only that day's matching usage. The global
count counts each file once and totals include each contribution once. Collapsed
date headings and individual cards show cache-read tokens and percentage beside
total tokens and recorded cost, calculated from the same day-scoped contributions.

Both all-days and selected-day Sessions have compact, Overview-style controls
(they do not change Overview):

- **Session name** searches only explicit Pi names by case-insensitive substring,
  ignoring surrounding search spaces. It never searches role labels or inferred
  titles. Unnamed parents and linked children are excluded while a term is active.
- **Cache-read share above (%)** accepts a freely entered 0–100 threshold, with
  up to six decimal places, such as `95` or `95.5`. Use digits and an optional
  decimal point: no signs, spaces, leading zeros (except `0`/`0.5`), exponent
  notation, percent signs, or incomplete decimals such as `95.`. Empty means no
  threshold; invalid input shows an accessible error and **UNAVAILABLE** results,
  never all files or stale totals. **Clear** (Clear name/share filters) restores both
  fields without changing the selected day or provider/model filters.
  Navigating to another route starts with empty name/share fields; they are not
  exported in the snapshot or stored in date links.

The threshold is **strictly greater than**, using exact, unrounded integer ratios:
`cacheRead / (input + cacheRead + cacheWrite)`. Output tokens are excluded. It is
tested separately for each measured file and event-local day, **after** matching
provider/model contributions, not against lifetime totals or group averages.
A multi-day file may qualify on one day but not another; its global measured-file
count is still one. Zero-denominator N/A is excluded with an active threshold;
`0` keeps positive shares, and `100` keeps none. Display percentages round to two
decimals, so a share displayed as `95.00%` may still be strictly above `95`.
Name and threshold filters intersect; counts, cards, groups, and contribution
totals all use that same subset.

All-days and valid selected-day Sessions routes (including provider/model-qualified
links) show a concise snapshot as-of time and timezone in the heading. Below the
controls, only the dynamic measured-file count or invalid-input feedback remains:
input guidance is still available to assistive technology, without visible
instructional, identity/privacy or day-navigation paragraphs. The limitations
and privacy guidance remain documented here. Overview and all valid Sessions
routes omit the global Data quality panel and footer; Companies, Agents, Stats
and invalid routes retain both. This changes presentation only, not collected
quality data or usage accounting.

A selected-day route opens that day's group and compares file contributions with
independent daily row totals. With a name/share filter, daily rows remain a
clearly labeled **unfiltered-by-name/share comparison**, still scoped to the
selected day and provider/model. Rows have no per-file name/share selection, so
their totals need not equal the displayed subset: no reconciliation or discrepancy
is claimed. Clearing name/share filters restores normal exact-token reconciliation
and the existing disclosed cost tolerance. Missing records and recorded zero usage remain
distinct; neither proves inactivity or complete historical attribution.
Weekly/Monthly expanded **date links open all sessions for that date**, without
provider/model filters. When filters apply, a separate **Filtered Sessions** link
reconciles the displayed filtered amount, which is not the all-day total. Daily,
All-time, and Custom range links retain their selected provider/model filters.

## Generate and open

```sh
PYTHONDONTWRITEBYTECODE=1 python3 dashboard.py
xdg-open "$HOME/.local/share/pi-usage-dashboard/index.html"
```

The default source is `PI_CODING_AGENT_SESSION_DIR` when set, otherwise `~/.pi/agent/sessions`. The default destination is `~/.local/share/pi-usage-dashboard/index.html`. To use different locations:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 dashboard.py --sessions /path/to/sessions --output /path/to/private/index.html
```

If the destination directory does not exist, the dashboard creates it privately (0700). An existing destination directory must already be owned by the current user with mode 0700; unsafe directories (including `/tmp`), and a symlink as the direct output parent, are rejected without changing their permissions. The HTML is atomically replaced with mode 0600. For an override, choose a dedicated private directory rather than a shared directory. The page makes no network requests and needs no server. Its browser refreshes every 300 seconds, but **refreshing does not regenerate the snapshot**. The optional user-level systemd timer below regenerates it every five minutes while the user manager runs. Manual generation is also available; both commands suppress Python bytecode near the source.

The page displays its generation time, local timezone, and covered date range. Malformed or incomplete lines and unreadable files are skipped with visible counts; future and out-of-range events are counted with warnings and included in the totals. A wrong system clock can make calendar buckets misleading. Cache-read share is `cacheRead / (input + cacheRead + cacheWrite)` and is N/A when the denominator is zero. Output tokens and cost are shown separately. Provider and model filters apply to the global date bars, model table, and headline totals only.

Five company sections appear below the global dashboard: OpenAI and DeepSeek are always present, and the other three are chosen on each generated snapshot by summed input, output, cache-read, and cache-write tokens. Only companies with positive measured token volume qualify; ties use company name order. If fewer than three qualify, visible unfilled slots remain. Each company section includes all its matching models regardless of the global provider/model filters, with total tokens, cache-read share, a daily/weekly/monthly trend controlled by the period picker, and a model breakdown. A company with no measured usage (including DeepSeek when absent) shows N/A cache-read share and an empty state. Regenerate the snapshot to include newly used models and recalculate the top three.

Classification uses an explicit model namespace or anchored family before a recognized direct provider alias (for example `openai-codex` → OpenAI). `openrouter` is a broker, not a company fallback: recognized models routed through it can be attributed, but unknown or blank model/provider rows remain **unclassified**. Unclassified rows still contribute to the global dashboard, never to a company section. No prompt or session content is included.

### CommandCode models in Pi

Pi usage events retain the actual provider and model IDs; no provider registration
is needed in the dashboard. For the `commandcode` provider,
`deepseek/deepseek-v4.1-flash` is classified as DeepSeek, while the `zai-org` and
`z-ai` model namespaces (including `zai-org/glm-5.3` and
`z-ai/glm-5.3-flash`) are classified as Z.ai. CommandCode remains the provider
label, not a company alias. Unknown namespaces remain unclassified. Company
cards still follow the existing token-volume ranking; configured models without
recorded usage do not create artificial rows or totals.

Installing the standalone CommandCode CLI is not required for Pi provider usage.
The dashboard does not yet collect standalone CommandCode CLI session files.
For optional local CLI inspection, install it at the workspace root with
`pnpm add -D -w command-code@latest` and check `pnpm exec cmd --version`
(Node.js 22+). It is a development dependency, not part of the browser bundle or
usage collection; no global installation or CLI login is needed for Pi usage.
GOAT subscription credits are not recorded spend, and no CommandCode price
reference is applied to usage to infer charges.

## Optional user timer

Run these commands as your normal user, from this project directory (`~/Projects/pi-usage-dashboard`). Review the source units under `systemd/` first. Installation copies units into the user manager; it does not require root, open a port, or install a server. The timer starts on the next five-minute calendar boundary; `Persistent=true` catches up after a missed calendar run when the user manager resumes. It does not keep the manager running: without user-manager lingering, scheduled runs stop after logout and resume on the next login. No lingering or system-level setup is required here.

Install and activate only after checking the paths and verifying the units:

```sh
cd "$HOME/Projects/pi-usage-dashboard"
systemd-analyze --user verify systemd/pi-usage-dashboard.service systemd/pi-usage-dashboard.timer
install -d -m 700 "$HOME/.config/systemd/user"
install -m 600 systemd/pi-usage-dashboard.service "$HOME/.config/systemd/user/pi-usage-dashboard.service"
install -m 600 systemd/pi-usage-dashboard.timer "$HOME/.config/systemd/user/pi-usage-dashboard.timer"
systemctl --user daemon-reload
systemctl --user enable --now pi-usage-dashboard.timer
```

After editing either source unit, refresh the installed copies and the running timer (a source edit alone does not change the installed units):

```sh
cd "$HOME/Projects/pi-usage-dashboard"
install -m 600 systemd/pi-usage-dashboard.service "$HOME/.config/systemd/user/pi-usage-dashboard.service"
install -m 600 systemd/pi-usage-dashboard.timer "$HOME/.config/systemd/user/pi-usage-dashboard.timer"
systemctl --user daemon-reload
systemctl --user restart pi-usage-dashboard.timer
```

Check schedule, most recent service execution, and errors; run the installed oneshot manually for immediate regeneration:

```sh
systemctl --user list-timers --all pi-usage-dashboard.timer
systemctl --user status pi-usage-dashboard.timer pi-usage-dashboard.service
journalctl --user -u pi-usage-dashboard.service -n 30 --no-pager
systemctl --user start pi-usage-dashboard.service
```

Disable scheduling and roll back the *installed copies* if needed (the project source and generated HTML are not deleted):

```sh
systemctl --user disable --now pi-usage-dashboard.timer
rm -- "$HOME/.config/systemd/user/pi-usage-dashboard.service" "$HOME/.config/systemd/user/pi-usage-dashboard.timer"
systemctl --user daemon-reload
```

The default generated HTML remains at `~/.local/share/pi-usage-dashboard/index.html` after disabling the timer. If the clock or timezone is wrong, the displayed as-of time and local date coverage may be misleading even with a healthy timer; inspect the page's data-quality warnings for future/out-of-range timestamps.

## Optional private React preview timer

This separate user timer regenerates
`file:///home/miguelgcd/.local/share/pi-usage-dashboard/preview.html` every five
minutes, at minutes 02, 07, 12, … (legacy runs at 00, 05, 10, …). It neither
replaces nor depends on the legacy units. It runs unprivileged with `UMask=0077`,
opens no listener, makes no network requests, and runs **no npm or build command**.
It embeds the last independently verified `web/dist` assets with fresh aggregate
data. Publication uses the existing atomic writer: an owned 0700 parent and a
0600 HTML file. Unsafe existing directory permissions are rejected, not changed.

### Verify assets before enabling publication

The service requires `web/dist/.verified` via `ConditionPathExists`. Without it,
systemd **skips** regeneration and preserves the previous preview. The marker is
an operator attestation, not an automatic build check; only the parent/operator
creates it after independent verification. Missing or invalid assets still fail
before atomic replacement, preserving the old preview without affecting legacy
regeneration. Inspect the journal to distinguish skips from failures.

UI source edits require `cd web && npm run build`, independent verification, and
an immediate manual preview refresh; the timer only refreshes data. The supported
`cd web && npm run build` command automatically invalidates only `dist/.verified`
in its npm `prebuild` hook, before Vite changes output; other files are not removed
by the hook. Vite does not empty `dist`. Direct `vite build` (or disabling npm
lifecycle scripts) bypasses this invalidation contract.

Before **every future build or asset change**, still stop only the preview units
and remove the marker first, so an already running service cannot publish while
assets change. Automatic invalidation does not stop an in-flight service:

```sh
cd "$HOME/Projects/pi-usage-dashboard"
# For an already installed preview timer:
systemctl --user stop pi-usage-dashboard-preview.timer pi-usage-dashboard-preview.service
rm -f -- web/dist/.verified
(cd web && npm run build)
```

For a first build, omit the stop command when the preview units are not installed.
Keep scheduling stopped and the marker absent until independent checks of this
exact build pass. Do not treat build success alone as verification. Then the
parent/operator may attest it:

```sh
touch web/dist/.verified
```

Do not modify assets after attestation without repeating that workflow. A direct
`dashboard.py --preview-react` invocation bypasses systemd's marker condition;
use it only after verification, never to publish an unchecked build.

### Install, refresh, and check only preview units

After verification, run as your normal user from the project directory:

```sh
systemd-analyze --user verify systemd/pi-usage-dashboard-preview.service systemd/pi-usage-dashboard-preview.timer
install -d -m 700 "$HOME/.config/systemd/user"
install -m 600 systemd/pi-usage-dashboard-preview.service "$HOME/.config/systemd/user/pi-usage-dashboard-preview.service"
install -m 600 systemd/pi-usage-dashboard-preview.timer "$HOME/.config/systemd/user/pi-usage-dashboard-preview.timer"
systemctl --user daemon-reload
systemctl --user enable --now pi-usage-dashboard-preview.timer
systemctl --user start pi-usage-dashboard-preview.service
xdg-open "$HOME/.local/share/pi-usage-dashboard/preview.html"
```

After future verified builds, manually refresh immediately, then resume scheduling:

```sh
systemctl --user start pi-usage-dashboard-preview.service
systemctl --user start pi-usage-dashboard-preview.timer
```

After source unit edits, repeat the two preview `install -m 600` commands and
`daemon-reload`, then `systemctl --user restart pi-usage-dashboard-preview.timer`.
Check the schedule, service outcome, and file permissions:

```sh
systemctl --user list-timers --all pi-usage-dashboard-preview.timer
systemctl --user status pi-usage-dashboard-preview.timer pi-usage-dashboard-preview.service
journalctl --user -u pi-usage-dashboard-preview.service -n 30 --no-pager
stat -c '%a %U %n' "$HOME/.local/share/pi-usage-dashboard" "$HOME/.local/share/pi-usage-dashboard/preview.html"
```

A successful oneshot may be inactive after completion. Expect directory mode 700
and file mode 600, owned by your user. `Persistent=true` catches missed calendar
runs when the user manager resumes; it does not enable lingering. Reloading the
browser never regenerates data; check the page's as-of time as well as the timer.

### Roll back only the preview timer

These exact commands stop preview work and remove only its installed units. They
leave legacy scheduling, `index.html`, the existing private `preview.html`, and
all project source/assets untouched:

```sh
systemctl --user disable --now pi-usage-dashboard-preview.timer
systemctl --user stop pi-usage-dashboard-preview.service
rm -- "$HOME/.config/systemd/user/pi-usage-dashboard-preview.service" "$HOME/.config/systemd/user/pi-usage-dashboard-preview.timer"
systemctl --user daemon-reload
```

## Test

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```
