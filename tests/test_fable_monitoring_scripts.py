from __future__ import annotations

import importlib.util
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MONITOR_PATH = ROOT / "scripts" / "monitor_fable_run.py"
sys.path.insert(0, str(ROOT / "src"))


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

    def test_launcher_smoke_runs_with_fake_uv_on_empty_passthrough(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_bin = tmp_path / "bin"
            fake_bin.mkdir()
            fake_uv = fake_bin / "uv"
            fake_uv.write_text(
                "#!/usr/bin/env bash\n"
                "printf 'fake uv %s\\n' \"$*\"\n"
                "while [[ $# -gt 0 ]]; do\n"
                "  if [[ \"$1\" == \"--run-id\" ]]; then run_id=\"$2\"; shift 2; continue; fi\n"
                "  if [[ \"$1\" == \"--output\" ]]; then output=\"$2\"; shift 2; continue; fi\n"
                "  shift\n"
                "done\n"
                "mkdir -p \"${output:-outputs}/${run_id:-fake}/resume_cache\"\n"
                "printf '{\"status\":\"ok\"}\\n' > \"${output:-outputs}/${run_id:-fake}/run-metadata.json\"\n"
                "printf '{\"ts\":\"2026-07-07T10:00:00.000Z\",\"kind\":\"run.end\",\"payload\":{\"status\":\"ok\"}}\\n' > \"${output:-outputs}/${run_id:-fake}/events.jsonl\"\n",
                encoding="utf-8",
            )
            fake_uv.chmod(0o755)
            output_root = tmp_path / "outputs"
            env = dict(os.environ)
            env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
            env["OPENAI_API_KEY"] = "fake-key-for-preflight"

            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts" / "run_fable_big.sh"),
                    "smoke",
                    "--run-id",
                    "local-smoke-test",
                    "--output",
                    str(output_root),
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            log = output_root / "local-smoke-test" / "terminal.log"
            self.assertTrue(log.exists())
            text = log.read_text(encoding="utf-8")
            self.assertIn("fake uv run python scripts/run_workflow.py", text)
            self.assertNotIn("--monitor --monitor-model", text)
            self.assertIn("--input enable_compute=false", text)
            self.assertIn("--input stop_after_review_round=true", text)

    def test_launcher_smoke_fails_fast_without_openai_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            env.pop("OPENAI_API_KEY", None)
            env["PROOFSTACK_RUN_FABLE_DISABLE_DOTENV"] = "1"
            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts" / "run_fable_big.sh"),
                    "smoke",
                    "--run-id",
                    "missing-key-smoke",
                    "--output",
                    str(Path(tmp) / "outputs"),
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 78)
        self.assertIn("missing required environment key: OPENAI_API_KEY", result.stderr)

    def test_launcher_defaults_to_external_monitor_and_smoke_overrides(self) -> None:
        text = (ROOT / "scripts" / "run_fable_big.sh").read_text(encoding="utf-8")

        self.assertIn("--llm-monitor", text)
        self.assertIn("models/openai/gpt-54-mini", text)
        self.assertIn("--input enable_compute=false", text)
        self.assertIn("--input stop_after_review_round=true", text)
        self.assertIn("<ready>true</ready>", text)
        self.assertIn('BUDGET_USD="${BUDGET_USD:-1}"', text)
        self.assertIn("terminal.log", text)

    def test_launcher_llm_monitor_flag_enables_run_workflow_monitor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_bin = tmp_path / "bin"
            fake_bin.mkdir()
            fake_uv = fake_bin / "uv"
            fake_uv.write_text(
                "#!/usr/bin/env bash\n"
                "printf 'fake uv %s\\n' \"$*\"\n"
                "while [[ $# -gt 0 ]]; do\n"
                "  if [[ \"$1\" == \"--run-id\" ]]; then run_id=\"$2\"; shift 2; continue; fi\n"
                "  if [[ \"$1\" == \"--output\" ]]; then output=\"$2\"; shift 2; continue; fi\n"
                "  shift\n"
                "done\n"
                "mkdir -p \"${output:-outputs}/${run_id:-fake}/resume_cache\"\n"
                "printf '{\"status\":\"ok\"}\\n' > \"${output:-outputs}/${run_id:-fake}/run-metadata.json\"\n"
                "printf '{\"ts\":\"2026-07-07T10:00:00.000Z\",\"kind\":\"run.end\",\"payload\":{\"status\":\"ok\"}}\\n' > \"${output:-outputs}/${run_id:-fake}/events.jsonl\"\n",
                encoding="utf-8",
            )
            fake_uv.chmod(0o755)
            output_root = tmp_path / "outputs"
            env = dict(os.environ)
            env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
            env["OPENAI_API_KEY"] = "fake-key-for-preflight"

            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts" / "run_fable_big.sh"),
                    "smoke",
                    "--llm-monitor",
                    "--run-id",
                    "local-smoke-test-llm",
                    "--output",
                    str(output_root),
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            text = (output_root / "local-smoke-test-llm" / "terminal.log").read_text(
                encoding="utf-8"
            )
            self.assertIn("--monitor --monitor-model models/openai/gpt-54-mini", text)

    def test_return_block_clamps_negative_round_count(self) -> None:
        from proofstack.agents.ac import visual_blocks
        from proofstack.agents.ac.ac_workflow import _CompileResult
        from proofstack.context import RunContext

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "answer.tex").write_text(
                "\\documentclass{article}\\begin{document}Done.\\end{document}",
                encoding="utf-8",
            )
            (workspace / "research_notes.tex").write_text("", encoding="utf-8")
            (workspace / "references.bib").write_text("", encoding="utf-8")

            def fake_compile(tex, **_kwargs):
                return _CompileResult(
                    tex=tex,
                    tex_path=None,
                    pdf_path=None,
                    compiled=True,
                    pages=1,
                )

            old_compile = visual_blocks._simple_compile_latex
            visual_blocks._simple_compile_latex = fake_compile
            try:
                ctx = RunContext.create(run_id="test", root_workdir=root / "outputs")
                block = visual_blocks.ACReturnBlock(ctx)
                out = asyncio.run(
                    block(
                        state={
                            "inputs": {
                                "problem": "P",
                                "problem_id": "p",
                                "n_rounds": 1,
                            },
                            "workspace": str(workspace),
                            "last_round_run": -1,
                            "early_stopped": True,
                            "review_history": [
                                {
                                    "review_md": "ok",
                                    "answer_ready": True,
                                    "mode": "fresh",
                                    "parse_failed": False,
                                    "messages_after": [],
                                }
                            ],
                        }
                    )
                )
            finally:
                visual_blocks._simple_compile_latex = old_compile

        self.assertEqual(out.rounds_completed, 0)
        self.assertTrue(out.early_stopped)


if __name__ == "__main__":
    unittest.main()
