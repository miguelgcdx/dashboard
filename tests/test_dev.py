"""Real dev collection from hermetic fabricated sources, never the user's corpus."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, datetime
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from dashboard import _COVERAGE_KEYS, company_for


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
URL = "http://127.0.0.1:5173/"
ENDPOINT = URL + "__pi_usage_snapshot"


def synthetic_home(directory):
    """Populate only temporary Pi-shaped sources, including private-field sentinels."""
    home = Path(directory)
    agent = home / ".pi" / "agent"
    parents = home / "PRIVATE_PARENT_ROOT"
    children = agent / "gentle-agents" / "sessions"
    tasks = agent / "gentle-agents" / "tasks"
    roles = agent / "agents"
    for path in (parents, children, tasks, roles):
        path.mkdir(parents=True)
    (roles / "worker.md").write_text("PRIVATE_ROLE_CONTENT")
    def event(identity, day, amount, output, read, write):
        return {"type": "usage", "id": identity, "timestamp": day + "T12:00:00Z",
                "provider": "synthetic", "model": "gpt-dev-fixture",
                "usage": {"input": amount, "output": output, "cacheRead": read,
                          "cacheWrite": write, "cost": {"total": 0.125}},
                "prompt": "PRIVATE_PROMPT", "cwd": "/PRIVATE_CWD",
                "content": "PRIVATE_RESPONSE", "sessionPath": "/PRIVATE_SESSION"}
    (parents / "PRIVATE_PARENT.jsonl").write_text("\n".join(json.dumps(row) for row in [
        {"type": "session", "id": "synthetic-parent-19", "cwd": "/PRIVATE_CWD",
         "parentSession": "/PRIVATE_PARENT_SESSION"},
        {"type": "session_info", "name": "Synthetic Café 東京 session"},
        {"type": "message", "message": {"role": "user", "content": "PRIVATE_MESSAGE"}},
        event("PRIVATE_OLD_ID", "2026-01-07", 10, 0, 0, 0),
        event("PRIVATE_PARENT_ID", "2026-01-08", 100, 20, 40, 5),
    ]))
    (children / "PRIVATE_CHILD.jsonl").write_text("\n".join(json.dumps(row) for row in [
        {"type": "session", "id": "synthetic-child-19"},
        event("PRIVATE_CHILD_ID", "2026-01-08", 200, 30, 80, 5),
        {"type": "session_info", "name": "PRIVATE_CHILD_NAME"},
    ]))
    (tasks / "PRIVATE_TASK.json").write_text(json.dumps({"task": {
        "agent": "worker", "sessionPath": "PRIVATE_CHILD.jsonl", "title": "PRIVATE_TITLE",
        "prompt": "PRIVATE_TASK_PROMPT", "model": "PRIVATE_LAUNCH_MODEL", "tokens": 999999,
    }}))
    return {"PATH": os.environ.get("PATH", os.defpath), "HOME": str(home),
            "PI_CODING_AGENT_SESSION_DIR": str(parents), "PYTHONDONTWRITEBYTECODE": "1",
            "TZ": "UTC", "LANG": "C.UTF-8", "COREPACK_ENABLE_NETWORK": "0",
            "XDG_CONFIG_HOME": str(home / "config"), "XDG_CACHE_HOME": str(home / "cache")}


def stop_group(process):
    """Always kill descendants too, even if the command runner already exited."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        pass
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


class DevTests(unittest.TestCase):
    def test_fixture_is_complete_fabricated_v5_private_snapshot(self):
        data = json.loads((WEB / "dev-snapshot.json").read_text())
        self.assertEqual(set(data), {"version", "companySlots", "roles", "rows", "stats", "sessions"})
        self.assertEqual(data["version"], 5)
        self.assertEqual(data["companySlots"], ["OpenAI", "DeepSeek", "Anthropic", "Google", "xAI"])
        self.assertEqual(data["roles"], ["worker", "explore", "verify"])
        self.assertTrue(data["rows"])
        for row in data["rows"]:
            self.assertEqual(set(row), {"day", "provider", "model", "role", "company",
                                        "input", "output", "cacheRead", "cacheWrite", "cost"})
            date.fromisoformat(row["day"])
            self.assertEqual(row["provider"], "synthetic")
            self.assertTrue(row["model"].endswith("-synthetic"))
            self.assertIn(row["role"], [None, *data["roles"]])
            self.assertIn(row["company"], data["companySlots"])
            self.assertEqual(row["company"], company_for(row["provider"], row["model"]))
            for key in ("input", "output", "cacheRead", "cacheWrite"):
                self.assertIsInstance(row[key], str)
                self.assertRegex(row[key], r"^(0|[1-9][0-9]*)$")
            self.assertTrue(math.isfinite(row["cost"]) and row["cost"] >= 0)
        contributions = {}
        self.assertTrue(data["sessions"])
        self.assertTrue(any(len(session["days"]) > 1 for session in data["sessions"]))
        measured_days = [day["day"] for session in data["sessions"] for day in session["days"]]
        self.assertLess(len(set(measured_days)), len(measured_days))
        for session in data["sessions"]:
            self.assertEqual(set(session), {"startDay", "endDay", "role", "name", "sessionId", "days"})
            if session["sessionId"] is not None:
                self.assertRegex(session["sessionId"], r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
            self.assertIn(session["role"], [None, *data["roles"]])
            if session["role"] is not None:
                self.assertIsNone(session["name"])
            elif session["name"] is not None:
                self.assertIsInstance(session["name"], str)
                self.assertGreater(len(session["name"]), 0)
                self.assertLessEqual(len(session["name"]), 200)
            self.assertTrue(session["days"])
            days = [day["day"] for day in session["days"]]
            self.assertEqual(days, sorted(set(days)))
            self.assertEqual(session["startDay"], days[0])
            self.assertEqual(session["endDay"], days[-1])
            for day in session["days"]:
                self.assertEqual(set(day), {"day", "models"})
                self.assertEqual(date.fromisoformat(day["day"]).isoformat(), day["day"])
                self.assertTrue(day["models"])
                for model in day["models"]:
                    self.assertEqual(set(model), {"provider", "model", "input", "output",
                                                  "cacheRead", "cacheWrite", "cost"})
                    self.assertEqual(model["provider"], "synthetic")
                    self.assertTrue(model["model"].endswith("-synthetic"))
                    key = (day["day"], session["role"], model["provider"], model["model"])
                    totals = contributions.setdefault(key, dict.fromkeys(
                        ("input", "output", "cacheRead", "cacheWrite", "cost"), 0))
                    for metric in ("input", "output", "cacheRead", "cacheWrite"):
                        self.assertIsInstance(model[metric], str)
                        self.assertRegex(model[metric], r"^(0|[1-9][0-9]*)$")
                        totals[metric] += int(model[metric])
                    self.assertIs(type(model["cost"]), float)
                    self.assertTrue(math.isfinite(model["cost"]) and model["cost"] >= 0)
                    totals["cost"] += model["cost"]
        expected = {(row["day"], row["role"], row["provider"], row["model"]): row for row in data["rows"]}
        self.assertEqual(set(contributions), set(expected))
        for key, totals in contributions.items():
            for metric in ("input", "output", "cacheRead", "cacheWrite"):
                self.assertEqual(totals[metric], int(expected[key][metric]))
            self.assertAlmostEqual(totals["cost"], expected[key]["cost"])
        stats = data["stats"]
        self.assertEqual(set(stats), {"asOf", "timezone", "oldestDay", "newestDay",
                                     "malformed", "unreadable", "future", "outOfRange", "coverage"})
        self.assertIsNotNone(datetime.fromisoformat(stats["asOf"]).tzinfo)
        self.assertEqual(stats["timezone"], "UTC")
        self.assertEqual(stats["oldestDay"], min(row["day"] for row in data["rows"]))
        self.assertEqual(stats["newestDay"], max(row["day"] for row in data["rows"]))
        self.assertEqual(set(stats["coverage"]), set(_COVERAGE_KEYS))
        for count in [*(stats[key] for key in ("malformed", "unreadable", "future", "outOfRange")),
                      *stats["coverage"].values()]:
            self.assertIs(type(count), int)
            self.assertGreaterEqual(count, 0)
            self.assertLessEqual(count, 9007199254740991)

    def test_root_pnpm_policy_disables_auto_install_and_lockfile(self):
        # Keep this tiny root-only policy explicit without adding a YAML dependency.
        self.assertEqual((ROOT / "pnpm-workspace.yaml").read_text().splitlines(), [
            "packages:", "  - '.'", "verifyDepsBeforeRun: false", "lockfile: false",
        ])
        self.assertIn("/node_modules/", (ROOT / ".gitignore").read_text().splitlines())
        readme = (ROOT / "README.md").read_text()
        self.assertIn("cd web && npm ci --ignore-scripts", readme)
        self.assertIn("`pnpm dev` runs scripts only, without automatic installation", readme)

    def test_dev_entry_loopback_and_production_isolation(self):
        package = json.loads((ROOT / "package.json").read_text())
        self.assertTrue(package["private"])
        self.assertEqual(package["scripts"], {"dev": "npm --prefix web run dev"})
        self.assertFalse({"dependencies", "workspaces"} & package.keys())
        # Optional inspection CLI may be removed without changing dashboard behavior.
        self.assertLessEqual(set(package.get("devDependencies", {})), {"command-code"})
        document = (WEB / "index.html").read_text()
        self.assertIn('type="application/json" id="usage-data"', document)
        self.assertIn("fetch('/__usage_workspace'", document)
        self.assertIn(".textContent = JSON.stringify(snapshot)", document)
        self.assertIn('role="status"', document)
        self.assertIn("root.setAttribute('role', 'alert')", document)
        self.assertIn("if (!response.ok)", document)
        self.assertLess(document.index(".textContent ="), document.index("await import('./src/main.tsx')"))
        self.assertEqual(re.findall(r"(?:from\s+|import\()['\"]([^'\"]+)", document), ["./src/main.tsx"])
        for forbidden in ("dev-snapshot", "synthetic-banner", "innerHTML", ".pi/",
                          "preview.html", "dashboard.py", "https://"):
            self.assertNotIn(forbidden, document)
        config = (WEB / "vite.config.ts").read_text()
        for setting in ("host: '127.0.0.1'", "port: 5173", "strictPort: true", "cors: false",
                        "fs: { strict: true, allow: [webPath] }", "publicDir: false",
                        "entry: 'src/main.tsx'", "formats: ['iife']", "apply: 'serve'",
                        "execFile('python3', ['-B', dashboardPath, command]", "'--workspace-json' : '--dev-json'", "'/__usage_workspace'",
                        "timeout: 30_000", "maxBuffer: 16 * 1024 * 1024", "shell: false",
                        "new URL('../dashboard.py', import.meta.url)",
                        "new URL('.', import.meta.url)"):
            self.assertIn(setting, config)
        for forbidden in ("dev-snapshot", "index.html", "exec(", "writeFile", "Access-Control-Allow-Origin"):
            self.assertNotIn(forbidden, config)
        if (WEB / "dist" / "dashboard.js").exists():
            bundle = (WEB / "dist" / "dashboard.js").read_text()
            for forbidden in ("__pi_usage_snapshot", "gpt-synthetic", "gpt-dev-fixture", "PRIVATE_"):
                self.assertNotIn(forbidden, bundle)
            for name in ("index.html", "dev-snapshot.json"):
                self.assertFalse((WEB / "dist" / name).exists())

    @contextmanager
    def dev_server(self):
        if not shutil.which("pnpm") or not (WEB / "node_modules" / "vite").is_dir():
            self.skipTest("Existing pnpm/Vite unavailable; no install attempted")
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", 5173))
            except OSError:
                self.skipTest("Port 5173 occupied: existing listener untouched")
        absent_metadata = [ROOT / name for name in ("pnpm-lock.yaml", "node_modules")
                           if not (ROOT / name).exists()]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with tempfile.TemporaryDirectory(prefix="pi-dev-test-") as directory:
            home = Path(directory)
            env = synthetic_home(home)
            env['PI_CODING_AGENT_DIR'] = str(home / '.pi/agent')
            env['CODEX_HOME'] = str(home / 'synthetic-codex')
            mode = home / "collector-mode"
            mode.write_text("ok")
            binary = home / "bin"
            binary.mkdir()
            # Instrument only the test PATH. Successful calls exec the actual collector.
            shim = binary / "python3"
            shim.write_text(f"#!{sys.executable}\n" + '''import os, sys, time
from pathlib import Path
home = Path(__file__).parent.parent
with (home / 'calls').open('a') as stream:
    stream.write('1\\n')
(home / ('child-' + str(os.getpid()))).write_text('')
mode = (home / 'collector-mode').read_text()
if mode == 'fail':
    print('PRIVATE_STDOUT')
    print('PRIVATE_STDERR_PATH', file=sys.stderr)
    sys.exit(1)
time.sleep(0.5)
os.execv(REAL_PYTHON, [REAL_PYTHON, *sys.argv[1:]])
'''.replace('REAL_PYTHON', repr(sys.executable)))
            shim.chmod(0o700)
            env["PATH"] = str(binary) + os.pathsep + env["PATH"]
            process = subprocess.Popen([shutil.which("pnpm"), "dev"], cwd=ROOT, env=env,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       start_new_session=True, text=True)
            try:
                deadline = time.monotonic() + 20
                while True:
                    self.assertIsNone(process.poll(), "Root pnpm dev exited before serving")
                    try:
                        with opener.open(URL, timeout=1) as response:
                            document = response.read().decode()
                        break
                    except (urllib.error.URLError, TimeoutError):
                        if time.monotonic() >= deadline:
                            self.fail("Root pnpm dev did not start within 20 seconds")
                        time.sleep(0.1)
                self.assertIn("Loading local Pi usage aggregates", document)
                self.assertIn("/@vite/client", document)
                yield opener, home, env
            finally:
                stop_group(process)
                logs, _ = process.communicate(timeout=5)
                for forbidden in ("PRIVATE_STDOUT", "PRIVATE_STDERR_PATH", "PRIVATE_PROMPT"):
                    self.assertNotIn(forbidden, logs, "Dev server logged raw collector details")
                for path in absent_metadata:
                    self.assertFalse(path.exists(), f"Root install metadata created: {path.name}")
                with socket.socket() as probe:
                    probe.settimeout(1)
                    self.assertNotEqual(probe.connect_ex(("127.0.0.1", 5173)), 0, "Dev listener lingered")
                for child in home.glob("child-*"):
                    with self.assertRaises(ProcessLookupError, msg="Collector process lingered"):
                        os.kill(int(child.name.removeprefix("child-")), 0)

    def assert_private_fields_absent(self, value, home):
        for forbidden in ("PRIVATE_", str(home), '"sessionPath"', '"parentSession"',
                          '"prompt"', '"cwd"', '"title"', '"task"', '"content"'):
            self.assertNotIn(forbidden, value)

    def test_managed_root_dev_http_schema_privacy_and_confinement(self):
        with self.dev_server() as (opener, home, env):
            def request_snapshot(_):
                # Independent clients exercise overlap rather than a shared connection.
                client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with client.open(ENDPOINT, timeout=10) as response:
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
                    self.assertEqual(response.headers["Cross-Origin-Resource-Policy"], "same-origin")
                    self.assertEqual(response.headers.get_content_type(), "application/json")
                    self.assertIsNone(response.headers.get("Access-Control-Allow-Origin"))
                    return response.read().decode()
            with ThreadPoolExecutor(max_workers=4) as pool:
                responses = list(pool.map(request_snapshot, range(4)))
            self.assertEqual(len(set(responses)), 1)
            self.assertEqual((home / "calls").read_text().splitlines(), ["1"])
            self.assert_private_fields_absent(responses[0], home)
            data = json.loads(responses[0])
            self.assertEqual(set(data), {"version", "rows", "roles", "stats", "companySlots", "sessions"})
            self.assertEqual(data["version"], 5)
            self.assertEqual(data["roles"], ["worker"])
            self.assertEqual(len(data["rows"]), 3)
            self.assertEqual(len(data["sessions"]), 2)
            self.assertEqual({row["role"] for row in data["rows"]}, {None, "worker"})
            self.assertEqual(data["stats"]["coverage"]["linkedChildFiles"], 1)
            self.assertEqual(data["stats"]["coverage"]["parentFiles"], 1)
            self.assertEqual(data["companySlots"], ["OpenAI", "DeepSeek", None, None, None])
            for row in data["rows"]:
                self.assertEqual(set(row), {"day", "role", "company", "provider", "model",
                                            "input", "output", "cacheRead", "cacheWrite", "cost"})
                for key in ("input", "output", "cacheRead", "cacheWrite"):
                    self.assertRegex(row[key], r"^(0|[1-9][0-9]*)$")
            for session in data["sessions"]:
                self.assertEqual(set(session), {"startDay", "endDay", "role", "name", "sessionId", "days"})
                self.assertEqual(session["sessionId"], "synthetic-parent-19"
                                 if session["role"] is None else "synthetic-child-19")
                self.assertEqual(session["name"], "Synthetic Café 東京 session"
                                 if session["role"] is None else None)
                for day in session["days"]:
                    self.assertEqual(set(day), {"day", "models"})
                    for model in day["models"]:
                        self.assertEqual(set(model), {"provider", "model", "input", "output",
                                                      "cacheRead", "cacheWrite", "cost"})
            self.assertEqual(sum(int(row[key]) for row in data["rows"] if row["day"] == "2026-01-08"
                                 for key in ("input", "output", "cacheRead", "cacheWrite")), 480)
            # The standalone CLI uses exactly the same source defaults and schema.
            cli = subprocess.run([sys.executable, "-B", str(ROOT / "dashboard.py"), "--dev-json"],
                                 env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(cli.returncode, 0)
            self.assertEqual(cli.stderr, "")
            other = json.loads(cli.stdout)
            other["stats"]["asOf"] = data["stats"]["asOf"]
            self.assertEqual(other, data)
            self.assertFalse((home / ".local/share/pi-usage-dashboard").exists(),
                             "CLI created a dashboard output directory")
            for headers in ({"Origin": "https://foreign.invalid"}, {"Origin": "null"},
                            {"Origin": "http://localhost:5173"}, {"Host": "foreign.invalid:5173"},
                            {"Host": "127.0.0.1.evil.invalid:5173"}):
                with self.subTest(headers=headers), self.assertRaises(urllib.error.HTTPError) as denied:
                    opener.open(urllib.request.Request(ENDPOINT, headers=headers), timeout=3)
                with denied.exception as response:
                    self.assertEqual(response.code, 403)
                    self.assertIsNone(response.headers.get("Access-Control-Allow-Origin"))
                    self.assert_private_fields_absent(response.read().decode(), home)
            for method in ("POST", "OPTIONS", "HEAD"):
                with self.subTest(method=method), self.assertRaises(urllib.error.HTTPError) as denied:
                    opener.open(urllib.request.Request(ENDPOINT, method=method), timeout=3)
                with denied.exception as response:
                    self.assertEqual(response.code, 405)
                    self.assertEqual(response.headers["Allow"], "GET")
                    self.assertIsNone(response.headers.get("Access-Control-Allow-Origin"))
            self.assertEqual(len((home / "calls").read_text().splitlines()), 1,
                             "Denied requests invoked the collector")
            # Harmless repository source and fabricated source, never actual private files.
            for path in (ROOT / "README.md", Path(env["PI_CODING_AGENT_SESSION_DIR"]) / "PRIVATE_PARENT.jsonl"):
                with self.subTest(path=path.name), self.assertRaises(urllib.error.HTTPError) as denied:
                    opener.open(URL + "@fs" + str(path), timeout=3)
                with denied.exception as response:
                    self.assertEqual(response.code, 403)
            (home / "collector-mode").write_text("fail")
            with self.assertRaises(urllib.error.HTTPError) as unavailable:
                opener.open(ENDPOINT, timeout=10)
            with unavailable.exception as response:
                self.assertEqual(response.code, 503)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertEqual(json.load(response), {"error": "Usage snapshot unavailable. Reload to retry."})
            (home / "collector-mode").write_text("ok")
            with opener.open(urllib.request.Request(ENDPOINT, headers={"Origin": URL.rstrip("/")}), timeout=10) as response:
                self.assertEqual(json.load(response)["version"], 5)
            self.assertEqual(len((home / "calls").read_text().splitlines()), 3,
                             "Failed collection was cached or later requests were not fresh")

    def test_managed_workspace_endpoint_sources_errors_and_security(self):
        with self.dev_server() as (opener, home, env):
            root = Path(env['CODEX_HOME']) / 'sessions'
            root.mkdir(parents=True)
            counters = dict(input_tokens=100, cached_input_tokens=20, output_tokens=30,
                            reasoning_output_tokens=5, cache_write_input_tokens=10)
            (root / 'rollout-synthetic.jsonl').write_text('\n'.join(json.dumps(item) for item in [
                {'type': 'session_meta', 'payload': {'id': 'synthetic-codex', 'model_provider': 'explicit-provider', 'cwd': '/PRIVATE_CWD'}},
                {'type': 'turn_context', 'payload': {'model': 'gpt-codex-only'}},
                {'type': 'event_msg', 'timestamp': '2026-01-08T12:00:00Z', 'payload': {
                    'type': 'token_count', 'info': {'total_token_usage': counters, 'last_token_usage': counters}}},
            ]))
            endpoint = URL + '__usage_workspace'
            def request(_):
                client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with client.open(endpoint, timeout=10) as response:
                    self.assertEqual(response.headers['Cache-Control'], 'no-store')
                    self.assertEqual(response.headers['Cross-Origin-Resource-Policy'], 'same-origin')
                    self.assertIsNone(response.headers.get('Access-Control-Allow-Origin'))
                    text = response.read().decode()
                    self.assert_private_fields_absent(text, home)
                    return text
            with ThreadPoolExecutor(max_workers=4) as pool:
                responses = list(pool.map(request, range(4)))
            self.assertEqual(len(set(responses)), 1)
            self.assertEqual((home / 'calls').read_text().splitlines(), ['1'])
            data = json.loads(responses[0])
            self.assertEqual(data['workspaceVersion'], 1)
            pi, codex = data['sources']['pi']['snapshot'], data['sources']['codex']['snapshot']
            self.assertEqual(len(pi['rows']), 3)
            self.assertEqual(len(codex['rows']), 1)
            self.assertEqual(codex['rows'][0]['input'], '70')
            self.assertEqual(codex['rows'][0]['provider'], 'explicit-provider')
            self.assertFalse(data['sources']['codex']['metadata']['costAvailable'])
            for headers in ({'Origin': 'null'}, {'Origin': 'https://foreign.invalid'}, {'Host': 'foreign.invalid:5173'}):
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    opener.open(urllib.request.Request(endpoint, headers=headers), timeout=3)
                self.assertEqual(denied.exception.code, 403)
                denied.exception.close()
            for method in ('POST', 'OPTIONS', 'HEAD'):
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    opener.open(urllib.request.Request(endpoint, method=method), timeout=3)
                self.assertEqual(denied.exception.code, 405)
                denied.exception.close()
            self.assertEqual(len((home / 'calls').read_text().splitlines()), 1)
            if shutil.which('chromium'):
                with tempfile.TemporaryDirectory(prefix='workspace-dev-browser-') as name:
                    profile = Path(name)
                    result = subprocess.run([shutil.which('chromium'), '--headless', '--disable-gpu',
                        '--no-first-run', '--no-default-browser-check', '--disable-sync',
                        '--disable-background-networking', '--disable-component-update', '--disable-extensions',
                        '--disable-breakpad', '--disable-crash-reporter', '--password-store=basic',
                        f'--user-data-dir={profile}', f'--crash-dumps-dir={profile}',
                        '--virtual-time-budget=10000', '--dump-dom', URL + '#/codex/overview'],
                        capture_output=True, text=True, timeout=45,
                        env={'PATH': os.defpath, 'HOME': name, 'XDG_CONFIG_HOME': str(profile / 'config'),
                             'XDG_CACHE_HOME': str(profile / 'cache'), 'LANG': 'C.UTF-8'})
                    self.assertEqual(result.returncode, 0)
                    # The embedded workspace contains both sources; isolation applies
                    # to the rendered active view, not its private JSON payload.
                    rendered = re.search(r'<main\b[^>]*>(.*?)</main>', result.stdout, re.S)
                    self.assertIsNotNone(rendered, 'Expected the rendered Codex main view')
                    view = rendered.group(1)
                    self.assertIn('<h1>Overview</h1>', view)
                    self.assertIn('gpt-codex-only', view)
                    self.assertIn('Recorded cost</h2><strong>Unavailable</strong>', view)
                    self.assertNotIn('gpt-dev-fixture', view)
            (home / 'collector-mode').write_text('fail')
            with self.assertRaises(urllib.error.HTTPError) as failed:
                opener.open(endpoint, timeout=10)
            with failed.exception as response:
                self.assertEqual(response.code, 503)
                self.assertEqual(json.load(response), {'error': 'Usage snapshot unavailable. Reload to retry.'})
            (home / 'collector-mode').write_text('ok')
            self.assertEqual(json.loads(request(0))['workspaceVersion'], 1)

    @unittest.skipUnless(shutil.which("chromium"), "Chromium unavailable; dev browser proof pending")
    def test_managed_root_dev_chromium_real_shaped_source_and_error(self):
        with self.dev_server() as (_, home, _):
            for route in ("Overview", "Companies", "Error"):
                if route == "Error":
                    (home / "collector-mode").write_text("fail")
                with self.subTest(route=route), tempfile.TemporaryDirectory(prefix="pi-dev-browser-") as name:
                    profile = Path(name)
                    command = [shutil.which("chromium"), "--headless", "--disable-gpu",
                               "--no-first-run", "--no-default-browser-check", "--disable-sync",
                               "--disable-background-networking", "--disable-component-update",
                               "--disable-extensions", "--disable-breakpad", "--disable-crash-reporter",
                               "--password-store=basic", f"--user-data-dir={profile}",
                               f"--crash-dumps-dir={profile}", "--virtual-time-budget=10000",
                               "--dump-dom", URL + "#/" + route.lower()]
                    browser = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                               text=True, start_new_session=True,
                                               env={"PATH": os.defpath, "HOME": name,
                                                    "XDG_CONFIG_HOME": str(profile / "config"),
                                                    "XDG_CACHE_HOME": str(profile / "cache"), "LANG": "C.UTF-8"})
                    try:
                        output, _ = browser.communicate(timeout=45)
                        self.assertEqual(browser.returncode, 0, "Isolated Chromium failed")
                        self.assert_private_fields_absent(output, home)
                        self.assertNotIn("SYNTHETIC SAMPLE", output)
                        if route == "Error":
                            self.assertIn('role="alert"', output)
                            self.assertIn("Local usage could not be loaded", output)
                            self.assertNotIn("<h1>", output)
                        else:
                            self.assertIn(f"<h1>{route}</h1>", output)
                            self.assertNotIn("could not be read safely", output)
                            local_notice = "Local development · Vite updates the UI; reload to collect current usage."
                            if route == "Overview":
                                self.assertNotIn(local_notice, output)
                                self.assertNotIn("<footer>", output)
                            else:
                                self.assertIn(local_notice, output)
                            self.assertNotIn("Offline preview · Reloads every 5 minutes.", output)
                            if route == "Companies":
                                self.assertEqual(len(re.findall(r'class="[^"]*\bcompany-card\b', output)), 5)
                            else:
                                self.assertIn("gpt-dev-fixture", output)
                                self.assertIn("Total tokens</h2><strong>480</strong>", output)
                                self.assertIn("Selected day · 2026-01-08", output)
                    finally:
                        stop_group(browser)


if __name__ == "__main__":
    unittest.main()
