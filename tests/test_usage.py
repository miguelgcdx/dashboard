"""Deterministic, synthetic session fixtures for usage accounting."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pi_usage
from pi_usage import collect_combined_usage, collect_usage


UTC = timezone.utc
AS_OF = datetime(2026, 1, 2, 12, tzinfo=UTC)


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write_lines(self, name, *entries):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(entry) if not isinstance(entry, str) else entry
                                  for entry in entries) + "\n", encoding="utf-8")

    def collect(self, **kwargs):
        return collect_usage(self.root, now=AS_OF, tz=UTC, **kwargs)

    def test_message_and_top_level_usage_are_bucketed_by_event_not_file(self):
        ms = int(datetime(2026, 1, 1, 23, 30, tzinfo=UTC).timestamp() * 1000)
        usage = {"input": 10, "output": 3, "cacheRead": 4, "cacheWrite": 2,
                 "cost": {"total": 0.125, "input": 0.02}}
        self.write_lines("old-folder/a.jsonl",
                         {"type": "session", "timestamp": "2024-01-01T00:00:00Z"},
                         {"id": "a", "type": "message", "timestamp": "2024-01-01T00:00:00Z",
                          "message": {"role": "assistant", "timestamp": ms,
                                      "provider": "p", "model": "m", "usage": usage,
                                      "content": "PRIVATE RESPONSE"}},
                         {"id": "b", "type": "message", "timestamp": "2026-01-01T23:30:00Z",
                          "message": {"role": "toolResult", "provider": "p", "model": "m",
                                      "usage": {"output": 2}, "content": "PRIVATE TOOL"}},
                         {"id": "c", "type": "usage", "timestamp": "2026-01-01T23:30:00Z",
                          "provider": "q", "model": "n", "usage": {"input": 7}},
                         {"id": "d", "type": "compaction", "timestamp": "2026-01-01T23:30:00Z",
                          "provider": "q", "model": "n", "usage": {"cacheRead": 1}},
                         {"id": "e", "type": "branch_summary", "timestamp": "2026-01-01T23:30:00Z",
                          "provider": "q", "model": "n", "usage": {"cacheWrite": 5}})
        result = self.collect()
        self.assertEqual(result["rows"], [
            {"day": "2026-01-01", "provider": "p", "model": "m", "input": 10,
             "output": 5, "cacheRead": 4, "cacheWrite": 2, "cost": 0.125},
            {"day": "2026-01-01", "provider": "q", "model": "n", "input": 7,
             "output": 0, "cacheRead": 1, "cacheWrite": 5, "cost": 0},
        ])
        self.assertEqual(result["stats"], {
            "events": 5, "files": 1, "malformed": 0, "unreadable": 0,
            "future": 0, "outOfRange": 0,
            "asOf": "2026-01-02T12:00:00+00:00", "timezone": "UTC",
            "oldestDay": "2026-01-01", "newestDay": "2026-01-01",
        })
        serialized = json.dumps(result)
        self.assertNotIn("PRIVATE", serialized)
        self.assertNotIn("old-folder", serialized)
        self.assertNotIn("\"id\"", serialized)

    def test_clones_do_not_erase_identical_distinct_occurrences(self):
        event = {"id": "same", "type": "usage", "timestamp": "2026-01-01T00:00:00Z",
                 "provider": "p", "model": "m", "usage": {"input": 2}}
        no_id = {key: value for key, value in event.items() if key != "id"}
        self.write_lines("a.jsonl", event, event, no_id)
        self.write_lines("fork/b.jsonl", event, event, no_id)
        result = self.collect()
        self.assertEqual(result["stats"]["events"], 4)
        self.assertEqual(result["rows"][0]["input"], 8)

    def test_identity_includes_timestamp_role_provider_model_and_usage(self):
        base = {"id": "same", "type": "message", "timestamp": "2026-01-01T00:00:00Z",
                "message": {"role": "assistant", "provider": "p", "model": "m",
                            "usage": {"input": 1}}}
        import copy
        variants = [copy.deepcopy(base) for _ in range(5)]
        variants[1]["timestamp"] = "2026-01-01T01:00:00Z"
        variants[2]["message"]["role"] = "toolResult"
        variants[3]["message"]["provider"] = "other"
        variants[4]["message"]["usage"]["input"] = 2
        self.write_lines("a.jsonl", *variants)
        self.write_lines("b.jsonl", base)
        result = self.collect()
        self.assertEqual(result["stats"]["events"], 5)
        self.assertEqual(sum(row["input"] for row in result["rows"]), 6)

    def test_malformed_incomplete_and_irrelevant_lines(self):
        self.write_lines("mixed.jsonl", "{bad", [],
                         {"type": "usage", "timestamp": "not-a-date", "usage": {"input": 1}},
                         {"type": "usage", "timestamp": "2026-01-01T00:00:00Z",
                          "usage": {"input": -1}},
                         {"type": "usage", "timestamp": "2026-01-01T00:00:00Z"},
                         {"type": "message", "message": {"role": "user", "content": "PRIVATE"}},
                         {"type": "message", "message": {"role": "assistant"}},
                         {"type": "usage", "timestamp": "2026-01-01T00:00:00Z",
                          "usage": {"input": 0, "cost": 0}})
        result = self.collect()
        self.assertEqual(result["stats"]["malformed"], 5)
        self.assertEqual(result["stats"]["events"], 1)
        self.assertEqual(result["rows"][0]["input"], 0)
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_optional_metadata_without_usage_is_not_malformed(self):
        self.write_lines("optional.jsonl",
                         {"type": "compaction", "timestamp": "2026-01-01T00:00:00Z"},
                         {"type": "branch_summary", "timestamp": "2026-01-01T00:00:00Z"},
                         {"type": "usage", "timestamp": "2026-01-01T00:00:00Z"},
                         {"type": "compaction", "timestamp": "2026-01-01T00:00:00Z",
                          "usage": {"input": 4}})
        result = self.collect()
        self.assertEqual(result["stats"]["malformed"], 1)
        self.assertEqual(result["stats"]["events"], 1)
        self.assertEqual(result["rows"][0]["input"], 4)

    def test_huge_integer_token_count_and_invalid_float_cost(self):
        huge = 10 ** 400
        self.write_lines("numbers.jsonl",
                         {"type": "usage", "timestamp": "2026-01-01T00:00:00Z",
                          "usage": {"input": huge, "cacheRead": huge, "cost": 0.5}},
                         {"type": "usage", "timestamp": "2026-01-01T00:00:00Z",
                          "usage": {"input": 1, "cost": float("nan")}},
                         {"type": "usage", "timestamp": "2026-01-01T00:00:00Z",
                          "usage": {"input": 1, "cost": -0.25}})
        result = self.collect()
        self.assertEqual(result["stats"]["events"], 1)
        self.assertEqual(result["stats"]["malformed"], 2)
        self.assertEqual(result["rows"][0]["input"], huge)
        self.assertEqual(result["rows"][0]["cacheRead"], huge)
        self.assertEqual(result["rows"][0]["cost"], 0.5)

    def test_unreadable_files_are_skipped_without_partial_accounting(self):
        event = {"type": "usage", "timestamp": "2026-01-01T00:00:00Z",
                 "usage": {"input": 3}}
        self.write_lines("denied.jsonl", event)
        self.write_lines("interrupted.jsonl", event)
        self.write_lines("valid.jsonl", event)

        class InterruptedStream:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def __iter__(self):
                yield (json.dumps(event) + "\n").encode()
                raise OSError("private read error")

        original_open = Path.open

        def open_with_failures(path, *args, **kwargs):
            if path.name == "denied.jsonl":
                raise PermissionError("private path")
            if path.name == "interrupted.jsonl":
                return InterruptedStream()
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", open_with_failures):
            result = self.collect()
        self.assertEqual(result["stats"]["files"], 3)
        self.assertEqual(result["stats"]["unreadable"], 2)
        self.assertEqual(result["stats"]["events"], 1)
        self.assertEqual(result["rows"][0]["input"], 3)
        self.assertNotIn("private", json.dumps(result))

    def test_invalid_utf8_line_does_not_stop_other_files(self):
        (self.root / "bad.jsonl").write_bytes(b"\xff\n")
        self.write_lines("nested/good.jsonl", {"type": "usage",
                         "timestamp": "2026-01-01T00:00:00Z", "usage": {"input": 3}})
        result = self.collect()
        self.assertEqual(result["stats"]["files"], 2)
        self.assertEqual(result["stats"]["malformed"], 1)
        self.assertEqual(result["rows"][0]["input"], 3)

    def test_local_date_boundary_future_and_explicit_clock(self):
        self.write_lines("one.jsonl",
                         {"type": "usage", "timestamp": "2026-01-01T23:30:00Z",
                          "provider": "p", "model": "m", "usage": {"input": 1}},
                         {"type": "usage", "timestamp": "2026-01-03T00:00:00Z",
                          "provider": "p", "model": "m", "usage": {"input": 1}})
        result = collect_usage(self.root, now=AS_OF, tz=timezone(timedelta(hours=2)))
        self.assertEqual([row["day"] for row in result["rows"]],
                         ["2026-01-02", "2026-01-03"])
        self.assertEqual(result["stats"]["future"], 1)
        self.assertEqual(result["stats"]["asOf"], "2026-01-02T14:00:00+02:00")
        self.assertEqual(result["stats"]["oldestDay"], "2026-01-02")
        self.assertEqual(result["stats"]["newestDay"], "2026-01-03")

    def test_epoch_warning_and_nonfinite_cost(self):
        self.write_lines("a.jsonl",
                         {"type": "usage", "timestamp": 0, "usage": {"input": 1}},
                         {"type": "usage", "timestamp": "2026-01-01T00:00:00Z",
                          "usage": {"cost": float("inf")}})
        result = self.collect()
        self.assertEqual(result["stats"]["outOfRange"], 1)
        self.assertEqual(result["stats"]["malformed"], 1)
        self.assertEqual(result["stats"]["events"], 1)
        json.dumps(result, allow_nan=False)

    def test_empty_and_naive_clock(self):
        result = self.collect()
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["stats"]["oldestDay"], None)
        self.assertEqual(result["stats"]["newestDay"], None)
        with self.assertRaises(ValueError):
            collect_usage(self.root, now=datetime(2026, 1, 2))


class CombinedUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.parents = self.root / "parents"
        self.children = self.root / "children"
        self.tasks = self.root / "tasks"
        self.agents = self.root / "agents"
        for directory in (self.parents, self.children, self.tasks, self.agents):
            directory.mkdir()
        for role in ("worker", "explore", "unused", "sdd-apply", "SDD-verify"):
            (self.agents / (role + ".md")).write_text("PRIVATE ROLE INSTRUCTIONS")

    def event(self, identity, model="actual-model", provider="actual-provider", **metrics):
        return {"id": identity, "type": "message", "timestamp": "2026-01-01T23:30:00Z",
                "message": {"role": "assistant", "provider": provider, "model": model,
                            "content": "PRIVATE RESPONSE", "usage": metrics or {"input": 3}}}

    def session(self, directory, name, *entries):
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(entry) if not isinstance(entry, str) else entry
                                    for entry in entries) + "\n")
        return path

    def task(self, name, child, role="worker", **overrides):
        data = {"agent": role, "sessionPath": str(child), "tokens": 999999,
                "model": "NOT-THE-ACTUAL-MODEL", "prompt": "PRIVATE PROMPT",
                "result": "PRIVATE RESULT", "id": "PRIVATE TASK ID", "cwd": "PRIVATE CWD"}
        data.update(overrides)
        wrapper = {"task": data, "thread": [{"role": "user", "content": "PRIVATE THREAD"}]}
        (self.tasks / (name + ".json")).write_text(json.dumps(wrapper))

    def collect(self, **kwargs):
        options = dict(session_dir=self.parents, task_dir=self.tasks,
                       child_session_dir=self.children, agent_dir=self.agents, now=AS_OF, tz=UTC)
        options.update(kwargs)
        return collect_combined_usage(**options)

    def assert_session_contract(self, result):
        metrics = ("input", "output", "cacheRead", "cacheWrite", "cost")
        totals = {}
        for session in result["sessions"]:
            self.assertEqual(set(session), {"startDay", "endDay", "role", "name", "sessionId", "days"})
            if session["sessionId"] is not None:
                self.assertRegex(session["sessionId"], r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
            self.assertIn(session["role"], [None, *result["roles"]])
            if session["role"] is not None:
                self.assertIsNone(session["name"])
            if session["name"] is not None:
                self.assertIsInstance(session["name"], str)
                self.assertLessEqual(len(session["name"]), 200)
                self.assertEqual(session["name"], pi_usage.sanitize_session_name(session["name"]))
            days = session["days"]
            self.assertTrue(days)
            self.assertEqual(session["startDay"], days[0]["day"])
            self.assertEqual(session["endDay"], days[-1]["day"])
            self.assertEqual([day["day"] for day in days],
                             sorted({day["day"] for day in days}))
            for day in days:
                self.assertEqual(set(day), {"day", "models"})
                self.assertTrue(day["models"])
                labels = [(model["provider"], model["model"]) for model in day["models"]]
                self.assertEqual(labels, sorted(set(labels)))
                for model in day["models"]:
                    self.assertEqual(set(model), {"provider", "model", *metrics})
                    key = (day["day"], model["provider"], model["model"], session["role"])
                    bucket = totals.setdefault(key, dict.fromkeys(metrics, 0))
                    for metric in metrics:
                        if metric != "cost":
                            self.assertIs(type(model[metric]), int)
                        bucket[metric] += model[metric]
        expected = {(row["day"], row["provider"], row["model"], row["role"]):
                    {metric: row[metric] for metric in metrics} for row in result["rows"]}
        self.assertEqual(totals, expected)
        output = json.dumps(result, allow_nan=False)
        for forbidden in ("PRIVATE", str(self.root), "sessionPath", '"id"', '"cwd"',
                          '"title"', '"prompt"', '"messages"', '"thread"', '"path"',
                          '"filename"', "NOT-THE-ACTUAL-MODEL"):
            self.assertNotIn(forbidden, output)

    def test_session_id_comes_only_from_first_physical_header(self):
        header = {"type": "session", "id": "pi-parent_19.1",
                  "parentSession": "/PRIVATE_PARENT_PATH", "cwd": "/PRIVATE_CWD"}
        cases = [
            ([header], "pi-parent_19.1"),
            ([], None),
            (["", header], None),
            (["   ", header], None),
            (["{broken", header], None),
            (['{"type":"session","id":"first","id":"second"}', header], None),
            (['{"type":"message","type":"session","id":"ambiguous"}', header], None),
            ([[], header], None),
            ([{"type": "session_info", "id": "PRIVATE_INFO_ID"}, header], None),
            ([self.event("PRIVATE_EVENT_ID"), header], None),
            ([{"type": "session"}, header], None),
            ([{"type": "session", "id": None}, header], None),
            ([header, {"type": "session", "id": "late-header"}], "pi-parent_19.1"),
        ]
        for entries, expected in cases:
            with self.subTest(entries=entries):
                self.session(self.parents, "PRIVATE_FILENAME.jsonl", *entries,
                             {"type": "session_info", "id": "PRIVATE_INFO_ID", "name": "Named"},
                             self.event("PRIVATE_ACCEPTED_EVENT", input=0))
                result = self.collect()
                self.assertEqual(len(result["sessions"]), 1)
                self.assertEqual(result["sessions"][0]["sessionId"], expected)
                self.assertEqual(result["sessions"][0]["name"], "Named")
                self.assert_session_contract(result)
                legacy = collect_usage(self.parents, now=AS_OF, tz=UTC)
                self.assertEqual(set(legacy), {"rows", "stats"})
                self.assertNotIn("sessionId", json.dumps(legacy))
                self.assertNotIn("pi-parent_19.1", json.dumps(legacy))
                self.assertEqual(legacy["rows"], [
                    {key: value for key, value in row.items() if key != "role"}
                    for row in result["rows"]])

    def test_custom_header_ids_are_bounded_ascii_tokens_or_null(self):
        for value in ("a", "A0._-z", "a" * 128, "", "a" * 129, None, True, 19,
                      [], {}, "../private", "/private", "a/b", "a\\b", ".hidden",
                      "-start", "_start", "with space", " padded ", "a\n", "a\x00b",
                      "a\x7fb", "東京", "a\u202eb", "<script>", "a&b", "a:b"):
            valid = isinstance(value, str) and value in ("a", "A0._-z", "a" * 128)
            with self.subTest(value=value):
                self.session(self.parents, "one.jsonl", {"type": "session", "id": value},
                             self.event("PRIVATE_ENTRY"))
                result = self.collect()
                self.assertEqual(result["sessions"][0]["sessionId"], value if valid else None)
                self.assertEqual(result["stats"]["events"], 1)
                self.assertEqual(result["stats"]["malformed"], 0)
                self.assert_session_contract(result)

    def test_header_ids_stay_file_paired_through_dedup_and_child_conflicts(self):
        shared = self.event("PRIVATE_SHARED", input=7)
        conflict = self.event("PRIVATE_CONFLICT", input=999)
        for filename, identity, events in (
                ("a.jsonl", "owner-a", [shared]),
                ("b.jsonl", "dropped-copy", [shared]),
                ("c.jsonl", "shared-header", [shared, self.event("PRIVATE_C", input=11)]),
                ("d.jsonl", "empty-file", []),
                ("e.jsonl", "shared-header", [self.event("PRIVATE_E", input=13)])):
            self.session(self.parents, filename, {"type": "session", "id": identity},
                         {"type": "session_info", "name": filename}, *events)
        for role, identity, events in (
                ("worker", "shared-header", [shared, conflict, self.event("PRIVATE_ZERO", input=0)]),
                ("explore", "excluded-child", [shared, conflict])):
            child = self.session(self.children, role + ".jsonl",
                                 {"type": "session", "id": identity}, *events)
            self.task(role, child, role, id="PRIVATE_TASK_ID")
        result = self.collect()
        self.assertEqual(sorted((s["sessionId"], s["role"] or "parent",
                                 s["name"] or "unnamed", s["days"][0]["models"][0]["input"])
                                for s in result["sessions"]),
                         [("owner-a", "parent", "a.jsonl", 7),
                          ("shared-header", "parent", "c.jsonl", 11),
                          ("shared-header", "parent", "e.jsonl", 13),
                          ("shared-header", "worker", "unnamed", 0)])
        self.assertEqual(result["stats"]["events"], 4)
        self.assertEqual(result["stats"]["coverage"]["conflictingEntries"], 1)
        self.assert_session_contract(result)

    def test_explicit_parent_name_is_latest_single_line_unicode_and_not_in_rows(self):
        second = self.event("PRIVATE SECOND", input=7)
        second["timestamp"] = "2026-01-02T01:00:00Z"
        self.session(self.parents, "PRIVATE SOURCE.jsonl",
                     {"type": "session_info", "name": "Superseded"},
                     self.event("PRIVATE FIRST", input=3), second,
                     {"type": "session_info", "name": "  Café\n東京\t🚀\x00\x7f\x85\u202e Name\u2028done  "},
                     {"type": "session_info", "title": "PRIVATE TITLE"},
                     '{"type":"session_info","name":')
        result = self.collect()
        self.assertEqual(len(result["sessions"]), 1)
        summary = result["sessions"][0]
        self.assertEqual(summary["name"], "Café 東京 🚀 Name done")
        self.assertEqual([day["day"] for day in summary["days"]],
                         ["2026-01-01", "2026-01-02"])
        self.assertEqual([day["models"][0]["input"] for day in summary["days"]], [3, 7])
        self.assertEqual(result["stats"]["malformed"], 1)
        self.assertTrue(all("name" not in row for row in result["rows"]))
        self.assert_session_contract(result)
        legacy = collect_usage(self.parents, now=AS_OF, tz=UTC)
        self.assertEqual(set(legacy), {"rows", "stats"})
        self.assertNotIn('"name"', json.dumps(legacy))
        self.assertEqual([{key: value for key, value in row.items() if key != "role"}
                          for row in result["rows"]], legacy["rows"])
        self.assertEqual({key: value for key, value in result["stats"].items()
                          if key != "coverage"}, legacy["stats"])

    def test_blank_invalid_and_control_only_names_clear_without_fallback(self):
        for value in (None, False, 1, [], {}, "", " \n\t ", "\x00\u202e\ud800"):
            with self.subTest(value=value):
                self.session(self.parents, "one.jsonl",
                             {"type": "session_info", "name": "Old name"},
                             self.event("one"), {"type": "session_info", "name": value})
                result = self.collect()
                self.assertIsNone(result["sessions"][0]["name"])
                self.assertEqual(result["stats"]["events"], 1)
                self.assertEqual(result["stats"]["malformed"], 0)
                self.assert_session_contract(result)
        self.session(self.parents, "one.jsonl", self.event("one"),
                     {"type": "session_info", "name": "東京🚀" * 200})
        self.assertEqual(self.collect()["sessions"][0]["name"], ("東京🚀" * 200)[:200])

    def test_header_message_task_and_child_names_are_never_name_sources(self):
        self.session(self.parents, "PRIVATE PATH.jsonl",
                     {"type": "session", "name": "PRIVATE HEADER", "title": "PRIVATE TITLE",
                      "cwd": "PRIVATE CWD", "id": "PRIVATE ID"},
                     {"type": "message", "name": "PRIVATE MESSAGE NAME", "message": {
                         "role": "user", "content": "PRIVATE FIRST MESSAGE",
                         "type": "session_info", "name": "PRIVATE NESTED NAME"}},
                     {"type": "custom", "data": {"type": "session_info", "name": "PRIVATE CUSTOM"}},
                     self.event("PRIVATE PARENT"))
        child = self.session(self.children, "child.jsonl", self.event("PRIVATE CHILD"),
                             {"type": "session_info", "name": "PRIVATE CHILD NAME"})
        self.task("child", child, title="PRIVATE TASK TITLE")
        task_path = self.tasks / "child.json"
        wrapper = json.loads(task_path.read_text())
        wrapper["task"]["name"] = "PRIVATE TASK NAME"
        task_path.write_text(json.dumps(wrapper))
        result = self.collect()
        self.assertEqual(len(result["sessions"]), 2)
        self.assertTrue(all(session["name"] is None for session in result["sessions"]))
        self.assert_session_contract(result)

    def test_names_remain_paired_through_parent_priority_and_child_conflicts(self):
        shared = self.event("shared", input=7)
        partial = self.event("partial", input=11)
        conflict = self.event("conflict", input=999)
        for filename, name, entries in (
                ("a.jsonl", "Owner A", (shared,)),
                ("b.jsonl", "Dropped duplicate", (shared,)),
                ("c.jsonl", "Owner C", (shared, partial)),
                ("d.jsonl", "No usage", ()),
                ("e.jsonl", "Owner E", (partial, partial)),
                ("f.jsonl", None, (self.event("unnamed", input=13),))):
            self.session(self.parents, filename, *entries,
                         {"type": "session_info", "name": name})
        for role, entries in (("worker", (shared, conflict, self.event("unique", input=17))),
                              ("explore", (shared, conflict))):
            child = self.session(self.children, role + ".jsonl", *entries,
                                 {"type": "session_info", "name": "PRIVATE CHILD NAME"})
            self.task(role, child, role)
        result = self.collect()
        self.assertEqual({(session["role"], session["name"]):
                          session["days"][0]["models"][0]["input"]
                          for session in result["sessions"]},
                         {(None, "Owner A"): 7, (None, "Owner C"): 11,
                          (None, "Owner E"): 11, (None, None): 13, ("worker", None): 17})
        self.assertEqual(result["stats"]["events"], 5)
        self.assertEqual(result["stats"]["coverage"]["conflictingEntries"], 1)
        self.assert_session_contract(result)

    def test_named_file_with_partial_read_failure_exports_neither_name_nor_usage(self):
        denied = self.session(self.parents, "denied.jsonl", self.event("lost"))
        self.session(self.parents, "valid.jsonl", self.event("valid"))
        original = pi_usage._safe_open

        class InterruptedStream:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def __iter__(self):
                yield json.dumps({"type": "session", "id": "PRIVATE_LOST_HEADER"})
                yield json.dumps({"type": "session_info", "name": "PRIVATE LOST NAME"})
                yield json.dumps({"type": "usage", "timestamp": 0, "usage": {"input": 999}})
                raise OSError("PRIVATE READ ERROR")

        with patch.object(pi_usage, "_safe_open", side_effect=lambda path:
                          InterruptedStream() if path == denied else original(path)):
            result = self.collect()
        self.assertEqual(result["stats"]["unreadable"], 1)
        self.assertEqual(result["stats"]["events"], 1)
        self.assertEqual(len(result["sessions"]), 1)
        self.assertIsNone(result["sessions"][0]["name"])
        self.assert_session_contract(result)

    def test_anonymous_file_days_use_actual_models_and_reconcile_with_rows(self):
        huge = 10 ** 400
        first = self.event("PRIVATE FIRST", model="z", provider="z-provider",
                           input=huge, output=2, cacheRead=huge, cacheWrite=4, cost=0.125)
        first["timestamp"] = "2026-01-01T22:30:00Z"
        second = self.event("PRIVATE SECOND", model="b", provider="a-provider", input=5)
        second["message"]["timestamp"] = "2026-01-02T00:30:00Z"
        switched = self.event("PRIVATE SWITCH", model="a", provider="a-provider", output=7)
        switched["message"]["timestamp"] = "2026-01-02T01:30:00Z"
        child = self.session(self.children, "PRIVATE FILE.jsonl", switched, second, first,
                             {"type": "session", "timestamp": "2020-01-01T00:00:00Z",
                              "id": "PRIVATE SESSION", "title": "PRIVATE TITLE",
                              "cwd": "PRIVATE CWD"})
        self.task("PRIVATE TASK", child)
        self.session(self.parents, "PRIVATE PARENT.jsonl", self.event("PRIVATE PARENT", input=11))
        other = self.session(self.children, "PRIVATE OTHER.jsonl",
                             self.event("PRIVATE OTHER", input=13))
        self.task("other", other)
        result = self.collect(tz="Europe/Madrid")
        self.assertEqual(result["stats"]["events"], 5)
        self.assertEqual(len(result["sessions"]), 3)
        multi = result["sessions"][0]
        self.assertEqual(multi, {
            "startDay": "2026-01-01", "endDay": "2026-01-02", "role": "worker", "name": None,
            "sessionId": None, "days": [
                {"day": "2026-01-01", "models": [
                    {"provider": "z-provider", "model": "z", "input": huge, "output": 2,
                     "cacheRead": huge, "cacheWrite": 4, "cost": 0.125}]},
                {"day": "2026-01-02", "models": [
                    {"provider": "a-provider", "model": "a", "input": 0, "output": 7,
                     "cacheRead": 0, "cacheWrite": 0, "cost": 0},
                    {"provider": "a-provider", "model": "b", "input": 5, "output": 0,
                     "cacheRead": 0, "cacheWrite": 0, "cost": 0}]}]})
        self.assertEqual([session["role"] for session in result["sessions"]],
                         ["worker", None, "worker"])
        self.assertEqual(sum(day["day"] == "2026-01-02" for session in result["sessions"]
                             for day in session["days"]), 3)
        self.assert_session_contract(result)

    def test_summaries_follow_global_acceptance_and_within_file_multiplicity(self):
        shared = self.event("PRIVATE SHARED", input=7)
        repeated = self.event("PRIVATE REPEATED", input=5)
        conflict = self.event("PRIVATE CONFLICT", input=999)
        self.session(self.parents, "z-parent.jsonl", shared)
        for name, role, entries in (
                ("a", "worker", (shared, repeated, repeated, conflict)),
                ("b", "worker", (shared, repeated, repeated)),
                ("c", "worker", (repeated, repeated, repeated)),
                ("d", "explore", (shared, conflict))):
            child = self.session(self.children, name + ".jsonl", *entries)
            self.task(name, child, role)
        result = self.collect()
        self.assertEqual(result["stats"]["events"], 4)
        self.assertEqual(result["stats"]["coverage"]["conflictingEntries"], 1)
        self.assertEqual(len(result["sessions"]), 3)
        self.assertEqual(sorted((session["role"] or "parent",
                                 session["days"][0]["models"][0]["input"])
                                for session in result["sessions"]),
                         [("parent", 7), ("worker", 5), ("worker", 10)])
        self.assert_session_contract(result)

    def test_idless_events_with_repeated_header_ids_remain_separate_file_contributions(self):
        event = self.event("removed", input=2)
        del event["id"]
        header = {"type": "session", "id": "shared-header"}
        self.session(self.parents, "a.jsonl", header, event, event)
        self.session(self.parents, "b.jsonl", header, event, event)
        for role in ("worker", "explore"):
            child = self.session(self.children, role + ".jsonl", header, event, event)
            self.task(role, child, role)
        result = self.collect()
        self.assertEqual(result["stats"]["events"], 8)
        self.assertEqual(result["stats"]["coverage"]["conflictingEntries"], 0)
        self.assertEqual(len(result["sessions"]), 4)
        self.assertEqual(result["sessions"][0], result["sessions"][1])
        self.assertTrue(all(session["sessionId"] == "shared-header" for session in result["sessions"]))
        self.assertTrue(all(session["days"][0]["models"][0]["input"] == 4
                            for session in result["sessions"]))
        self.assert_session_contract(result)

    def test_empty_unreadable_and_zero_metric_sources(self):
        self.assertEqual(self.collect()["sessions"], [])
        self.session(self.parents, "empty.jsonl", {"type": "session", "id": "PRIVATE_EMPTY_ID"})
        self.session(self.parents, "malformed.jsonl", "PRIVATE BAD JSON")
        denied = self.session(self.parents, "denied.jsonl", self.event("PRIVATE DENIED"))
        self.session(self.parents, "zero.jsonl", {"type": "session", "id": "zero-accepted"},
                     self.event("PRIVATE ZERO", input=0))
        original = pi_usage._safe_open

        def open_with_failure(path):
            if path == denied:
                raise PermissionError("PRIVATE PATH")
            return original(path)

        with patch.object(pi_usage, "_safe_open", side_effect=open_with_failure):
            result = self.collect()
        self.assertEqual(result["stats"]["files"], 4)
        self.assertEqual(result["stats"]["events"], 1)
        self.assertEqual(result["stats"]["malformed"], 1)
        self.assertEqual(result["stats"]["unreadable"], 1)
        self.assertEqual(len(result["sessions"]), 1)
        self.assertEqual(result["sessions"][0]["days"][0]["models"][0]["input"], 0)
        self.assertEqual(result["sessions"][0]["sessionId"], "zero-accepted")
        self.assert_session_contract(result)

    def test_summary_sorting_uses_metadata_and_legacy_json_stays_unchanged(self):
        first = self.event("PRIVATE A", model="a", input=3)
        second = self.event("PRIVATE B", model="b", input=2)
        self.session(self.parents, "PRIVATE A.jsonl", second)
        self.session(self.parents, "PRIVATE B.jsonl", first)
        legacy = collect_usage(self.parents, now=AS_OF, tz=UTC)
        expected = {
            "rows": [
                {"day": "2026-01-01", "provider": "actual-provider", "model": "a",
                 "input": 3, "output": 0, "cacheRead": 0, "cacheWrite": 0, "cost": 0},
                {"day": "2026-01-01", "provider": "actual-provider", "model": "b",
                 "input": 2, "output": 0, "cacheRead": 0, "cacheWrite": 0, "cost": 0}],
            "stats": {"events": 2, "files": 2, "malformed": 0, "unreadable": 0,
                      "future": 0, "outOfRange": 0, "asOf": "2026-01-02T12:00:00+00:00",
                      "timezone": "UTC", "oldestDay": "2026-01-01", "newestDay": "2026-01-01"}}
        self.assertEqual(json.dumps(legacy), json.dumps(expected))
        before = self.collect()
        self.assertEqual([{key: value for key, value in row.items() if key != "role"}
                          for row in before["rows"]], legacy["rows"])
        self.assertEqual({key: value for key, value in before["stats"].items()
                          if key != "coverage"}, legacy["stats"])
        # Swap only synthetic fixture content; exported ordering must not use filenames.
        self.session(self.parents, "PRIVATE A.jsonl", first)
        self.session(self.parents, "PRIVATE B.jsonl", second)
        self.assertEqual(json.dumps(before), json.dumps(self.collect()))
        self.assertEqual(json.dumps(collect_usage(self.parents, now=AS_OF, tz=UTC)),
                         json.dumps(expected))
        self.assert_session_contract(before)

    def test_model_switch_roles_and_private_aggregate_schema(self):
        self.session(self.parents, "parent.jsonl", self.event("PRIVATE PARENT ID", input=7))
        child = self.session(self.children, "PRIVATE CHILD.jsonl",
                             self.event("PRIVATE CHILD ID", input=11, cacheRead=8, cost=0.25),
                             self.event("PRIVATE OTHER ID", model="second", provider="other",
                                        input=5, output=4, cacheWrite=2, cost={"total": 0.5}))
        self.task("PRIVATE TASK", child)
        other = self.session(self.children, "explorer.jsonl", self.event("explorer", input=2))
        self.task("explorer", other, "explore")
        result = self.collect()
        self.assertEqual(result["roles"], ["explore", "unused", "worker"])
        rows = {(row["role"], row["model"]): row for row in result["rows"]}
        self.assertEqual(rows[(None, "actual-model")]["input"], 7)
        self.assertEqual(rows[("worker", "actual-model")]["input"], 11)
        self.assertEqual(rows[("worker", "actual-model")]["cacheRead"], 8)
        self.assertEqual(rows[("worker", "second")], {
            "day": "2026-01-01", "provider": "other", "model": "second", "role": "worker",
            "input": 5, "output": 4, "cacheRead": 0, "cacheWrite": 2, "cost": 0.5})
        self.assertEqual(rows[("explore", "actual-model")]["input"], 2)
        for row in result["rows"]:
            self.assertEqual(set(row), {"day", "provider", "model", "role", "input",
                                        "output", "cacheRead", "cacheWrite", "cost"})
        output = json.dumps(result, allow_nan=False)
        for forbidden in ("PRIVATE", str(self.root), "sessionPath", "cwd", "prompt", "result",
                          "thread", "999999", "NOT-THE-ACTUAL-MODEL"):
            self.assertNotIn(forbidden, output)
        self.assertTrue(all(type(value) is int for value in result["stats"]["coverage"].values()))

    def test_duplicate_links_and_copied_entries_count_once_across_sources(self):
        shared = self.event("shared", input=7)
        child_event = self.event("child-only", input=5)
        self.session(self.parents, "parent.jsonl", shared)
        child = self.session(self.children, "a.jsonl", shared, child_event, child_event)
        copied = self.session(self.children, "b.jsonl", shared, child_event, child_event)
        self.task("a", child)
        self.task("duplicate", child)
        self.task("relative", "a.jsonl")
        self.task("copy", copied)
        result = self.collect()
        self.assertEqual(result["stats"]["events"], 3)
        self.assertEqual(result["stats"]["coverage"]["duplicateTaskLinks"], 2)
        self.assertEqual({row["role"]: row["input"] for row in result["rows"]},
                         {None: 7, "worker": 10})

    def test_conflicting_links_are_quarantined_before_any_child_read(self):
        for name, roles in (("two-roles", ("worker", "explore")),
                            ("sdd-conflict", ("worker", "sdd-apply")),
                            ("unknown-conflict", ("worker", "unconfigured")),
                            ("missing-role", ("worker", None))):
            child = self.session(self.children, name + ".jsonl", self.event(name))
            for index, role in enumerate(roles):
                self.task(name + str(index), child, role)
        with patch.object(pi_usage, "_safe_open", wraps=pi_usage._safe_open) as opened:
            result = self.collect()
        self.assertFalse(any(call.args[0].suffix == ".jsonl" for call in opened.call_args_list))
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["sessions"], [])
        coverage = result["stats"]["coverage"]
        self.assertEqual(coverage["conflictingChildFiles"], 3)
        self.assertEqual(coverage["excludedChildFiles"], 4)
        self.assertEqual(coverage["malformedTasks"], 1)

    def test_copies_with_different_roles_have_no_arbitrary_owner(self):
        event = self.event("copied")
        for role in ("worker", "explore"):
            child = self.session(self.children, role + ".jsonl", event)
            self.task(role, child, role)
        result = self.collect()
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["sessions"], [])
        self.assertEqual(result["stats"]["coverage"]["conflictingEntries"], 1)
        self.session(self.parents, "parent.jsonl", event)
        result = self.collect()
        self.assertEqual(result["stats"]["events"], 1)
        self.assertIsNone(result["rows"][0]["role"])
        self.assertEqual(result["stats"]["coverage"]["conflictingEntries"], 0)
        self.assertEqual(len(result["sessions"]), 1)
        self.assertIsNone(result["sessions"][0]["role"])
        self.assert_session_contract(result)

    def test_sdd_unknown_and_orphan_sessions_are_never_opened(self):
        orphan = self.session(self.children, "worker-looking-orphan.jsonl", "PRIVATE BAD JSON")
        for name in ("sdd-apply", "SDD-verify", "unconfigured"):
            child = self.session(self.children, name + ".jsonl", "PRIVATE BAD JSON")
            self.task(name, child, name)
        # Even a parent root encompassing children must not bypass attribution.
        with patch.object(pi_usage, "_safe_open", wraps=pi_usage._safe_open) as opened:
            result = self.collect(session_dir=self.root)
        self.assertFalse(any(call.args[0].suffix == ".jsonl" for call in opened.call_args_list))
        self.assertEqual(result["stats"]["malformed"], 0)
        self.assertEqual(result["sessions"], [])
        coverage = result["stats"]["coverage"]
        self.assertEqual(coverage["childFiles"], 4)
        self.assertEqual(coverage["excludedChildFiles"], 3)
        self.assertEqual(coverage["sddTasks"], 2)
        self.assertEqual(coverage["unlinkedChildFiles"], 1)
        self.assertEqual(coverage["parentFilesExcluded"], 4)
        self.assertNotIn(str(orphan), json.dumps(result))

    def test_missing_and_malformed_metadata_do_not_guess_roles(self):
        self.session(self.children, "worker.jsonl", self.event("unlinked"))
        (self.tasks / "broken.json").write_text("{unfinished")
        (self.tasks / "array.json").write_text("[]")
        (self.tasks / "duplicate-keys.json").write_text(
            '{"task":{"agent":"worker","agent":"explore",'
            '"sessionPath":"worker.jsonl"},"thread":["PRIVATE THREAD"]}')
        self.task("missing-file", self.children / "missing.jsonl")
        self.task("missing-path", None)
        result = self.collect()
        self.assertEqual(result["rows"], [])
        coverage = result["stats"]["coverage"]
        self.assertEqual(coverage["malformedTasks"], 3)
        self.assertEqual(coverage["missingChildLinks"], 1)
        self.assertEqual(coverage["invalidTaskPaths"], 1)
        self.assertEqual(coverage["unlinkedChildFiles"], 1)
        result = self.collect(task_dir=self.root / "missing-tasks")
        self.assertEqual(result["stats"]["coverage"]["unavailableRoots"], 1)
        self.assertEqual(result["stats"]["coverage"]["unlinkedChildFiles"], 1)

    def test_malformed_wrappers_never_fall_back_to_top_level_metadata(self):
        self.session(self.children, "child.jsonl", self.event("child", input=99))
        metadata = {"agent": "worker", "sessionPath": "child.jsonl"}
        wrappers = [[], None, metadata,
                    {**metadata, "task": None}, {**metadata, "task": []},
                    {**metadata, "task": "PRIVATE PROMPT"}]
        for index, wrapper in enumerate(wrappers):
            with self.subTest(wrapper=index):
                (self.tasks / "malformed.json").write_text(json.dumps(wrapper))
                with patch.object(pi_usage, "_safe_open", wraps=pi_usage._safe_open) as opened:
                    result = self.collect()
                self.assertEqual(result["rows"], [])
                self.assertEqual(result["stats"]["coverage"]["malformedTasks"], 1)
                self.assertFalse(any(call.args[0].suffix == ".jsonl"
                                     for call in opened.call_args_list))
                self.assertNotIn("PRIVATE", json.dumps(result))

    def test_nested_metadata_wins_over_top_level_and_thread_decoys(self):
        child = self.session(self.children, "child.jsonl", self.event("child", input=13))
        self.task("nested", child)
        path = self.tasks / "nested.json"
        wrapper = json.loads(path.read_text())
        wrapper.update(agent="sdd-apply", sessionPath="missing.jsonl")
        wrapper["thread"] = {"agent": "sdd-apply", "sessionPath": "missing.jsonl",
                             "content": "PRIVATE THREAD"}
        path.write_text(json.dumps(wrapper))
        result = self.collect()
        self.assertEqual(result["stats"]["coverage"]["linkedChildFiles"], 1)
        self.assertEqual(result["rows"][0]["role"], "worker")
        self.assertEqual(result["rows"][0]["input"], 13)
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertNotIn("thread", json.dumps(result))

    def test_duplicate_wrapper_and_nested_keys_fail_closed(self):
        self.session(self.children, "child.jsonl", self.event("child", input=99))
        metadata = '{"agent":"worker","sessionPath":"child.jsonl"}'
        wrappers = [
            '{"task":' + metadata + ',"task":' + metadata + ',"thread":[]}',
            '{"task":{"agent":"worker","agent":"worker",'
            '"sessionPath":"child.jsonl"},"thread":[]}',
            '{"task":{"agent":"worker","sessionPath":"child.jsonl",'
            '"sessionPath":"child.jsonl"},"thread":[]}',
            '{"task":' + metadata + ',"thread":[],"thread":["PRIVATE THREAD"]}',
        ]
        for index, wrapper in enumerate(wrappers):
            with self.subTest(wrapper=index):
                (self.tasks / "duplicate.json").write_text(wrapper)
                with patch.object(pi_usage, "_safe_open", wraps=pi_usage._safe_open) as opened:
                    result = self.collect()
                self.assertEqual(result["rows"], [])
                self.assertEqual(result["stats"]["coverage"]["malformedTasks"], 1)
                self.assertFalse(any(call.args[0].suffix == ".jsonl"
                                     for call in opened.call_args_list))
                self.assertNotIn("PRIVATE", json.dumps(result))

    def test_symlinks_and_traversal_cannot_escape_any_input_root(self):
        outside = self.session(self.root, "outside.jsonl", self.event("outside", input=999))
        (self.children / "linked.jsonl").symlink_to(outside)
        (self.children / "linked-dir").symlink_to(self.parents, target_is_directory=True)
        (self.parents / "external.jsonl").symlink_to(outside)
        (self.agents / "evil.md").symlink_to(self.agents / "worker.md")
        self.task("external", outside)
        self.task("traversal", "../outside.jsonl")
        self.task("normalized-traversal", "nested/../legit.jsonl")
        self.task("symlink", "linked.jsonl")
        self.task("directory-symlink", "linked-dir/external.jsonl")
        self.task("null", "bad\u0000.jsonl")
        (self.tasks / "linked.json").symlink_to(self.tasks / "external.json")
        result = self.collect()
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["sessions"], [])
        coverage = result["stats"]["coverage"]
        self.assertEqual(coverage["unsafePaths"], 5)
        self.assertEqual(coverage["invalidTaskPaths"], 4)
        self.assertEqual(coverage["missingChildLinks"], 2)
        self.assertNotIn("evil", result["roles"])
        alias = self.root / "alias"
        alias.symlink_to(self.children, target_is_directory=True)
        self.assertEqual(self.collect(child_session_dir=alias)["stats"]["coverage"]
                         ["unavailableRoots"], 1)
        self.assertEqual(self.collect(child_session_dir=alias / "nested")["stats"]["coverage"]
                         ["unavailableRoots"], 1)

    def test_symlink_swap_after_inventory_is_rejected_at_open(self):
        child = self.session(self.children, "child.jsonl", self.event("child"))
        outside = self.session(self.root, "outside.jsonl", self.event("outside", input=999))
        self.task("child", child)
        original = pi_usage._safe_open

        def swap(path):
            if path == child:
                # A synthetic race: replace a fixture, never a real session.
                path.rename(path.with_suffix(".saved"))
                path.symlink_to(outside)
            return original(path)

        with patch.object(pi_usage, "_safe_open", side_effect=swap):
            result = self.collect()
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["sessions"], [])
        self.assertEqual(result["stats"]["unreadable"], 1)

    def test_parent_api_stays_isolated_and_ignores_nested_tool_payload(self):
        event = self.event("parent", input=2)
        self.session(self.parents, "parent.jsonl", event,
                     {"type": "message", "message": {"role": "toolResult",
                      "toolName": "subagent_run", "content": [{"usage": {"input": 999}}]}})
        child = self.session(self.children, "child.jsonl", self.event("child", input=100))
        self.task("child", child)
        before = collect_usage(self.parents, now=AS_OF, tz=UTC)
        combined = self.collect()
        after = collect_usage(self.parents, now=AS_OF, tz=UTC)
        self.assertEqual(before, after)
        self.assertEqual(before["rows"][0]["input"], 2)
        self.assertEqual(set(before), {"rows", "stats"})
        self.assertNotIn("role", before["rows"][0])
        self.assertNotIn("coverage", before["stats"])
        self.assertEqual(sum(row["input"] for row in combined["rows"]), 102)

    def test_child_dates_warnings_and_partial_read_failure(self):
        future = self.event("future")
        future["timestamp"] = "2026-01-03T00:00:00Z"
        old = self.event("epoch")
        old["timestamp"] = 0
        child = self.session(self.children, "good.jsonl", self.event("good"), "{bad", future, old)
        with child.open("ab") as stream:
            stream.write(b"\xff\n")
        self.task("good", child)
        interrupted = self.session(self.children, "interrupted.jsonl", self.event("lost"))
        self.task("interrupted", interrupted)
        original = pi_usage._safe_open

        class InterruptedStream:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def __iter__(self):
                yield json.dumps({"type": "usage", "timestamp": 0, "usage": {"input": 900}})
                raise OSError("PRIVATE PATH")

        with patch.object(pi_usage, "_safe_open", side_effect=lambda path:
                          InterruptedStream() if path == interrupted else original(path)):
            result = self.collect(tz="Europe/Madrid")
        self.assertEqual(result["stats"]["malformed"], 2)
        self.assertEqual(result["stats"]["unreadable"], 1)
        self.assertEqual(result["stats"]["events"], 3)
        self.assertEqual(result["stats"]["future"], 1)
        self.assertEqual(result["stats"]["outOfRange"], 1)
        self.assertEqual(result["stats"]["timezone"], "Europe/Madrid")
        self.assertEqual(len(result["sessions"]), 1)
        self.assert_session_contract(result)
        self.assertEqual([row["day"] for row in result["rows"]],
                         ["1970-01-01", "2026-01-02", "2026-01-03"])
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_retained_task_cap_reports_unknown_historical_coverage(self):
        # Mirror the sanitized corpus shape, not any real session content.
        for index in range(322):
            child = self.session(self.children, f"child-{index}.jsonl",
                                 self.event(f"entry-{index}", input=1))
            if index < 199:
                self.task(f"task-{index}", child)
        self.task("duplicate", self.children / "child-0.jsonl")
        result = self.collect()
        coverage = result["stats"]["coverage"]
        self.assertEqual(coverage["taskFiles"], 200)
        self.assertEqual(coverage["childFiles"], 322)
        self.assertEqual(coverage["linkedChildFiles"], 199)
        self.assertEqual(coverage["unlinkedChildFiles"], 123)
        self.assertEqual(coverage["duplicateTaskLinks"], 1)
        self.assertEqual(result["stats"]["files"], 199)
        self.assertEqual(result["stats"]["events"], 199)
        self.assertEqual(result["rows"][0]["input"], 199)

    def test_empty_configuration_is_fail_closed(self):
        child = self.session(self.children, "child.jsonl", self.event("child"))
        self.task("child", child)
        result = self.collect(agent_dir=self.root / "missing-agents")
        self.assertEqual(result["roles"], [])
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["stats"]["coverage"]["unknownRoleTasks"], 1)
        self.assertEqual(result["stats"]["coverage"]["excludedChildFiles"], 1)


if __name__ == "__main__":
    unittest.main()
