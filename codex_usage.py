"""Metadata-only Codex rollout accounting, independent of Pi event parsing.

No default live root: callers must explicitly supply codex_home. See
``docs/codex-accounting.md`` for conservative provenance and coverage limits.
"""
from collections import defaultdict
from datetime import datetime, timezone, tzinfo
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from pi_usage import _directory_fd, _inventory, _safe_open, _unique_object, validated_session_id


_COVERAGE = (
    "taskFiles", "malformedTasks", "unreadableTasks", "invalidTaskPaths",
    "missingChildLinks", "duplicateTaskLinks", "sddTasks", "unknownRoleTasks",
    "childFiles", "linkedChildFiles", "excludedChildFiles", "unlinkedChildFiles",
    "conflictingChildFiles", "parentFiles", "parentFilesExcluded", "unsafePaths",
    "unreadableDirectories", "unavailableRoots", "conflictingEntries",
)
_COUNTERS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
             "output_tokens", "reasoning_output_tokens")
_WARNINGS = ("duplicateFiles", "conflictingThreads", "invalidCounters",
             "ambiguousInitialCounters", "forkedThreads", "unsupportedLineage",
             "invalidIdentity", "invalidTimestamps", "missingModels")


def _counter(value):
    if not isinstance(value, dict):
        raise ValueError("missing counters")
    # Cache-write is an additive protocol field: older layouts omit only it.
    values = tuple(value.get(key, 0 if key == "cache_write_input_tokens" else None)
                   for key in _COUNTERS)
    if any(type(item) is not int or item < 0 for item in values):
        raise ValueError("invalid counter")
    gross, read, write, output, reasoning = values
    if read + write > gross or reasoning > output:
        raise ValueError("inconsistent subsets")
    if "total_tokens" in value:
        total = value["total_tokens"]
        if type(total) is not int or total != gross + output:
            raise ValueError("inconsistent total")
    return values


def _metrics(delta):
    gross, read, write, output, reasoning = delta
    if min(delta) < 0 or read + write > gross or reasoning > output:
        raise ValueError("inconsistent delta")
    return dict(input=gross - read - write, cacheRead=read, cacheWrite=write,
                output=output, cost=0)


def _stamp(value):
    if not isinstance(value, str):
        raise ValueError("invalid timestamp")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("timestamp must be aware")
    return stamp


def _label(value):
    # Explicit model/provider metadata only, never path/name fallbacks.
    if not isinstance(value, str) or len(value) > 200 or any(ord(c) < 32 for c in value):
        return ""
    return value


def _load(path, stats, warnings):
    """Parse only supported metadata; retain no transcript payloads."""
    identity = None
    signature = []
    events = []
    model = ""
    provider = ""
    parent = role = None
    previous = None
    excluded = False
    try:
        with _safe_open(path) as stream:
            for number, line in enumerate(stream):
                try:
                    entry = json.loads(line, object_pairs_hook=_unique_object)
                    if not isinstance(entry, dict):
                        raise ValueError("invalid record")
                except (ValueError, UnicodeError, RecursionError):
                    stats["malformed"] += 1
                    continue
                kind = entry.get("type")
                payload = entry.get("payload")
                if not isinstance(payload, dict):
                    if kind in ("session_meta", "turn_context", "event_msg"):
                        stats["malformed"] += 1
                        excluded = True
                    continue
                if number == 0 and kind == "session_meta":
                    identity = validated_session_id(payload.get("id"))
                    provider = _label(payload.get("model_provider"))
                    if payload.get("forked_from_id") is not None:
                        warnings["forkedThreads"] += 1
                        excluded = True
                    source = payload.get("source")
                    if any(key in payload for key in ("parent_thread_id", "parent_id")):
                        warnings["unsupportedLineage"] += 1
                    if isinstance(source, dict):
                        subagent = source.get("subagent")
                        spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
                        if isinstance(spawn, dict):
                            parent = validated_session_id(spawn.get("parent_thread_id"))
                            candidate = validated_session_id(spawn.get("agent_role"))
                            if parent and candidate and not candidate.lower().startswith("sdd-"):
                                role = candidate
                            else:
                                warnings["unsupportedLineage"] += 1
                        else:
                            warnings["unsupportedLineage"] += 1
                    elif source not in (None, "cli", "vscode", "exec", "appServer"):
                        warnings["unsupportedLineage"] += 1
                    # Detect copy metadata conflicts without exposing parent IDs.
                    signature.append(("meta", parent, role, source if isinstance(source, str) else "nested", provider))
                elif kind == "session_meta":
                    excluded = True
                    warnings["invalidIdentity"] += 1
                elif kind == "turn_context":
                    model = _label(payload.get("model"))
                    signature.append(("model", model))
                elif kind == "event_msg" and payload.get("type") == "token_count":
                    info = payload.get("info")
                    if info is None:
                        continue  # rate-limit-only notification, no usage evidence
                    try:
                        if not isinstance(info, dict):
                            raise ValueError("missing info")
                        current = _counter(info.get("total_token_usage"))
                        if previous is None:
                            if any(current) and current != _counter(info.get("last_token_usage")):
                                warnings["ambiguousInitialCounters"] += 1
                                excluded = True
                            delta = current
                        else:
                            delta = tuple(a - b for a, b in zip(current, previous))
                        metrics = _metrics(delta)
                    except (ValueError, TypeError):
                        warnings["invalidCounters"] += 1
                        excluded = True
                        continue
                    previous = current
                    try:
                        stamp = _stamp(entry.get("timestamp"))
                    except (ValueError, OverflowError):
                        warnings["invalidTimestamps"] += 1
                        excluded = True
                        continue
                    signature.append(("usage", stamp.isoformat(), model, current))
                    if any(delta):
                        events.append((stamp, model, metrics))
    except OSError:
        stats["unreadable"] += 1
        return None
    if identity is None:
        warnings["invalidIdentity"] += 1
        return None
    return identity, signature, events, parent, role, excluded, provider


def collect_codex_usage(codex_home, now=None, tz=None):
    """Return Pi-compatible aggregates plus separate honest Codex capabilities.

    Only regular rollout*.jsonl files confined to sessions/archived_sessions are
    opened. Compatible prefix copies of an explicit thread ID count once. An
    unproven initial baseline, fork, reset or divergence quarantines that thread.
    The cost=0 compatibility placeholder is NOT measured spend; callers must
    preserve codex.costAvailable=False when presenting or comparing results.
    """
    now = datetime.now(timezone.utc) if now is None else now
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    if isinstance(tz, str):
        tz = ZoneInfo(tz)
    if tz is not None and not isinstance(tz, tzinfo):
        raise ValueError("tz must be a timezone")
    local_now = now.astimezone(tz)
    coverage = dict.fromkeys(_COVERAGE, 0)
    warnings = dict.fromkeys(_WARNINGS, 0)
    stats = dict(events=0, files=0, malformed=0, unreadable=0, future=0, outOfRange=0,
                 asOf=local_now.isoformat(), timezone=getattr(tz, "key", None) or local_now.tzname() or "local",
                 oldestDay=None, newestDay=None, coverage=coverage)
    home = Path(codex_home).absolute()
    roots = []
    files = []
    for name in ("sessions", "archived_sessions"):
        root = home / name
        try:
            with _directory_fd(root):
                state = "available"
        except FileNotFoundError:
            state = "missing"
        except OSError:
            state = "unavailable"
        roots.append(state)
        files.extend(path for path in _inventory(root, ".jsonl", True, coverage)
                     if path.name.startswith("rollout"))
    status = ("available" if "available" in roots else
              "missing" if all(state == "missing" for state in roots) else "unavailable")
    grouped = defaultdict(list)
    for path in files:
        stats["files"] += 1
        loaded = _load(path, stats, warnings)
        if loaded is not None:
            grouped[loaded[0]].append(loaded)
    accepted = []
    for identity, copies in sorted(grouped.items()):
        longest = max(copies, key=lambda item: len(item[1]))
        if any(copy[1] != longest[1][:len(copy[1])] for copy in copies):
            warnings["conflictingThreads"] += 1
            continue
        if any(copy[5] for copy in copies):
            continue
        warnings["duplicateFiles"] += len(copies) - 1
        accepted.append(longest)
    accepted_ids = {item[0] for item in accepted}
    totals = defaultdict(lambda: dict(input=0, cacheRead=0, cacheWrite=0, output=0, cost=0))
    sessions = []
    roles = set()
    for identity, _, events, parent, role, _, provider in accepted:
        if parent is not None:
            coverage["childFiles"] += 1
            if parent in accepted_ids and parent != identity and role:
                coverage["linkedChildFiles"] += 1
                roles.add(role)
            else:
                coverage["unlinkedChildFiles"] += 1
                warnings["unsupportedLineage"] += 1
                role = None
        else:
            coverage["parentFiles"] += 1
        session_totals = defaultdict(lambda: dict(input=0, cacheRead=0, cacheWrite=0, output=0, cost=0))
        for stamp, model, metrics in events:
            day = stamp.astimezone(tz).date().isoformat()
            for bucket in (totals[(day, provider, model, role)], session_totals[(day, provider, model)]):
                for key, value in metrics.items():
                    bucket[key] += value
            stats["events"] += 1
            stats["future"] += stamp > now
            stats["outOfRange"] += stamp.year < 2000
            warnings["missingModels"] += not bool(model)
        days = []
        for (day, provider, model), metrics in sorted(session_totals.items()):
            if not days or days[-1]["day"] != day:
                days.append(dict(day=day, models=[]))
            days[-1]["models"].append(dict(provider=provider, model=model, **metrics))
        if days:
            sessions.append(dict(startDay=days[0]["day"], endDay=days[-1]["day"],
                                 role=role, name=None, sessionId=identity, days=days))
    rows = [dict(day=day, provider=provider, model=model, role=role, **metrics)
            for (day, provider, model, role), metrics in sorted(totals.items(), key=lambda item: tuple(v or "" for v in item[0]))]
    if rows:
        stats.update(oldestDay=rows[0]["day"], newestDay=rows[-1]["day"])
    return dict(rows=rows, roles=sorted(roles), stats=stats, sessions=sessions,
                codex=dict(status=status, roots=dict(zip(("sessions", "archived_sessions"), roots)),
                           costAvailable=False, modelAvailable=any(row["model"] for row in rows),
                           providerAvailable=any(row["provider"] for row in rows), namesAvailable=False,
                           lineage="explicit-thread-spawn-only", accounting="validated-cumulative-deltas",
                           warnings=warnings))
