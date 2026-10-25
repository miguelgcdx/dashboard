"""Offline dashboard behavior with synthetic metadata and hostile labels."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from dashboard import (_COVERAGE_KEYS, company_for, company_slots, react_snapshot_json,
                       render_dashboard, render_react_dashboard, write_dashboard)
from pi_usage import collect_usage


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def fixture(self):
        sessions = self.root / "sessions"
        sessions.mkdir()
        private = "PRIVATE PROMPT DO NOT EXPORT"
        events = [
            {"type": "usage", "timestamp": "2026-01-04T23:00:00Z",
             "provider": "</script><img src=x onerror=alert(1)>",
             "model": "<b>unsafe & model</b>",
             "usage": {"input": 8, "output": 3, "cacheRead": 4,
                       "cacheWrite": 2, "cost": 0.125}, "content": private,
             "id": "PRIVATE-ID"},
            {"type": "usage", "timestamp": "2026-01-05T01:00:00Z",
             "provider": "safe", "model": "free", "usage": {"output": 1}},
            {"type": "usage", "timestamp": "1999-01-01T00:00:00Z",
             "provider": "safe", "model": "free", "usage": {"input": 1}},
            {"type": "usage", "timestamp": "2026-01-09T00:00:00Z",
             "provider": "safe", "model": "free", "usage": {"input": 1}},
        ]
        (sessions / "private-session-id.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events) + "\n{broken\n",
            encoding="utf-8")
        return sessions

    def test_self_contained_safe_json_and_no_raw_content(self):
        sessions = self.fixture()
        data = collect_usage(sessions, now=datetime(2026, 1, 6, tzinfo=timezone.utc),
                             tz=timezone.utc)
        data["stats"]["unreadable"] = 1
        html = render_dashboard(data)
        embedded = re.search(r'<script type="application/json" id="usage-data">(.*?)</script>',
                             html, re.DOTALL)
        self.assertIsNotNone(embedded)
        decoded = json.loads(embedded.group(1))
        self.assertEqual(decoded["rows"][1]["provider"],
                         "</script><img src=x onerror=alert(1)>")
        self.assertEqual(decoded["stats"]["malformed"], 1)
        self.assertEqual(decoded["stats"]["future"], 1)
        self.assertEqual(decoded["stats"]["outOfRange"], 1)
        self.assertEqual(decoded["stats"]["unreadable"], 1)
        self.assertEqual(decoded["stats"]["timezone"], "UTC")
        for secret in ("PRIVATE PROMPT", "PRIVATE-ID", "private-session-id.jsonl",
                       "</script><img", "<b>unsafe"):
            self.assertNotIn(secret, html)
        self.assertIn("\\u003c/script\\u003e", embedded.group(1))
        self.assertIn('http-equiv="refresh" content="300"', html)
        self.assertIn("default-src 'none'", html)
        self.assertNotIn("src=\"http", html)
        self.assertNotIn("href=\"http", html)
        self.assertIn("textContent", html)
        self.assertNotIn("innerHTML", html)
        self.assertIn("getUTCDay() + 6", html)
        self.assertIn("cacheRead + totals.cacheWrite", html)
        self.assertIn("denominator === 0n) return 'N/A'", html)
        self.assertIn("BigInt(row[key])", html)
        for key in ("input", "output", "cacheRead", "cacheWrite"):
            self.assertIsInstance(decoded["rows"][0][key], str)

    def named_snapshot(self, name="Café 東京 🚀"):
        model = {"provider": "synthetic", "model": "gpt-test", "input": 10 ** 100,
                 "output": 3, "cacheRead": 4, "cacheWrite": 2, "cost": 0.125}
        sessions = [{"startDay": "2026-01-04", "endDay": "2026-01-05", "role": role,
                     "name": name if role is None else "PRIVATE CHILD NAME",
                     "sessionId": "synthetic-parent-19" if role is None else "synthetic-child-19",
                     "days": [{"day": day, "models": [dict(model)]}
                              for day in ("2026-01-04", "2026-01-05")]}
                    for role in (None, "worker")]
        return {"rows": [{"day": day["day"], "role": session["role"], **model}
                         for session in sessions for day in session["days"]],
                "sessions": sessions, "roles": ["worker"],
                "stats": {"asOf": "2026-01-06T00:00:00+00:00", "timezone": "UTC",
                          "oldestDay": "2026-01-04", "newestDay": "2026-01-05",
                          "malformed": 0, "unreadable": 0, "future": 0, "outOfRange": 0,
                          "coverage": dict.fromkeys(_COVERAGE_KEYS, 0)}}

    def test_v5_exact_nested_allowlist_adds_only_session_header_ids(self):
        data = self.named_snapshot()
        private = {key: "PRIVATE SENTINEL" for key in
                   ("title", "sessionPath", "id", "cwd", "path", "filename", "parentSession",
                    "header", "prompt", "response", "content", "thread", "task", "messages")}
        def inject(value):
            if isinstance(value, dict):
                for child in list(value.values()):
                    inject(child)
                value.update(private)
                value.setdefault("name", "PRIVATE NON-SESSION NAME")
                value.setdefault("sessionId", "PRIVATE_NON_SESSION_ID")
            elif isinstance(value, list):
                for child in value:
                    inject(child)
        inject(data)
        payload = react_snapshot_json(data)
        decoded = json.loads(payload)
        self.assertEqual(decoded["version"], 5)
        self.assertEqual([session["sessionId"] for session in decoded["sessions"]],
                         ["synthetic-parent-19", "synthetic-child-19"])
        self.assertEqual(payload.count('"sessionId":'), 2)
        self.assertEqual(set(decoded), {"version", "rows", "roles", "stats", "sessions", "companySlots"})
        self.assertEqual([session["name"] for session in decoded["sessions"]], ["Café 東京 🚀", None])
        metrics = {"provider", "model", "input", "output", "cacheRead", "cacheWrite", "cost"}
        for row in decoded["rows"]:
            self.assertEqual(set(row), metrics | {"day", "role", "company"})
        for session in decoded["sessions"]:
            self.assertEqual(set(session), {"startDay", "endDay", "role", "name", "sessionId", "days"})
            for day in session["days"]:
                self.assertEqual(set(day), {"day", "models"})
                for model in day["models"]:
                    self.assertEqual(set(model), metrics)
                    self.assertEqual(model["input"], str(10 ** 100))
        self.assertEqual(set(decoded["stats"]), {"asOf", "timezone", "oldestDay", "newestDay",
                         "malformed", "unreadable", "future", "outOfRange", "coverage"})
        self.assertEqual(set(decoded["stats"]["coverage"]), set(_COVERAGE_KEYS))
        self.assertNotIn("PRIVATE", payload)
        for key in private:
            self.assertNotIn('"' + key + '":', payload)
        self.assertNotIn("Café", render_dashboard(data))
        self.assertNotIn('"name":', render_dashboard(data))
        self.assertNotIn('"sessionId":', render_dashboard(data))
        self.assertNotIn("synthetic-parent-19", render_dashboard(data))
        self.assertNotIn("synthetic-child-19", render_dashboard(data))

    def test_v5_requires_nullable_valid_session_ids_without_deduplicating_them(self):
        for value in ("", "a" * 129, False, 42, [], {}, "a/b", "../path", r"a\b",
                      "space id", " a", "a\n", "a\x00b", "a\x7fb", "é", "a\u202eb",
                      "<script>", "a&b", "a:b", ".start", "_start", "-start"):
            for index in (0, 1):
                with self.subTest(value=value, index=index):
                    data = self.named_snapshot()
                    data["sessions"][index]["sessionId"] = value
                    with self.assertRaisesRegex(ValueError, "session ID"):
                        react_snapshot_json(data)
        data = self.named_snapshot()
        del data["sessions"][0]["sessionId"]
        with self.assertRaisesRegex(ValueError, "Missing aggregate fields"):
            react_snapshot_json(data)
        for value in (None, "a", "A0._-z", "a" * 128, "shared-header"):
            with self.subTest(value=value):
                data = self.named_snapshot()
                for session in data["sessions"]:
                    session["sessionId"] = value
                decoded = json.loads(react_snapshot_json(data))
                self.assertEqual([s["sessionId"] for s in decoded["sessions"]], [value, value])
                self.assertEqual(len(decoded["rows"]), len(data["rows"]))

    def test_v5_name_is_script_safe_unicode_text_in_offline_html(self):
        name = '</script><script>alert("x")</script><img src=x onerror=alert(1)> & Café 東京 🚀'
        data = self.named_snapshot(name)
        assets = self.root / "assets"
        assets.mkdir()
        (assets / "dashboard.js").write_text("'use strict';")
        (assets / "dashboard.css").write_text("body { margin: 0; }")
        html = render_react_dashboard(data, assets)
        embedded = re.search(r'<script type="application/json" id="usage-data">(.*?)</script>',
                             html, re.DOTALL).group(1)
        self.assertEqual(embedded, react_snapshot_json(data))
        self.assertEqual(json.loads(embedded)["sessions"][0]["name"], name)
        self.assertNotIn("<", embedded)
        self.assertNotIn(">", embedded)
        self.assertNotIn("&", embedded)
        self.assertIn("\\u003c/script\\u003e", embedded)
        self.assertEqual(html.count("</script>"), 2)
        self.assertNotIn("PRIVATE CHILD NAME", html)
        self.assertIn("connect-src 'none'", html)
        self.assertNotIn("src=\"http", html)
        self.assertNotIn("innerHTML", html)

    def test_v5_requires_nullable_name_and_sanitizes_at_serialization_boundary(self):
        for name in (False, 1, [], {}):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "name"):
                react_snapshot_json(self.named_snapshot(name))
        data = self.named_snapshot()
        del data["sessions"][0]["name"]
        with self.assertRaisesRegex(ValueError, "Missing aggregate fields"):
            react_snapshot_json(data)
        for name, expected in ((None, None), (" \x00\u202e\ud800 ", None),
                               (" A\nB\tC\u2028D\u2029E ", "A B C D E"),
                               ("🚀" * 250, "🚀" * 200)):
            with self.subTest(name=name):
                decoded = json.loads(react_snapshot_json(self.named_snapshot(name)))
                self.assertEqual(decoded["sessions"][0]["name"], expected)

    def test_company_classification_prefers_model_and_rejects_unknown_brokers(self):
        cases = [
            ("anthropic", "gpt-5.6-codex", "OpenAI"),
            ("openai-codex", "deepseek-r1", "DeepSeek"),
            ("openrouter", "moonshotai/kimi-k2.6", "Moonshot"),
            ("openrouter", "openrouter/qwen/qwen3", "Alibaba"),
            ("openai-codex", "unrecognized", "OpenAI"),
            ("google-gemini", "unrecognized", "Google"),
            ("openrouter", "unrecognized", None),
            ("openrouter", "other/gpt-5", None),
            ("", "", None),
            ("", "not-gpt-5", None),
            ("", "claude-4", "Anthropic"),
            ("", "gemini-2", "Google"),
            ("", "grok-3", "xAI"),
            ("", "kimi-k2", "Moonshot"),
            ("", "qwen3-coder", "Alibaba"),
            ("", "qwen-3", "Alibaba"),
            ("", "qwenish", None),
            ("", "llama3.1", "Meta"),
            ("", "llama-4", "Meta"),
            ("", "mistral-small", "Mistral"),
        ]
        for provider, model, expected in cases:
            with self.subTest(provider=provider, model=model):
                self.assertEqual(company_for(provider, model), expected)

    def test_commandcode_models_preserve_company_and_broker_identity(self):
        cases = [
            ("deepseek/deepseek-v4.1-flash", "DeepSeek"),
            ("zai-org/glm-5.3", "Z.ai"),
            ("z-ai/glm-5.3-flash", "Z.ai"),
            ("unrecognized", None),
            ("othercorp/glm-5.3", None),
        ]
        for model, expected in cases:
            with self.subTest(model=model):
                self.assertEqual(company_for("commandcode", model), expected)
        self.assertEqual(company_for("cliproxyapi", "gemini-3.8-flash-high"), "Google")

    def test_unknown_namespace_does_not_inherit_direct_provider_company(self):
        rows = []
        for provider, model in (("openai-codex", "othercorp/custom-model"),
                                ("google-gemini", "openrouter/othercorp/gemini-2")):
            with self.subTest(provider=provider, model=model):
                self.assertIsNone(company_for(provider, model))
            rows.append({"provider": provider, "model": model, "input": 100,
                         "output": 0, "cacheRead": 0, "cacheWrite": 0})
        self.assertEqual(company_slots(rows), ["OpenAI", "DeepSeek", None, None, None])

    def test_five_company_slots_rank_token_volume_and_ignore_zero(self):
        def row(provider, model, input=0, output=0, cacheRead=0, cacheWrite=0):
            return {"provider": provider, "model": model, "input": input,
                    "output": output, "cacheRead": cacheRead, "cacheWrite": cacheWrite}
        rows = [row("openrouter", "moonshotai/kimi-k2.6"),
                row("unknown", "", input=1000),
                row("openrouter", "claude-4", input=4, cacheRead=6),
                row("anthropic", "claude-3", output=5),
                row("deepseek", "deepseek-v3", output=900),
                row("", "gemini-2", cacheWrite=15),
                row("", "grok-3", input=15),
                row("", "qwen-3", output=14)]
        self.assertEqual(company_slots(rows),
                         ["OpenAI", "DeepSeek", "Anthropic", "Google", "xAI"])
        self.assertEqual(company_slots(rows[:2]),
                         ["OpenAI", "DeepSeek", None, None, None])
        self.assertEqual(company_slots([row("", "grok-3", input=1)]),
                         ["OpenAI", "DeepSeek", "xAI", None, None])

    def test_snapshot_classification_is_private_and_does_not_mix_companies(self):
        def row(provider, model, amount, day="2026-01-05"):
            return {"day": day, "provider": provider, "model": model,
                    "input": amount, "output": 0, "cacheRead": 0,
                    "cacheWrite": 0, "cost": 0}
        stats = {"asOf": "2026-01-06T00:00:00+00:00", "timezone": "UTC",
                 "oldestDay": "2026-01-05", "newestDay": "2026-01-05",
                 "malformed": 0, "unreadable": 0, "future": 0, "outOfRange": 0}
        rows = [row("anthropic", "gpt-5.6", 7),
                row("openai-codex", "private-proxy-model", 2),
                row("openrouter", "unknown-broker-model", 13),
                row("", "", 17), row("openrouter", "moonshotai/kimi-k2.6", 0)]
        def payload():
            html = render_dashboard({"rows": rows, "stats": stats})
            embedded = re.search(r'<script type="application/json" id="usage-data">(.*?)</script>',
                                 html, re.DOTALL)
            return html, json.loads(embedded.group(1))
        html, initial = payload()
        self.assertEqual(initial["companySlots"], ["OpenAI", "DeepSeek", None, None, None])
        self.assertEqual([item["company"] for item in initial["rows"]],
                         ["OpenAI", "OpenAI", None, None, "Moonshot"])
        self.assertEqual(len(initial["rows"]), 5)  # Unknown usage remains globally visible.
        self.assertIn('No additional company with measured token usage', html)
        self.assertIn("row.company !== company", html)
        self.assertIn("renderCompanies();", html)
        self.assertIn("period.addEventListener('change', render)", html)
        rows.append(row("openrouter", "moonshotai/kimi-k2.6", 18, "2026-01-06"))
        _, refreshed = payload()
        self.assertEqual(refreshed["companySlots"],
                         ["OpenAI", "DeepSeek", "Moonshot", None, None])
        self.assertEqual(refreshed["rows"][-1]["company"], "Moonshot")
        rows.append(row("", "gpt-5.6-\u003cscript\u003e", 1))
        escaped_html, escaped = payload()
        self.assertEqual(escaped["rows"][-1]["company"], "OpenAI")
        self.assertNotIn("gpt-5.6-<script>", escaped_html)
        self.assertIn("gpt-5.6-\\u003cscript\\u003e", escaped_html)

    def test_atomic_private_output_and_cli_smoke(self):
        sessions = self.fixture()
        output = self.root / "destination" / "dashboard.html"
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        command = [sys.executable, "dashboard.py", "--sessions", str(sessions),
                   "--output", str(output)]
        result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1],
                                env=env, text=True, capture_output=True, check=True)
        self.assertIn("Dashboard updated", result.stdout)
        self.assertEqual(stat.S_IMODE(output.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        html = output.read_text(encoding="utf-8")
        self.assertNotIn("PRIVATE PROMPT", html)
        self.assertNotIn("private-session-id", html)
        self.assertEqual(len(list(output.parent.iterdir())), 1)
        write_dashboard(output, "replacement")
        self.assertEqual(output.read_text(encoding="utf-8"), "replacement")
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

    def test_existing_private_parent_is_not_chmodded(self):
        parent = self.root / "existing"
        parent.mkdir(mode=0o700)
        parent.chmod(0o700)
        output = parent / "index.html"
        output.write_text("old", encoding="utf-8")
        output.chmod(0o644)
        with patch.object(Path, "chmod", side_effect=AssertionError("existing parent chmod")):
            write_dashboard(output, "new")
        self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o700)
        self.assertEqual(output.read_text(encoding="utf-8"), "new")
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        self.assertEqual(sorted(path.name for path in parent.iterdir()), ["index.html"])

    def test_existing_public_parent_is_rejected_without_mode_change(self):
        parent = self.root / "public"
        parent.mkdir(mode=0o755)
        parent.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "private"):
            write_dashboard(parent / "index.html", "data")
        self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o755)
        self.assertEqual(list(parent.iterdir()), [])

    def test_existing_parent_owned_by_other_user_is_rejected(self):
        parent = self.root / "other-owner"
        parent.mkdir(mode=0o700)
        parent.chmod(0o700)
        with patch("dashboard.os.getuid", return_value=parent.stat().st_uid + 1):
            with self.assertRaisesRegex(ValueError, "owned"):
                write_dashboard(parent / "index.html", "data")
        self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o700)
        self.assertEqual(list(parent.iterdir()), [])

    def test_output_parent_symlink_is_rejected(self):
        real = self.root / "real"
        real.mkdir()
        link = self.root / "alias"
        link.symlink_to(real, target_is_directory=True)
        with self.assertRaises(ValueError):
            write_dashboard(link / "index.html", "data")
        self.assertEqual(list(real.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
