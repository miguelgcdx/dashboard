# PStack Pi comparison and P-Agents

## Outcome

Extend the private Pi usage dashboard so the user can compare two isolated Pi workflows (Gentle AI and the `michael-denyer/pstack-claude` Pi port) and inspect individual port agents in a separate `P-Agents` route. Preserve existing `Agents`, legacy `index.html`/timer, loopback-only development and private offline preview. Report, separately, how the port follows or diverges from original Cursor PStack; token usage is not parity evidence.

## Authorization and scope

- User approved targeting the *Pi port* for the comparison, not an installation, Ghostty edits, publishing, migration, push, PR, merge or commit. Original upstream is `cursor/plugins/pstack`; the chosen port is a distinct implementation with declared policy forks.
- No guessed historical provenance from terminal, model/provider, roles, names, IDs, paths, prompts or output. Project, workflow, run and agent instance are independent dimensions; preserve explicit `Unknown` and attribution coverage. Never export source paths, cwd, transcript content, tool arguments, secrets or PStack record descriptions/output.
- Pi-port `pstack-agents` custom entries explicitly record agent ID, type, child Pi session ID and lifecycle; an agent ID may be reused on resume and is not a unique run ID. Optional project/workflow/run tags need an explicit emitter and strict versioned validation; the dashboard must not pretend that Pi-port entries already supply them.
- Pi's documented assistant, tool-result and top-level usage categories are separate call work, but parent tool-result usage may conditionally overlap a linked child's usage. Exact entry identity does not prove call containment. Do not silently drop events or claim proven non-overlap; characterize with synthetic fixtures and label any remaining uncertainty.
- Recorded Pi cost is not a paid invoice or an API-reference estimate. No outcome-quality or elapsed-time metric without new evidence. Fair experiment comparisons require same task, model, thinking level and starting code, with fresh sessions and separate cold/warm cache trials.

## Tasks

- [x] Map the candidate port, Pi metadata seam, dashboard privacy boundary and conditional accounting risk. Evidence: read-only upstream/local explorations; chosen port has explicit `pstack-agents` agent and child session IDs, Pi supports non-context `custom` entries. No installation or file changes in this step.
- [ ] Characterize usage semantics with deterministic tests before any new combined comparison totals. Include a parent tool-result and a linked child with distinct event IDs and equal work; preserve additive assistant/tool-result/top-level summary calls and exact-entry dedup. Distinguish demonstrable duplication from ambiguous equal metrics. Checks: focused unittest plus independent review of the accounting claim.
- [ ] Define a private allowlisted attribution contract: source provenance, versioned explicit project/workflow/run labels where emitted, Pi-port agent-instance and child session linkage, conflicts/unknown/coverage. Keep per-file identities separate from conversations, safe path confinement, Python integer / browser BigInt tokens, and unchanged legacy output. Checks: hostile/malformed metadata and privacy boundary tests.
- [ ] Add workflow comparison and `P-Agents` route in reviewable UI slices; preserve `Agents`, existing date/provider/model/Sessions links, accessible honest empty states and file:// CSP. Checks: typecheck/build, focused component/browser tests, recorded-vs-unavailable cost labels and no invented attribution.
- [ ] Independently verify collector/UI/privacy/accounting and the opted-in private preview only if separately authorized; keep preview timer paused and `.verified` revoked before any preview-affecting source edit until build attestation. Do not alter the legacy index/timer or install the port.
- [ ] Produce a read-only, source-linked parity assessment of chosen Pi port against original upstream (tracked v0.15.5 versus newer v0.15.6), distinguishing mirrored capabilities, policy forks and unknown runtime behavior. This cannot be inferred from tokens or costs.

## Status

Target port selected; first implementation step is accounting characterization. Fidelity research is in progress. No port installation, source edits, preview publication, commits or Ghostty changes. The preview timer is currently active and the prior asset marker attested; stop the timer and revoke `web/dist/.verified` before editing preview-affecting source. Existing repository policy says no commit without explicit user request.
