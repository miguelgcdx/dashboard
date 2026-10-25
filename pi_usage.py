"""Aggregate Pi usage with opted-in private names/header IDs, never message content."""

from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone, tzinfo
import json
import math
import os
from pathlib import Path
import re
import stat
import unicodedata
from zoneinfo import ZoneInfo


_METRICS = ("input", "output", "cacheRead", "cacheWrite", "cost")
SESSION_NAME_LIMIT = 200


def validated_session_id(value):
    """Keep an exact private header token or return None, never repair an ID.

    Pi supports custom IDs; this export deliberately accepts only 1–128 ASCII
    characters matching [A-Za-z0-9][A-Za-z0-9._-]*. Paths, whitespace, controls,
    markup and unusual custom IDs are unavailable rather than inferred/truncated.
    This token is display metadata, NOT measured-file or usage-event identity.
    """
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        return value
    return None


def sanitize_session_name(value):
    """Bound explicit private display text; never infer a name or its author.

    Replace controls (including bidi formatting), lone surrogates and whitespace
    with single spaces. Keep Unicode text, not markup encoding; the snapshot
    serializer handles script-safe JSON and the UI must render this as text.
    """
    if not isinstance(value, str):
        return None
    cleaned = "".join(" " if unicodedata.category(char) in ("Cc", "Cf", "Cs")
                      else char for char in value)
    return " ".join(cleaned.split())[:SESSION_NAME_LIMIT].strip() or None


def _timestamp(value):
    if isinstance(value, bool):
        raise ValueError("invalid timestamp")
    try:
        if isinstance(value, (int, float)):
            # Pi message timestamps are epoch milliseconds.
            return datetime.fromtimestamp(value / 1000, timezone.utc)
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError("invalid timestamp") from exc
    raise ValueError("invalid timestamp")


def _metrics(usage):
    if not isinstance(usage, dict) or not any(key in usage for key in _METRICS):
        raise ValueError("missing usage")
    values = {}
    for key in _METRICS:
        value = usage.get(key, 0)
        if key == "cost" and isinstance(value, dict):
            value = value.get("total", 0)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("invalid usage")
        if key != "cost" and not isinstance(value, int):
            raise ValueError("invalid token count")
        if value < 0 or (isinstance(value, float) and not math.isfinite(value)):
            raise ValueError("invalid usage")
        values[key] = value
    return values


def _event(entry):
    kind = entry.get("type")
    if kind == "message":
        message = entry.get("message")
        if not isinstance(message, dict) or message.get("role") not in ("assistant", "toolResult"):
            return None
        source = message
        role = message["role"]
    elif kind in ("usage", "compaction", "branch_summary"):
        source = entry
        role = ""
    else:
        return None
    if "usage" not in source:
        # Message and optional metadata entries without usage are not events.
        # An explicit top-level usage event must have usage.
        if kind != "usage":
            return None
        raise ValueError("missing usage")
    metrics = _metrics(source["usage"])
    stamp = _timestamp(source.get("timestamp", entry.get("timestamp")))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    provider = source.get("provider", entry.get("provider", ""))
    model = source.get("model", entry.get("model", ""))
    if not isinstance(provider, str) or not isinstance(model, str):
        raise ValueError("invalid provider or model")
    # The identity is deliberately metadata-only. Without an entry ID, identical
    # payloads may be distinct events and are never deduplicated.
    identity = None
    if isinstance(entry.get("id"), str) and entry["id"]:
        identity = (entry["id"], stamp.astimezone(timezone.utc).isoformat(), kind,
                    role, provider, model, tuple(metrics[key] for key in _METRICS))
    return stamp, provider, model, metrics, identity


def collect_usage(session_dir, now=None, tz=None):
    """Collect usage recursively under session_dir, without child discovery.

    Rows are sorted by local day, provider and model. ``now`` must be aware;
    ``tz`` accepts a tzinfo or IANA name, defaulting to local historical offsets.
    Future and pre-2000 events are included with warning counts.
    """
    sources = ((path, None) for path in sorted(Path(session_dir).rglob("*.jsonl"))
               if not path.is_symlink() and path.is_file())
    return _collect_sources(sources, lambda path: path.open("rb"), now, tz)


def _collect_sources(sources, opener, now, tz, combined=False, coverage=None):
    """Aggregate metadata-only events; source order gives parents precedence.

    Combined summaries describe measured session FILES with attributed accepted
    usage, NOT unique logical conversations. They are parallel metadata, never
    added into global rows/totals. Only the opted-in validated first-line header
    ID can identify a session; never export entry IDs or source paths.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    if isinstance(tz, str):
        tz = ZoneInfo(tz)
    if tz is not None and not isinstance(tz, tzinfo):
        raise ValueError("tz must be a timezone")
    local_now = now.astimezone(tz)
    label = getattr(tz, "key", None) or local_now.tzname() or "local"
    stats = {"events": 0, "files": 0, "malformed": 0, "unreadable": 0, "future": 0,
             "outOfRange": 0,
             "asOf": local_now.isoformat(), "timezone": label,
             "oldestDay": None, "newestDay": None}
    totals = defaultdict(lambda: {metric: 0 for metric in _METRICS})
    loaded = []
    owners = defaultdict(set)
    for path, agent in sources:
        stats["files"] += 1
        pending = []
        name = None
        session_id = None
        malformed = 0
        try:
            with opener(path) as stream:
                for line_number, line in enumerate(stream):
                    try:
                        entry = json.loads(line)
                        if not isinstance(entry, dict):
                            raise ValueError("invalid entry")
                        # Physical first line only; later headers never repair it.
                        # Reject ambiguous header keys without changing accounting.
                        if combined and line_number == 0 and entry.get("type") == "session":
                            try:
                                header = json.loads(line, object_pairs_hook=_unique_object)
                                session_id = validated_session_id(header.get("id"))
                            except (ValueError, TypeError, UnicodeError, RecursionError):
                                pass
                        # Only explicit parent metadata opts into private naming.
                        # Missing name is not an update; blank/invalid clears it.
                        if (combined and agent is None and
                                entry.get("type") == "session_info" and "name" in entry):
                            name = sanitize_session_name(entry["name"])
                        event = _event(entry)
                    except (ValueError, TypeError, UnicodeError, RecursionError):
                        malformed += 1
                        continue
                    if event is not None:
                        pending.append(event)
        except OSError:
            # Reading can fail even after opening; discard that file entirely.
            stats["unreadable"] += 1
            continue
        stats["malformed"] += malformed
        # Keep metadata paired with this file when dedup drops some/all events.
        # Never zip separate metadata lists or deduplicate by session header ID.
        loaded.append((agent, name, session_id, pending))
        if combined:
            for *_, identity in pending:
                if identity is not None:
                    owners[identity].add(agent)
    # Parent evidence wins over copied child entries. Copies attributed to
    # different child roles have no defensible owner and are excluded.
    conflicts = {identity for identity, roles in owners.items()
                 if None not in roles and len(roles) > 1}
    if combined:
        coverage["conflictingEntries"] = len(conflicts)
    # Preserve within-file multiplicity and the maximum across copied files.
    seen = Counter()
    sessions = [] if combined else None
    for agent, name, session_id, pending in loaded:
        session_totals = defaultdict(lambda: {metric: 0 for metric in _METRICS}) if combined else None
        in_file = Counter()
        for stamp, provider, model, metrics, identity in pending:
            if identity is not None:
                if identity in conflicts:
                    continue
                in_file[identity] += 1
                if in_file[identity] <= seen[identity]:
                    continue
                seen[identity] = in_file[identity]
            day = (stamp.astimezone(tz) if tz is not None else
                   datetime.fromtimestamp(stamp.timestamp())).date().isoformat()
            key = (day, provider, model, agent) if combined else (day, provider, model)
            bucket = totals[key]
            for metric in _METRICS:
                bucket[metric] += metrics[metric]
            if combined:
                session_bucket = session_totals[(day, provider, model)]
                for metric in _METRICS:
                    session_bucket[metric] += metrics[metric]
            stats["events"] += 1
            stats["future"] += stamp > now
            # An epoch/default timestamp is suspect, not a reliable
            # historical Pi session date; keep it visible with a warning.
            stats["outOfRange"] += stamp.year < 2000
        if combined and session_totals:
            days = []
            for (day, provider, model), metrics in sorted(session_totals.items()):
                if not days or days[-1]["day"] != day:
                    days.append({"day": day, "models": []})
                days[-1]["models"].append({"provider": provider, "model": model, **metrics})
            sessions.append({"startDay": days[0]["day"], "endDay": days[-1]["day"],
                             "role": agent, "name": name, "sessionId": session_id, "days": days})
    rows = []
    for key, metrics in sorted(totals.items(), key=lambda item: tuple(
            value or "" for value in item[0])):
        day, provider, model = key[:3]
        row = {"day": day, "provider": provider, "model": model, **metrics}
        if combined:
            row["role"] = key[3]
        rows.append(row)
    if rows:
        stats["oldestDay"] = rows[0]["day"]
        stats["newestDay"] = rows[-1]["day"]
    result = {"rows": rows, "stats": stats}
    if combined:
        # Sort by exported metadata only; the header ID is not a unique file key.
        result["sessions"] = sorted(sessions, key=lambda session: (
            session["startDay"], session["endDay"], session["role"] or "",
            json.dumps(session["days"], sort_keys=True), session["name"] or "",
            session["sessionId"] or ""))
    return result


@contextmanager
def _directory_fd(path):
    """Pin every directory component without following symbolic links (Linux)."""
    path = Path(path).absolute()
    if ".." in path.parts:
        raise OSError("unsafe directory")
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _safe_open(path):
    with _directory_fd(path.parent) as directory:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=directory)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError("not a regular file")
            stream = os.fdopen(fd, "rb")
        except BaseException:
            os.close(fd)
            raise
        with stream:
            yield stream


def _inventory(root, suffix, recursive, coverage):
    """List regular files without traversing symlinks or exporting paths."""
    files = []

    def walk(directory, relative):
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    name = relative / entry.name
                    if entry.is_symlink():
                        coverage["unsafePaths"] += 1
                    elif entry.is_dir(follow_symlinks=False):
                        if recursive:
                            try:
                                child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY |
                                                os.O_NOFOLLOW, dir_fd=directory)
                            except OSError:
                                coverage["unreadableDirectories"] += 1
                                continue
                            try:
                                walk(child, name)
                            finally:
                                os.close(child)
                    elif entry.is_file(follow_symlinks=False) and name.suffix == suffix:
                        files.append(root / name)
        except OSError:
            coverage["unreadableDirectories"] += 1

    try:
        with _directory_fd(root) as directory:
            walk(directory, Path())
    except OSError:
        coverage["unavailableRoots"] += 1
    return sorted(files)


def _child_path(value, root):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("invalid child path")
    path = Path(value)
    if ".." in path.parts or path.suffix != ".jsonl":
        raise ValueError("invalid child path")
    path = path if path.is_absolute() else root / path
    if not path.is_relative_to(root):
        raise ValueError("unconfined child path")
    return path


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate metadata key")
        result[key] = value
    return result


def collect_combined_usage(session_dir, task_dir=None, child_session_dir=None,
                           agent_dir=None, now=None, tz=None):
    """Combine parent Pi usage with safely linked, named non-SDD child usage.

    Task JSON wrappers supply only ``task.agent`` and ``task.sessionPath``;
    prompt and thread content is never used or exported. Paths may be absolute
    or relative to child_session_dir. Role names are the stems of
    regular agents/*.md files (contents are never read). No role or model is
    inferred from session names, task tokens, launch models, or message text.
    Parent rows have role=None; child rows use the configured role name.
    ``roles`` includes unused non-SDD names. Rows expose only day/provider/model/
    role and metrics; stats/coverage contain fixed counters, dates and timezone.
    ``sessions`` describes measured session FILES with attributed accepted usage,
    NOT unique logical conversations. Each summary has startDay, endDay, role,
    name, sessionId and days containing day-specific actual provider/model metrics.
    sessionId is the validated id from the first physical type:session header,
    or None when absent/invalid. Both parent and safely linked child headers are
    eligible; repeated IDs never merge files or affect usage deduplication. Only
    explicit parent session_info.name is retained as bounded private text; the
    latest explicit field wins (blank/invalid clears). This does not establish
    human authorship. Child and unnamed summaries have name=None, with no fallback.
    Files with no accepted events are omitted. Summaries are parallel metadata,
    never added into global rows/totals.

    Conflicting roles (including excluded/invalid roles) quarantine a child.
    Parent entries win cross-source deduplication; copies with conflicting child
    roles are excluded. As in collect_usage, entries without IDs cannot safely
    be deduplicated. Symlink components and traversal are rejected at open time.
    Coverage is observed retained-metadata coverage, NOT historical completeness:
    unlinked children are never read, even if task history has been pruned.
    """
    home = Path.home() / ".pi" / "agent"
    parents = Path(session_dir).absolute()
    tasks = Path(task_dir if task_dir is not None else home / "gentle-agents/tasks").absolute()
    children = Path(child_session_dir if child_session_dir is not None else
                    home / "gentle-agents/sessions").absolute()
    agents = Path(agent_dir if agent_dir is not None else home / "agents").absolute()
    coverage = dict.fromkeys((
        "taskFiles", "malformedTasks", "unreadableTasks", "invalidTaskPaths",
        "missingChildLinks", "duplicateTaskLinks", "sddTasks", "unknownRoleTasks",
        "childFiles", "linkedChildFiles", "excludedChildFiles", "unlinkedChildFiles",
        "conflictingChildFiles", "parentFiles", "parentFilesExcluded",
        "unsafePaths", "unreadableDirectories", "unavailableRoots", "conflictingEntries",
    ), 0)
    roles = sorted(path.stem for path in _inventory(agents, ".md", False, coverage)
                   if not path.stem.lower().startswith("sdd-"))
    role_set = set(roles)
    child_files = set(_inventory(children, ".jsonl", True, coverage))
    coverage["childFiles"] = len(child_files)
    links = defaultdict(set)
    invalid = set()
    for path in _inventory(tasks, ".json", False, coverage):
        coverage["taskFiles"] += 1
        try:
            with _safe_open(path) as stream:
                wrapper = json.load(stream, object_pairs_hook=_unique_object)
        except (ValueError, UnicodeError, RecursionError):
            coverage["malformedTasks"] += 1
            continue
        except OSError:
            coverage["unreadableTasks"] += 1
            continue
        if not isinstance(wrapper, dict) or not isinstance(wrapper.get("task"), dict):
            coverage["malformedTasks"] += 1
            continue
        task = wrapper["task"]
        try:
            child = _child_path(task.get("sessionPath"), children)
        except ValueError:
            coverage["invalidTaskPaths"] += 1
            continue
        if child not in child_files:
            coverage["missingChildLinks"] += 1
            continue
        role = task.get("agent")
        if not isinstance(role, str) or not role:
            coverage["malformedTasks"] += 1
            invalid.add(child)
            links[child]  # Record that this is linked, but not attributable.
            continue
        if role in links[child]:
            coverage["duplicateTaskLinks"] += 1
        links[child].add(role)
        if role.lower().startswith("sdd-"):
            coverage["sddTasks"] += 1
        elif role not in role_set:
            coverage["unknownRoleTasks"] += 1
    selected = []
    for child, names in sorted(links.items()):
        if len(names) > 1:
            coverage["conflictingChildFiles"] += 1
        if child in invalid or len(names) != 1 or not names <= role_set:
            coverage["excludedChildFiles"] += 1
        else:
            selected.append((child, next(iter(names))))
    coverage["linkedChildFiles"] = len(selected)
    coverage["unlinkedChildFiles"] = len(child_files - links.keys())
    parent_files = _inventory(parents, ".jsonl", True, coverage)
    # An overly broad parent root must not bypass child attribution rules.
    safe_parents = [path for path in parent_files if not path.is_relative_to(children)]
    coverage["parentFilesExcluded"] = len(parent_files) - len(safe_parents)
    coverage["parentFiles"] = len(safe_parents)
    result = _collect_sources([(path, None) for path in safe_parents] + selected,
                              _safe_open, now, tz, combined=True, coverage=coverage)
    result["roles"] = roles
    result["stats"]["coverage"] = coverage
    return result
