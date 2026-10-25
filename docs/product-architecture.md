# Local usage workspaces and comparisons

## Product and scope

One private, local dashboard serves Pi/Gentle Shell and Codex CLI/Desktop as separate workspaces. Both expose Overview, Companies, Agents, Sessions and Stats where source records support them. Compare lets one user explicitly group selected measured session summaries into labeled experiment runs and compare recorded token usage for the same project. The dashboard does not execute projects, inspect prompts to establish task equivalence, or declare a quality winner.

The current Pi-only dashboard is preserved on local `main` at `ac88a74`. Implementation is on `feat/codex-usage-comparisons`; later merging and publication remain user decisions. No dependencies, infrastructure, installers or scheduled services are added.

## Sources and ownership

- `pi_usage.py` remains the Pi event/accounting owner. Its public collection APIs and established accounting behavior are not silently rewritten for this feature.
- `codex_usage.py` owns Codex rollout discovery, schema validation, cumulative-counter normalization, safe explicit child-role metadata, copy deduplication, and Codex coverage warnings. Python stdlib only. It may reuse demonstrated safe filesystem helpers from `pi_usage.py`, not Pi event parsing.
- `dashboard.py` owns strict Python-to-browser allowlists, source collection orchestration, source capability metadata and private offline publication. The existing Pi v5 serializer and legacy routes remain compatibility seams.
- `web/src/data.ts` continues owning parsed Pi-compatible usage values and exact BigInt arithmetic. `web/src/workspace.ts` owns a versioned envelope containing separate parsed source snapshots and capabilities.
- `web/src/App.tsx` owns source navigation and routes, not collector or experiment business logic. Reuse existing focused page components; small source-aware props/helpers may adapt unavailable costs and source-specific copy. Never copy the whole React dashboard into a second Codex tree.
- `web/src/comparison.ts` owns explicit run capture, validation, exact token comparisons and versioned export/import. `Compare.tsx` owns experiment inputs, measured-file selection and results presentation. `comparison.css` is narrowly scoped to this page.

## Collection and privacy

Codex rollout roots are `${CODEX_HOME:-~/.codex}/sessions` and `archived_sessions`. Do not read auth/config credentials, SQLite, session indexes/history, arbitrary referenced paths or transcript/tool content as analytics metadata. Sources are not network integrations. Collection must reject symlink/traversal escape and return bounded counters rather than private paths in errors.

Use only explicit `session_meta.payload.id` as a Codex display ID, validated against the existing bounded ASCII-token contract; never derive a display ID from filenames. Explicit nested `source.subagent.thread_spawn` parent IDs and roles can establish child attribution. Unknown/internal/unlinked roles remain unknown; do not infer pstack from provider, path, role name or prompts. Model comes only from the preceding explicit `turn_context.model`; missing model and provider remain empty/unavailable independently. No inferred transcript title. User supplies project/workflow names when comparing.

New collector outputs are compatible aggregate `rows`, `stats`, `roles`, `sessions` structures for existing strict snapshot serialization, plus source-owned Codex capability/coverage metadata outside those allowlists. Session names can remain null. All tokens stay Python integers / decimal JSON strings / browser BigInt. Cost is not available from supported Codex token metadata: an internal compatibility zero must never be rendered as actual free usage or spend.

Only synthetic temporary fixtures are used during implementation and verification. Reading actual live Codex directories or changing installed services needs separate explicit authorization. No live preview output is published as part of this feature.

## Codex accounting contract

Primary references are upstream `openai/codex` protocol, Responses SSE mapping and session/history code, linked in `docs/codex-accounting.md` alongside exact supported assumptions and exclusions.

- `input_tokens` is inclusive input; `cached_input_tokens` is its cached-read subset. Current versions may also expose `cache_write_input_tokens` as an input subset. Disjoint normalized input = gross input minus cache reads minus cache writes. Reject inconsistent subsets rather than clamping away errors.
- `output_tokens` already includes reasoning output. Keep full output in the shared output field; never add reasoning a second time. A future reasoning breakdown must remain a subset, not extra total volume.
- Shared total is normalized input + cacheRead + cacheWrite + output = Codex gross input + gross output. No prices are multiplied into spend; totals are not billed units or quality scores.
- Repeated cumulative `total_token_usage` snapshots can be status/rate-limit rebroadcasts. Count validated positive cumulative deltas, not every `last_token_usage` repetition. Use per-component checks, not total-only monotonicity.
- Copies of the same thread must not be summed. Compatible prefix copies may contribute accepted extensions once; divergent histories must be disclosed/excluded rather than arbitrarily assigned. Distinct thread IDs with identical usage are distinct work.
- Forks can contain inherited parent history. Unless new-only work is proven by supported stored metadata, exclude fork accounting with an explicit warning rather than infer a baseline from a parent's final counter. Counter decreases or ambiguous initial baselines are coverage gaps, not permission to reset and recount.
- Event-local date attribution follows accepted timestamped token deltas, never filename dates. Missing source roots, malformed/unreadable records, future dates and incomplete attribution are explicit.

## Workspace flows and compatibility

Development: loopback-only Vite collects a new versioned workspace response from a dedicated additive route. It contains isolated Pi and Codex snapshots/capabilities, never merged global rows. Resolve Pi agent home from explicit configuration/current `PI_CODING_AGENT_DIR` for this new mode, preserving old CLI/default behavior. Keep the existing `/__pi_usage_snapshot` and `--dev-json` interfaces intact. Missing Codex can show an empty/unavailable source while Pi remains usable; hard collection errors must not become fabricated usage.

Offline: add an explicit workspace generation mode/flag using the existing private atomic writer and hashed CSP. Existing `--preview-react`, legacy default output and service/timer behavior stay unchanged. The React loader accepts old standalone v3/v4/v5 snapshots as Pi-only input and the new envelope as workspace input. A bad supported-version payload fails closed rather than guessing its source.

UI: top-level Pi/Gentle Shell, Codex and Compare links switch workspace scope. Existing `#/overview`, Companies, Agents, Sessions day links and Stats remain Pi routes. Codex has prefixed routes with working in-workspace detail links; changing source never silently retains another source's model/session filters. Source-specific missing costs and attribution limitations are explicit. Both data sets use the same core view components and their accessibility/responsive behavior.

## Comparison flow and state

User gives an experiment/project label and run/workflow labels, chooses source and measured session files, then captures each run. Each capture stores the selected summaries' observed metrics and snapshot as-of context, not live paths or transcript data. One selected summary index is counted once, even if clicked repeatedly; separate files sharing a displayed session ID must remain selectable separately. Children are explicit selections, never invisibly added again to a parent total.

Compare computes exact categories and totals and shows recorded model identities for each run. Explain that same task/model/settings/baseline and comparable cache conditions are needed to isolate harness effects. Different models still permit a setup comparison, not a claim that the harness caused the difference. Unavailable costs show Unavailable; no automatic quality or cost winner.

Versioned JSON export/import preserves the experiment without a backend. Strict nested allowlists, bounded labels/counts, decimal-string tokens and explicit sources prevent unsafe imports or precision loss. Invalid imports leave existing captures unchanged. Render labels as text; no eval, remote assets or untrusted HTML. Export/import user-requested files is explicit; browser persistence must not be a hidden requirement under file://.

## Failure, verification and delivery

Collector failure is source-scoped and sanitized. No retry daemon, queue, database, cloud service or mutation of source logs. Preserve the existing dev process timeout/output limits and same-origin/loopback restrictions. No new server write endpoint is required for comparisons.

Apply RED/GREEN tests to new accounting and comparison rules; keep tests with each behavior. Run legacy functional regressions and one differential check for shared CLI/parser behavior against the baseline using isolated temporary source data, never committed git-history tests. Check responsive keyboard interactions and file:// CSP with synthetic browser fixtures. High-risk source privacy/contracts receive one independent verifier after implementation checks; native review follows only provider-owned RDD assessment and consent.

Canonical feature requirements/progress: `odd/tasks/codex-usage-comparisons.md`. Accounting details: `docs/codex-accounting.md`. User commands and privacy limitations: README. Slice order: collector and comparison modules (independent), workspace integration, verified closure. `main` is not advanced automatically.
