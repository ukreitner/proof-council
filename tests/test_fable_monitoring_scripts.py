from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MONITOR_PATH = ROOT / "scripts" / "monitor_fable_run.py"


def _load_monitor_module():
    spec = importlib.util.spec_from_file_location("monitor_fable_run", MONITOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load monitor_fable_run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_event(path: Path, **event) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")


class FableMonitoringScriptTests(unittest.TestCase):
    def test_monitor_report_flags_errors_and_pending_model_calls(self) -> None:
        monitor = _load_monitor_module()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "outputs" / "fable-test"
            run_dir.mkdir(parents=True)
            events = run_dir / "events.jsonl"
            _write_event(
                events,
                ts="2026-07-07T10:00:00.000Z",
                kind="model.call.start",
                call_id="model-1",
                payload={"model": "models/openai/gpt-54-mini"},
            )
            _write_event(
                events,
                ts="2026-07-07T10:00:01.000Z",
                kind="tool.call",
                call_id="tool-1",
                payload={"tool": "code_interpreter"},
            )
            _write_event(
                events,
                ts="2026-07-07T10:00:02.000Z",
                kind="monitor.summary",
                call_id="mon-1",
                payload={
                    "display_label": "Author",
                    "summary": "The author returned a tiny draft.",
                },
            )
            _write_event(
                events,
                ts="2026-07-07T10:00:03.000Z",
                kind="agent.error",
                call_id="agent-1",
                agent="Compute",
                payload={"type": "RuntimeError", "msg": "podman image missing"},
            )

            report, status = monitor.build_report(run_dir, stale_minutes=0)

        self.assertEqual(status, 2)
        self.assertIn("error-looking events: 1", report)
        self.assertIn("pending model calls: 1", report)
        self.assertIn("tool-related events: 1", report)
        self.assertIn("monitor summaries: 1", report)
        self.assertIn("podman image missing", report)

    def test_monitor_report_marks_completed_model_call_not_pending(self) -> None:
        monitor = _load_monitor_module()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "outputs" / "fable-test"
            run_dir.mkdir(parents=True)
            events = run_dir / "events.jsonl"
            _write_event(
                events,
                ts="2026-07-07T10:00:00.000Z",
                kind="model.call.start",
                call_id="model-1",
                payload={"model": "models/openai/gpt-54-mini"},
            )
            _write_event(
                events,
                ts="2026-07-07T10:00:02.000Z",
                kind="model.call",
                call_id="model-1",
                payload={"model": "models/openai/gpt-54-mini", "cost_usd": 0.01},
            )

            report, status = monitor.build_report(run_dir, stale_minutes=0)

        self.assertEqual(status, 0)
        self.assertIn("pending model calls: 0", report)

    def test_scripts_have_valid_syntax(self) -> None:
        subprocess.run(
            ["bash", "-n", str(ROOT / "scripts" / "run_fable_big.sh")],
            check=True,
        )
        subprocess.run(
            [sys.executable, "-m", "py_compile", str(MONITOR_PATH)],
            check=True,
        )

    def test_runbook_captures_first_hour_and_podman_requirements(self) -> None:
        text = (ROOT / "docs" / "ada_fable_runs.md").read_text(encoding="utf-8")

        self.assertIn("first hour or two", text)
        self.assertIn("Podman", text)
        self.assertIn("scripts/monitor_fable_run.py", text)
        self.assertIn("scripts/run_fable_big.sh smoke", text)

    def test_launcher_defaults_to_monitor_and_smoke_overrides(self) -> None:
        text = (ROOT / "scripts" / "run_fable_big.sh").read_text(encoding="utf-8")

        self.assertIn("--monitor", text)
        self.assertIn("models/openai/gpt-54-mini", text)
        self.assertIn("--input enable_compute=false", text)
        self.assertIn("terminal.log", text)


if __name__ == "__main__":
    unittest.main()
