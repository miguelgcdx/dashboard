# Codex rollout accounting

`codex_usage.collect_codex_usage(codex_home, now=None, tz=None)` collects local
metadata without writing files or opening live sources implicitly. It uses only
Python's standard library. This is a distinct format adapter, not a Pi log parser.
CLI/Desktop coverage exists only when those clients retain the supported rollout
metadata; it is not a claim of complete account history or billed usage.

## Supported source boundary

Only regular `rollout*.jsonl` files recursively inside the explicitly supplied
`codex_home/sessions` and `codex_home/archived_sessions` are read. No auth/config,
SQLite, index, history, arbitrary referenced path or transcript-title fallback is
used. Other JSONL filenames are ignored. The existing Pi descriptor-based
`_inventory`, `_directory_fd` and `_safe_open` primitives are reused: every
component is opened without following symlinks, files must still be regular at
open time, traversal components are rejected, and errors expose counters, not
paths. This requires the same POSIX/Linux filesystem support as those primitives.
A failed read discards that file's data.

`codex.status` is `available` if at least one rollout root can be opened (including
an empty directory), `missing` if both roots are absent, otherwise `unavailable`.
`codex.roots` reports each fixed root separately, so partial availability is not
silently treated as complete history. An existing home without either rollout
root still has missing source roots. Missing directories are never created.

## Exact supported records

The collector accepts JSON objects with unique keys. Only these fields affect
accounting; other transcript payloads are ignored and never exported:

- The **first physical line** must be `type: "session_meta"` with
  `payload.id` matching `[A-Za-z0-9][A-Za-z0-9._-]{0,127}`. No filename-derived
  identity, repaired ID or later replacement header is accepted. Another
  `session_meta` excludes the thread. ID-less files cannot be safely deduplicated
  and are excluded.
- `type: "turn_context"`, `payload.model`: a preceding explicit model label
  applies to subsequent token events. Missing/invalid labels remain `""`.
  Labels are bounded to 200 characters with no ASCII controls. Neither a model
  in session metadata nor a filename, prompt, provider or launch configuration
  supplies a model fallback.
- The first physical `session_meta.payload.model_provider` supplies an independent
  provider label, validated with the same 200-character/no-ASCII-controls bound.
  Missing, non-string or invalid values remain `""`; never infer a provider from
  the model or default it to OpenAI. The provider remains fixed across model
  switches, participates in copy-conflict detection, and appears in both rows
  and session model metrics. Literal HTML-like labels remain text; the strict
  snapshot serializer escapes them rather than interpreting them as markup.
- `type: "event_msg"`, `payload.type: "token_count"`, `payload.info` contains
  `total_token_usage` and optionally `last_token_usage`. `info: null`/absent
  is a rate-limit/status-only notification, not token evidence.
- Usage counters are `input_tokens`, `cached_input_tokens`, `output_tokens`,
  `reasoning_output_tokens`, and the additive `cache_write_input_tokens`.
  Only cache-write may be omitted, meaning zero for legacy layouts. Counts must
  be nonnegative Python integers, not booleans, floats, strings or null. Optional
  `total_tokens` must equal gross input plus output. Large integers remain exact;
  Python's JSON parser's standard integer digit limit remains in effect.
- Token event `timestamp` must be an aware ISO datetime. Attribution is by the
  accepted delta's local date in the requested timezone (IANA name or `tzinfo`),
  never by a filename. Default timezone uses the system's historical local
  conversion. Future and pre-2000 events are included with warning counters.
- `session_meta.payload.source.subagent.thread_spawn` may contain
  `parent_thread_id` and `agent_role`. A bounded ASCII-token non-`sdd-` role is
  attributed only when the explicit parent is also accepted in this collection
  and is not the child itself. No parent paths are followed. Other/unknown,
  missing-role or unlinked lineage is counted as unsupported; otherwise valid
  token usage remains under unknown role (`null`), not guessed to be a main
  session or a particular harness. Top-level `parent_thread_id`/`parent_id`
  metadata is disclosed as unsupported, not used as a linkage fallback.
  Parent IDs themselves are never exported.
- An explicit `session_meta.payload.forked_from_id` excludes the fork, even if
  its counters look like new work. Parent final totals are never a baseline.

## Counter provenance and disjoint metrics

Upstream totals are snapshots, not individual calls. For each thread, the first
nonzero snapshot is accepted **only if its entire counter vector equals a valid
`last_token_usage` vector**. This is evidence of one observed usage update, not
permission to attribute a pre-existing accumulated history. An all-zero first
snapshot establishes zero directly. An unequal initial total/last vector excludes
the entire thread with `ambiguousInitialCounters`; missing/invalid first last
counters exclude it with `invalidCounters`. No later delta is rescued from an
unproven baseline. These rules deliberately undercount truncated/resumed histories
whose first retained total exceeds their last update. Equality does not prove
absence of an unmarked inherited history; unsupported upstream versions/forks
remain a coverage limitation.

After the first snapshot, compute deltas **per component** from the preceding
validated cumulative snapshot. Do not sum `last_token_usage`, reset after a
decrease, or check only `total_tokens`. A repeated snapshot contributes zero,
even if it has a later timestamp or a repeated nonzero `last_token_usage`.
A negative component, inconsistent subset, invalid count or invalid token-event
timestamp quarantines the entire thread, including previously pending usage.
This deliberately excludes resets/non-monotonic revisions instead of claiming
that all upstream versions are monotonic.

For each accepted delta:

```text
input      = delta(input_tokens) - delta(cached_input_tokens)
                                - delta(cache_write_input_tokens)
cacheRead  = delta(cached_input_tokens)
cacheWrite = delta(cache_write_input_tokens)
output     = delta(output_tokens)
```

Cache read + write must not exceed gross input, for both snapshots and deltas.
Reasoning must not exceed output, for snapshots and deltas; reasoning is already
inside output and is **not added again**. The disjoint sum is exactly gross input
plus output. No rates or prices are used. Shared `cost: 0` is solely a serializer
compatibility placeholder: **`codex.costAvailable` is always false**. Consumers
must carry that capability separately and display Unavailable, never free usage
or actual spend. The existing `dashboard.react_snapshot_json` accepts the base
shape but intentionally drops the separate `codex` metadata; workspace integration
must preserve it in its own envelope.

## Copies, children and partial coverage

Files group only by explicit thread ID. The accounting signatures consist of
relevant header lineage and provider, model-context records and timestamped cumulative token
snapshots. Identical copies and exact accounting-prefix copies contribute the
longest compatible history once. Distinct IDs with identical counters remain
separate work. Conflicting metadata or divergent histories exclude the whole
thread (`conflictingThreads`), not an arbitrary winning copy. An excluded copy
also prevents acceptance of its compatible thread. Benign differences in context
or rebroadcast ordering can therefore cause conservative exclusion.

Each accepted thread has its own measured summary. Parent and spawned child
usage are disjoint; selecting a parent does not implicitly select/add its child.
No parent aggregate embeds child totals. A linked child's role does not identify
its workflow/harness: project and workflow labels must be user-supplied.

Malformed JSON, duplicate keys, partial trailing lines and non-object records
increment `stats.malformed` and are skipped. Supported records with invalid
payloads quarantine the file's thread. A parseable invalid token record is not
silently converted to zero. Complete valid records before a truncated non-JSON
line can still be measured: this is retained-metadata coverage, not proof that
no omitted usage exists. Unknown schemas/lineage and unmarked forks cannot be
reconstructed from prompts, paths or parent final totals.

All strict Pi `stats.coverage` keys exist. Pi-specific task/link counters are zero
when not applicable. `parentFiles`, `childFiles`, `linkedChildFiles` and
`unlinkedChildFiles` describe accepted deduplicated Codex threads; `stats.files`
is the observed rollout file count. `unsafePaths`, `unavailableRoots` and
`unreadableDirectories` come from the shared safe inventory. Separate
`codex.warnings` reports duplicate copies, conflicting threads, invalid counters,
ambiguous initial baselines, explicit forks, unsupported lineage, invalid IDs,
invalid timestamps and accepted events without models. Counts are observations,
not estimates of missing token volume.

## Public result and privacy

The result has `rows`, `roles`, `stats`, `sessions` and `codex`. Rows carry
`day`, `provider`, `model`, `role`, `input`, `cacheRead`, `cacheWrite`, `output`,
`cost`. Sessions carry `startDay`, `endDay`, `role`, `name: null`, `sessionId`
and `days`, with day-specific model metrics. Summaries mirror accepted rows;
they must never be added again to aggregate totals. Explicit thread IDs are
private/correlatable display metadata, not user-entered experiment labels.
No cwd, source path, prompt, response, tool content or inferred session name is
exported. No source logs are modified.

For example, gross input 100, cached read 20, cache write 10 and output 30
(including reasoning 5) produce `input: 70, cacheRead: 20, cacheWrite: 10,
output: 30, cost: 0` plus `codex.costAvailable: false`.

## Integration metadata contract

The complete top-level result is `{rows, roles, stats, sessions, codex}`.
`codex` is a sibling of the Pi-compatible aggregates, not part of `stats` and
not a field added to individual rows or session summaries. Its exact shape is:

```json
{
  "status": "available",
  "roots": {
    "sessions": "available",
    "archived_sessions": "missing"
  },
  "costAvailable": false,
  "modelAvailable": true,
  "providerAvailable": true,
  "namesAvailable": false,
  "lineage": "explicit-thread-spawn-only",
  "accounting": "validated-cumulative-deltas",
  "warnings": {
    "duplicateFiles": 0,
    "conflictingThreads": 0,
    "invalidCounters": 0,
    "ambiguousInitialCounters": 0,
    "forkedThreads": 0,
    "unsupportedLineage": 0,
    "invalidIdentity": 0,
    "invalidTimestamps": 0,
    "missingModels": 0
  }
}
```

Every shown key is always present. Status values are exactly `"available"`,
`"missing"`, or `"unavailable"`, both for the source and each fixed root.
An available source means at least one root opens, **not** that usage exists,
all files are readable, or all roots are available. Missing means both roots
are absent; unavailable covers other cases where neither root opens.
`roots` contains fixed labels only, never absolute source paths.

`modelAvailable` and `providerAvailable` are independent booleans indicating
that at least one accepted row has a nonempty explicit model or provider,
respectively; neither promises complete attribution for every row. Both are
false for an empty result. `costAvailable` and `namesAvailable` are always false
in this supported adapter. `lineage` and `accounting` are the exact literal strings shown above,
not inferred workflow labels or completeness claims.

All warning values are nonnegative integer observation counts:

| Warning key | Observation counted |
| --- | --- |
| `duplicateFiles` | Extra compatible copies of accepted threads. |
| `conflictingThreads` | Thread IDs with incompatible accounting signatures. |
| `invalidCounters` | Token records with invalid/missing counters, inconsistent totals/subsets or invalid deltas. |
| `ambiguousInitialCounters` | Nonzero first snapshots with valid but unequal total/last vectors. |
| `forkedThreads` | Files explicitly marked with non-null `forked_from_id`; accounting is excluded without a parent-total baseline. |
| `unsupportedLineage` | Unsupported source/parent/role observations or unresolved accepted child links. |
| `invalidIdentity` | Missing/invalid first-header identities or later session headers. |
| `invalidTimestamps` | Token records without valid aware timestamps. |
| `missingModels` | Accepted nonzero delta events lacking an explicit model. |

Warnings can overlap, and file-level observations can repeat across copied
files; do not sum warnings as an excluded-thread count. A zero warning count
does not establish complete history. Malformed JSON, read failures and date
warnings remain in `stats`, not duplicated into new Codex warning keys.

`stats.coverage` always retains all 19 required Pi-compatible keys, including
when the source is missing or empty:

```text
taskFiles malformedTasks unreadableTasks invalidTaskPaths
missingChildLinks duplicateTaskLinks sddTasks unknownRoleTasks
childFiles linkedChildFiles excludedChildFiles unlinkedChildFiles
conflictingChildFiles parentFiles parentFilesExcluded unsafePaths
unreadableDirectories unavailableRoots conflictingEntries
```

The workspace bridge must carry `codex` metadata separately from the strict
snapshot serialization and identify its parsed snapshot with source `"codex"`.
The comparison component can continue consuming parsed `Snapshot` values with
source `"pi" | "codex"`; it has no direct collector dependency. In particular,
never infer cost availability from the shared numeric cost placeholder.

## Primary references and verification

These are upstream main-branch contracts, not a pinned promise of support for
all historical Codex releases. Tests pin the adapter's exact supported layouts
above rather than treating current-main research as universal monotonicity.

- [Protocol: rollout records, SessionMeta, SessionSource, TokenUsage and TokenUsageInfo](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/protocol.rs)
  ([raw source](https://raw.githubusercontent.com/openai/codex/main/codex-rs/protocol/src/protocol.rs)).
- [Responses SSE usage mapping](https://github.com/openai/codex/blob/main/codex-rs/codex-api/src/sse/responses.rs).
- [Session state and cumulative accounting](https://github.com/openai/codex/blob/main/codex-rs/core/src/state/session.rs).
- [Token usage replay](https://github.com/openai/codex/blob/main/codex-rs/core/src/token_usage_replay.rs).
- [Rollout storage](https://github.com/openai/codex/tree/main/codex-rs/core/src/rollout).

Run isolated fixtures without touching live Codex sources:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_codex_usage.py' -v
```

Fixtures cover current nonzero cache writes and the legacy missing-write layout,
reasoning subsets, exact huge integers, initial zero/equality/ambiguity, component
decreases and invalid deltas, repeated rebroadcasts, model/date switches, copy and
prefix deduplication, divergent copies, identical distinct threads, forks, nested
parent/child roles, unknown lineage, invalid IDs, partial/corrupt records, source
availability, symlink confinement, privacy sentinels and strict snapshot acceptance.
Provider fixtures additionally cover explicit labels (including HTML-like text
and the 200-character boundary), invalid/missing providers without model-based
fallbacks, provider-copy conflicts, model switches preserving independent
providers, and mixed provider availability.
