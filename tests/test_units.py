"""Check the shipped timer contract without installing or activating units."""

import configparser
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


UNITS = Path(__file__).resolve().parents[1] / "systemd"


def load_unit(name):
    unit = configparser.ConfigParser(interpolation=None)
    with (UNITS / name).open(encoding="utf-8") as source:
        unit.read_file(source)
    return unit


class UnitTests(unittest.TestCase):
    def test_service_is_private_unprivileged_local_oneshot(self):
        unit = load_unit("pi-usage-dashboard.service")
        service = unit["Service"]
        self.assertEqual(service["Type"], "oneshot")
        self.assertEqual(service["WorkingDirectory"], "%h/Projects/pi-usage-dashboard")
        self.assertEqual(service["UMask"], "0077")
        self.assertEqual(
            service["ExecStart"],
            "/usr/bin/python3 -B %h/Projects/pi-usage-dashboard/dashboard.py",
        )
        self.assertNotIn("User", service)
        self.assertNotIn("RootDirectory", service)
        self.assertNotIn("ExecStartPre", service)

    def test_timer_runs_every_five_minutes_in_user_manager(self):
        unit = load_unit("pi-usage-dashboard.timer")
        self.assertEqual(unit["Timer"]["OnCalendar"], "*:0/5")
        self.assertEqual(unit["Timer"]["Persistent"], "true")
        self.assertEqual(unit["Timer"]["Unit"], "pi-usage-dashboard.service")
        self.assertEqual(unit["Install"]["WantedBy"], "timers.target")
        self.assertNotIn("Service", unit)


class PreviewUnitTests(unittest.TestCase):
    def test_preview_service_is_private_unprivileged_local_oneshot(self):
        service = load_unit("pi-usage-dashboard-preview.service")["Service"]
        self.assertEqual(service["Type"], "oneshot")
        self.assertEqual(service["WorkingDirectory"], "%h/Projects/pi-usage-dashboard")
        self.assertEqual(service["UMask"], "0077")
        self.assertEqual(
            service["ExecStart"],
            "/usr/bin/python3 -B %h/Projects/pi-usage-dashboard/dashboard.py "
            "--preview-react --output %h/.local/share/pi-usage-dashboard/preview.html",
        )

    def test_preview_service_requires_independently_verified_assets(self):
        unit = load_unit("pi-usage-dashboard-preview.service")
        self.assertEqual(
            unit["Unit"]["ConditionPathExists"],
            "%h/Projects/pi-usage-dashboard/web/dist/.verified",
        )
        # A missing marker skips the service, rather than failing an assertion
        # or running a helper that could create the marker automatically.
        self.assertEqual(set(unit["Unit"]), {"description", "conditionpathexists"})

    def test_preview_service_has_no_build_network_or_legacy_dependencies(self):
        unit = load_unit("pi-usage-dashboard-preview.service")
        self.assertEqual(set(unit.sections()), {"Unit", "Service"})
        # Together with the exact ExecStart above, this forbids build/server
        # commands, privilege overrides, hooks, and dependency coupling.
        self.assertEqual(
            set(unit["Service"]), {"type", "workingdirectory", "umask", "execstart"}
        )
        self.assertEqual(set(unit["Unit"]), {"description", "conditionpathexists"})

    def test_preview_timer_runs_every_five_minutes_staggered_from_legacy(self):
        unit = load_unit("pi-usage-dashboard-preview.timer")
        legacy = load_unit("pi-usage-dashboard.timer")
        self.assertEqual(unit["Timer"]["OnCalendar"], "*:2/5")
        self.assertNotEqual(unit["Timer"]["OnCalendar"], legacy["Timer"]["OnCalendar"])
        self.assertEqual(unit["Timer"]["Persistent"], "true")
        self.assertEqual(unit["Timer"]["Unit"], "pi-usage-dashboard-preview.service")
        self.assertNotEqual(unit["Timer"]["Unit"], legacy["Timer"]["Unit"])
        self.assertEqual(unit["Install"]["WantedBy"], "timers.target")

    def test_preview_timer_has_no_legacy_or_network_coupling(self):
        unit = load_unit("pi-usage-dashboard-preview.timer")
        self.assertEqual(set(unit.sections()), {"Unit", "Timer", "Install"})
        self.assertEqual(set(unit["Unit"]), {"description"})
        self.assertEqual(set(unit["Timer"]), {"oncalendar", "persistent", "unit"})
        self.assertEqual(set(unit["Install"]), {"wantedby"})


@unittest.skipUnless(shutil.which("npm") and shutil.which("node"), "npm and node required")
class BuildMarkerTests(unittest.TestCase):
    def run_disposable_build(self, *, with_marker, exit_code=0):
        package = json.loads(
            (UNITS.parent / "web" / "package.json").read_text(encoding="utf-8")
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dist = root / "dist"
            dist.mkdir()
            marker = dist / ".verified"
            if with_marker:
                marker.write_text("previous attestation", encoding="utf-8")
            sibling = dist / "keep.txt"
            sibling.write_text("unrelated asset", encoding="utf-8")
            # Run the shipped hook through npm, replacing only Vite with an
            # observer that asserts invalidation happened BEFORE the build.
            (root / "observe.cjs").write_text(
                "const fs = require('node:fs');\n"
                "const assert = require('node:assert/strict');\n"
                "assert.equal(fs.existsSync('dist/.verified'), false);\n"
                "assert.equal(fs.readFileSync('dist/keep.txt', 'utf8'), 'unrelated asset');\n"
                "console.log('marker absent before build; sibling preserved');\n"
                f"process.exit({exit_code});\n",
                encoding="utf-8",
            )
            (root / "package.json").write_text(
                json.dumps({"private": True, "scripts": {
                    "prebuild": package["scripts"]["prebuild"],
                    "build": "node observe.cjs",
                }}),
                encoding="utf-8",
            )
            environment = dict(os.environ)
            environment.update({
                "npm_config_userconfig": str(root / "user.npmrc"),
                "npm_config_globalconfig": str(root / "global.npmrc"),
                "npm_config_cache": str(root / "npm-cache"),
                "npm_config_ignore_scripts": "false",
            })
            result = subprocess.run(
                ["npm", "run", "build"], cwd=root, env=environment,
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, exit_code, result.stdout + result.stderr)
            self.assertIn("marker absent before build; sibling preserved", result.stdout)
            self.assertFalse(marker.exists())
            self.assertEqual(sibling.read_text(encoding="utf-8"), "unrelated asset")

    def test_build_invalidates_marker_before_output_changes_and_preserves_sibling(self):
        self.run_disposable_build(with_marker=True)

    def test_build_succeeds_without_marker_and_preserves_sibling(self):
        self.run_disposable_build(with_marker=False)

    def test_failed_build_leaves_marker_invalidated_and_preserves_sibling(self):
        self.run_disposable_build(with_marker=True, exit_code=7)


if __name__ == "__main__":
    unittest.main()
