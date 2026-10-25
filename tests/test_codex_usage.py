"""Synthetic Codex rollout contracts; never accesses live Codex state."""
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from codex_usage import collect_codex_usage
from dashboard import react_snapshot_json

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def meta(identity="thread-1", **extra):
    return {"type": "session_meta", "payload": {"id": identity, "source": "cli", **extra}}


def context(model="explicit-model"):
    return {"type": "turn_context", "payload": {"model": model}}


def counts(i=100, read=20, write=10, out=30, reasoning=5):
    return dict(input_tokens=i, cached_input_tokens=read,
                cache_write_input_tokens=write, output_tokens=out,
                reasoning_output_tokens=reasoning, total_tokens=i + out)


def usage(total=None, last=None, stamp="2026-05-01T01:00:00Z"):
    total = counts() if total is None else total
    return {"timestamp": stamp, "type": "event_msg", "payload": {
        "type": "token_count", "info": {"total_token_usage": total,
                                          "last_token_usage": total if last is None else last}}}


class CodexUsageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "codex"
        self.home.mkdir()
        (self.home / "sessions").mkdir()
        (self.home / "archived_sessions").mkdir()

    def write(self, name, records, root="sessions"):
        path = self.home / root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
        return path

    def collect(self, **kwargs):
        return collect_codex_usage(self.home, now=NOW, tz="UTC", **kwargs)

    def test_disjoint_nonzero_writes_reasoning_and_bridge(self):
        self.write("rollout-a.jsonl", [meta(), context(), usage()])
        result = self.collect()
        row, = result["rows"]
        self.assertEqual({k: row[k] for k in ("input", "cacheRead", "cacheWrite", "output", "cost")},
                         dict(input=70, cacheRead=20, cacheWrite=10, output=30, cost=0))
        self.assertEqual(row["provider"], "")
        self.assertFalse(result["codex"]["costAvailable"])
        self.assertEqual(result["codex"]["status"], "available")
        snapshot = json.loads(react_snapshot_json(result))
        self.assertEqual(snapshot["sessions"][0]["sessionId"], "thread-1")
        self.assertEqual(snapshot["rows"][0]["cacheWrite"], "10")

    def test_explicit_provider_is_bounded_text_independent_of_model(self):
        for provider in ("recorded-provider", "<script>alert('label')</script>", "p" * 200):
            with self.subTest(provider=provider):
                self.write("rollout-a.jsonl", [meta(model_provider=provider), usage()])
                result = self.collect()
                row, = result["rows"]
                self.assertEqual(row["provider"], provider)
                self.assertEqual(row["model"], "")
                self.assertTrue(result["codex"]["providerAvailable"])
                self.assertFalse(result["codex"]["modelAvailable"])
                snapshot_text = react_snapshot_json(result)
                snapshot = json.loads(snapshot_text)
                self.assertEqual(snapshot["rows"][0]["provider"], provider)
                self.assertEqual(snapshot["sessions"][0]["days"][0]["models"][0]["provider"], provider)
                self.assertNotIn("<script>", snapshot_text)

    def test_missing_or_invalid_provider_never_inferred_from_model(self):
        for extra in ({}, {"model_provider": None}, {"model_provider": True},
                      {"model_provider": 123}, {"model_provider": "p" * 201},
                      {"model_provider": "unsafe\nprovider"}):
            with self.subTest(extra=extra):
                self.write("rollout-a.jsonl", [meta(**extra), context("openai/gpt-explicit"), usage()])
                result = self.collect()
                self.assertEqual(result["rows"][0]["provider"], "")
                self.assertEqual(result["rows"][0]["model"], "openai/gpt-explicit")
                self.assertFalse(result["codex"]["providerAvailable"])
                self.assertEqual(result["sessions"][0]["days"][0]["models"][0]["provider"], "")

    def test_provider_copy_conflict_quarantines_thread(self):
        self.write("rollout-a.jsonl", [meta(model_provider="provider-a"), context(), usage()])
        self.write("rollout-b.jsonl", [meta(model_provider="provider-b"), context(), usage()])
        result = self.collect()
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["sessions"], [])
        self.assertEqual(result["codex"]["warnings"]["conflictingThreads"], 1)
        self.assertFalse(result["codex"]["providerAvailable"])

    def test_model_switch_preserves_independent_header_provider(self):
        self.write("rollout-a.jsonl", [meta(model_provider="explicit-provider"),
                   context("first-model"), usage(), context("other/model"), usage(counts(200))])
        self.write("rollout-b.jsonl", [meta("other-thread"), context("first-model"), usage()])
        result = self.collect()
        self.assertEqual([(r["provider"], r["model"], r["input"]) for r in result["rows"]],
                         [("", "first-model", 70), ("explicit-provider", "first-model", 70),
                          ("explicit-provider", "other/model", 100)])
        self.assertTrue(result["codex"]["providerAvailable"])
        session = next(s for s in result["sessions"] if s["sessionId"] == "thread-1")
        self.assertEqual([(m["provider"], m["model"]) for m in session["days"][0]["models"]],
                         [("explicit-provider", "first-model"), ("explicit-provider", "other/model")])

    def test_copy_prefix_dedup_and_identical_distinct_ids(self):
        prefix = [meta(), context(), usage()]
        self.write("rollout-a.jsonl", prefix)
        self.write("rollout-copy.jsonl", prefix, root="archived_sessions")
        self.write("rollout-long.jsonl", prefix + [usage(counts(150, 30, 15, 40))])
        self.write("rollout-other.jsonl", [meta("thread-2"), context(), usage()])
        result = self.collect()
        self.assertEqual(sum(r["input"] + r["cacheRead"] + r["cacheWrite"] + r["output"] for r in result["rows"]), 320)
        self.assertEqual(len(result["sessions"]), 2)
        self.assertEqual(result["codex"]["warnings"]["duplicateFiles"], 2)

    def test_divergent_copies_excluded_not_arbitrarily_selected(self):
        self.write("rollout-a.jsonl", [meta(), context(), usage()])
        self.write("rollout-b.jsonl", [meta(), context(), usage(counts(101))])
        result = self.collect()
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["codex"]["warnings"]["conflictingThreads"], 1)

    def test_model_switch_dates_rebroadcast_component_deltas(self):
        self.write("rollout-a.jsonl", [meta(), context("first"), usage(), usage(),
                   context("second"), usage(counts(150, 40, 15, 50), stamp="2026-05-02T23:30:00Z"),
                   usage(counts(150, 40, 15, 50), stamp="2026-05-03T01:00:00Z")])
        result = collect_codex_usage(self.home, now=NOW, tz="Europe/Madrid")
        self.assertEqual([(r["day"], r["model"], r["input"], r["cacheRead"], r["cacheWrite"], r["output"]) for r in result["rows"]],
                         [("2026-05-01", "first", 70, 20, 10, 30), ("2026-05-03", "second", 25, 20, 5, 20)])
        self.assertEqual(result["stats"]["events"], 2)

    def test_invalid_counts_and_subsets_exclude_thread(self):
        for bad in (-1, True, 1.5, "100", None):
            with self.subTest(bad=bad):
                invalid = counts(); invalid["input_tokens"] = bad
                self.write("rollout-a.jsonl", [meta(), context(), usage(invalid)])
                result = self.collect()
                self.assertEqual(result["rows"], [])
                self.assertGreater(result["codex"]["warnings"]["invalidCounters"], 0)
        for invalid in (counts(10), counts(reasoning=31), dict(counts(), total_tokens=999)):
            self.write("rollout-a.jsonl", [meta(), context(), usage(invalid)])
            self.assertEqual(self.collect()["rows"], [])

    def test_huge_integers_exact(self):
        huge = 10 ** 100
        self.write("rollout-a.jsonl", [meta(), context(), usage(counts(huge, 0, 0, huge, 0))])
        result = self.collect()
        self.assertEqual(result["rows"][0]["input"], huge)
        self.assertEqual(json.loads(react_snapshot_json(result))["rows"][0]["output"], str(huge))

    def test_initial_baseline_requires_zero_or_total_equal_last(self):
        self.write("rollout-a.jsonl", [meta(), context(), usage(counts(), counts(50, 10, 5, 15))])
        result = self.collect()
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["codex"]["warnings"]["ambiguousInitialCounters"], 1)
        self.write("rollout-a.jsonl", [meta(), context(), usage(counts(0, 0, 0, 0, 0)), usage()])
        self.assertEqual(self.collect()["rows"][0]["input"], 70)
        record = usage(); del record["payload"]["info"]["last_token_usage"]
        self.write("rollout-a.jsonl", [meta(), record])
        self.assertEqual(self.collect()["rows"], [])

    def test_descending_and_inconsistent_delta_not_reset(self):
        for second in (counts(90, 20, 10, 50), counts(110, 40, 10, 40)):
            self.write("rollout-a.jsonl", [meta(), context(), usage(), usage(second)])
            result = self.collect()
            self.assertEqual(result["rows"], [])
            self.assertEqual(result["codex"]["warnings"]["invalidCounters"], 1)

    def test_forks_excluded_without_parent_final_baseline_guess(self):
        self.write("rollout-parent.jsonl", [meta("parent"), context(), usage(counts(500))])
        self.write("rollout-child.jsonl", [meta("fork", forked_from_id="parent"), context(), usage(), usage(counts(200))])
        result = self.collect()
        self.assertEqual([s["sessionId"] for s in result["sessions"]], ["parent"])
        self.assertEqual(result["codex"]["warnings"]["forkedThreads"], 1)

    def test_nested_parent_child_once_and_unsupported_lineage(self):
        child = meta("child", source={"subagent": {"thread_spawn": {
            "parent_thread_id": "parent", "depth": 1, "agent_role": "reviewer"}}})
        self.write("rollout-parent.jsonl", [meta("parent"), context(), usage()])
        self.write("rollout-child.jsonl", [child, context(), usage()])
        self.write("rollout-copy.jsonl", [child, context(), usage()])
        self.write("rollout-unknown.jsonl", [meta("unknown", source={"subagent": "review"}), usage()])
        result = self.collect()
        self.assertEqual(result["roles"], ["reviewer"])
        self.assertEqual(len(result["sessions"]), 3)
        self.assertEqual(sum(r["output"] for r in result["rows"]), 90)
        self.assertEqual(result["codex"]["warnings"]["unsupportedLineage"], 1)
        self.assertEqual(result["stats"]["coverage"]["linkedChildFiles"], 1)

    def test_unsupported_top_level_parent_metadata_is_disclosed(self):
        self.write("rollout-a.jsonl", [meta(parent_thread_id="unproven-parent"), usage()])
        result = self.collect()
        self.assertEqual(result["codex"]["warnings"]["unsupportedLineage"], 1)
        self.assertIsNone(result["sessions"][0]["role"])
        self.assertNotIn("unproven-parent", json.dumps(result))

    def test_collection_preserves_all_source_bytes_and_directory_entries(self):
        self.write("rollout-a.jsonl", [meta(), context(), usage()])
        before = {str(p.relative_to(self.home)): p.read_bytes()
                  for p in self.home.rglob("*") if p.is_file()}
        self.collect()
        after = {str(p.relative_to(self.home)): p.read_bytes()
                 for p in self.home.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_partial_corrupt_records_unknown_model_and_privacy(self):
        path = self.write("rollout-a.jsonl", [meta(cwd="PRIVATE-CWD", model="NOT-A-MODEL", model_provider="recorded-provider"),
                          {"type": "response_item", "payload": {"text": "PRIVATE-PROMPT"}}, usage()])
        with path.open("ab") as stream:
            stream.write(b'{bad\n{"type":')
        result = self.collect()
        self.assertEqual(result["stats"]["malformed"], 2)
        self.assertEqual(result["rows"][0]["provider"], "recorded-provider")
        self.assertEqual(result["rows"][0]["model"], "")
        self.assertTrue(result["codex"]["providerAvailable"])
        self.assertFalse(result["codex"]["modelAvailable"])
        exported = json.dumps(result)
        for private in ("PRIVATE", "NOT-A", str(self.home), "cwd", "parent_thread_id"):
            self.assertNotIn(private, exported)

    def test_missing_empty_roots_no_creation(self):
        missing = self.home / "missing"
        result = collect_codex_usage(missing, now=NOW, tz="UTC")
        self.assertEqual(result["codex"]["status"], "missing")
        self.assertFalse(missing.exists())
        self.assertEqual(self.collect()["codex"]["status"], "available")
        self.assertEqual(self.collect()["rows"], [])

    def test_symlink_safety_and_rollout_allowlist(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        secret = outside / "rollout-private.jsonl"
        secret.write_text(json.dumps(meta()) + "\n" + json.dumps(usage()) + "\n")
        (self.home / "sessions" / "rollout-link.jsonl").symlink_to(secret)
        (self.home / "sessions" / "nested").symlink_to(outside, target_is_directory=True)
        self.write("history.jsonl", [meta(), usage()])
        result = self.collect()
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["stats"]["coverage"]["unsafePaths"], 2)
        root_alias = Path(self.tmp.name) / "alias"
        root_alias.symlink_to(self.home, target_is_directory=True)
        self.assertEqual(collect_codex_usage(root_alias, now=NOW)["rows"], [])

    def test_invalid_identity_headers_and_dates(self):
        for records in ([usage()], [meta("../invalid"), usage()], [meta(), meta("other"), usage()],
                        [meta(), usage(stamp="not-a-date")]):
            self.write("rollout-a.jsonl", records)
            result = self.collect()
            self.assertEqual(result["rows"], [])
        self.write("rollout-a.jsonl", [meta(), usage(stamp="1999-01-01T00:00:00Z"),
                                    usage(counts(200), stamp="2027-01-01T00:00:00Z")])
        result = self.collect()
        self.assertEqual(result["stats"]["outOfRange"], 1)
        self.assertEqual(result["stats"]["future"], 1)

    def test_legacy_absent_write_counter_and_repeated_last_not_summed(self):
        old = counts(write=0); del old["cache_write_input_tokens"]
        self.write("rollout-a.jsonl", [meta(), context(), usage(old), usage(old), usage(old)])
        result = self.collect()
        self.assertEqual(result["rows"][0]["cacheWrite"], 0)
        self.assertEqual(result["rows"][0]["input"], 80)
        self.assertEqual(result["stats"]["events"], 1)


if __name__ == "__main__":
    unittest.main()
