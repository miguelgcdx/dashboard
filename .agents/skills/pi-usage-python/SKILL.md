---
name: pi-usage-python
description: "Trigger: Python parser, JSONL attribution, offline dashboard, unittest. Preserve Pi usage privacy, accounting, and preview isolation."
license: Apache-2.0
metadata:
  author: pi-usage-dashboard
  version: "1.0"
---

## Activation Contract

Load for this project's Python collection, child attribution, HTML snapshot bridge, or unittest changes. Use existing React/TypeScript skills for UI work.

## Hard Rules

- Use standard-library Python and metadata only. Never export prompts, responses, tool arguments, threads, task/entry IDs, cwd, or source paths. By explicit user opt-in, ONLY the private React v5 measured-file summaries may carry sanitized `session_info.name` from parent files and validated `id` from the first physical `type:session` header of accepted parent/linked-child files. These Pi names/IDs are not file identities and may be sensitive or correlatable; never derive them from prompts, task data, filenames, or event IDs. Keep the legacy output, usage rows, and logs free of these fields.
- Link children only through nested `task.agent`/`task.sessionPath` under the confined child root. Reject traversal and symlink components at open time. Exclude SDD, unknown, unlinked, and conflicting roles fail-closed; derive configured roles from filenames, never file contents.
- Attribute provider/model and dates to actual usage events, never launch models, task totals, session headers, or filenames. Do not recount nested usage subsets.
- Preserve identity-based copy deduplication, within-file multiplicity, parent precedence, and conflicting-child exclusion. Never deduplicate ID-less events by guesswork.
- Keep token counts as Python integers and decimal strings across JSON to browser BigInt; reject invalid/nonfinite metrics. Keep cost separate. Compute cache share as `cacheRead / (input + cacheRead + cacheWrite)`; show N/A at zero.
- Export only versioned nested-allowlist usage JSON with the two user-approved private name/header-ID fields above; escape script-breaking characters. Keep self-contained offline HTML, restrictive CSP, hashed executable scripts and preview styles, no network assets/listener, and private atomic publication (owned 0700 parent, 0600 file). Never chmod an existing unsafe parent.
- Preserve explicit as-of/timezone, event-local dates, Monday weeks, coverage gaps, and malformed/unreadable/future/out-of-range warnings. Never equate retained linkage with complete history.
- Keep legacy collection/rendering and timer unchanged by default. Require `--preview-react` with a separate explicit output; fail before replacement when preview assets are unavailable.

## Decision Gates

| Change | Boundary |
| --- | --- |
| Parsing/linkage/accounting | `pi_usage.py`; preserve both collection APIs. |
| HTML/CLI/publication | `dashboard.py`; preserve legacy/preview isolation. |
| Proposed Python split | Require a demonstrated feature seam, not file size. |

## Execution Steps

1. Read the feature task, affected module, and matching tests below.
2. Build deterministic synthetic `unittest` fixtures in self-cleaning `TemporaryDirectory`; inject aware `now`, timezone, and all input roots. Never read live sessions or activate timers. Do not introduce pytest.
3. Exercise privacy sentinels, hostile labels, symlink/traversal rejection, duplicates, model switches, large integers, and empty/partial coverage as applicable.
4. From the project root, run `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q`. Report browser/build-dependent skips honestly; do not build or migrate live output without authorization.

## Output Contract

Return changed paths, preserved contracts, exact test results/skips, and live-state effects. Report TDD only when explicitly configured; never invent RED/GREEN evidence.

## References

- [Feature tasks](../../../odd/tasks/pi-usage-dashboard.md)
- [Parser](../../../pi_usage.py) and [parser tests](../../../tests/test_usage.py)
- [Renderer](../../../dashboard.py) and [renderer tests](../../../tests/test_dashboard.py)
- [Preview bridge tests](../../../tests/test_react_bridge.py)
- [Timer contract tests](../../../tests/test_units.py)
