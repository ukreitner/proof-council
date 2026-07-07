#!/usr/bin/env python3
"""Summarize early health signals for a long Fable/Author-Critic run.

The dashboard is the nicest way to inspect a run interactively, but on a
remote tmux session we also want a plain terminal check that reads the actual
event log and highlights failure-looking signals.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Read outputs/<run-id>/events.jsonl and print run health.")
    p.add_argument("run_id", help="Run id under --output.")
    p.add_argument("--output", type=Path, default=Path("outputs"), help="Outputs root.")
    p.add_argument("--tail", type=int, default=12, help="Recent events to print.")
    p.add_argument(
        "--stale-minutes",
        type=float,
        default=30.0,
        help="Warn if a non-finished run has no event newer than this.",
    )
    p.add_argument(
        "--no-fail",
        action="store_true",
        help="Always exit 0 even when errors or staleness are detected.",
    )
    return p


def main() -> int:
    args = _argparser().parse_args()
    run_dir = args.output / args.run_id
    report, status = build_report(
        run_dir,
        tail=max(0, args.tail),
        stale_minutes=args.stale_minutes,
    )
    print(report)
    return 0 if args.no_fail else status


def build_report(run_dir: Path, *, tail: int = 12, stale_minutes: float = 30.0) -> tuple[str, int]:
    lines: list[str] = []
    if not run_dir.exists():
        return f"run directory not found: {run_dir}", 4

    events_path = run_dir / "events.jsonl"
    events, malformed = _read_jsonl(events_path)
    metadata = _read_json(run_dir / "run-metadata.json")
    cache_entries = sorted((run_dir / "resume_cache").glob("*.json")) if (run_dir / "resume_cache").exists() else []
    run_log = run_dir / "terminal.log"

    lines.append(f"run: {run_dir.name}")
    lines.append(f"path: {run_dir}")
    lines.append(f"events: {len(events)}")
    if malformed:
        lines.append(f"malformed event lines: {malformed}")
    lines.append(f"resume cache entries: {len(cache_entries)}")
    if run_log.exists():
        lines.append(f"terminal log: {run_log}")

    status = _run_status(metadata, events)
    lines.append(f"status: {status}")

    if events:
        last = events[-1]
        lines.append(f"last event: {_format_event(last)}")
    else:
        lines.append("last event: none")

    counts = Counter(str(evt.get("kind") or "<missing>") for evt in events)
    if counts:
        compact_counts = ", ".join(f"{kind}={count}" for kind, count in counts.most_common(12))
        lines.append(f"event counts: {compact_counts}")

    error_events = _error_events(events)
    pending_model_calls = _pending_model_calls(events)
    tool_events = _tool_events(events)
    monitor_summaries = _monitor_summaries(events)
    stale = _is_stale(events, metadata, stale_minutes=stale_minutes)

    lines.append("")
    lines.append("health checks:")
    lines.append(f"- error-looking events: {len(error_events)}")
    lines.append(f"- pending model calls: {len(pending_model_calls)}")
    lines.append(f"- tool-related events: {len(tool_events)}")
    lines.append(f"- monitor summaries: {len(monitor_summaries)}")
    if stale:
        lines.append(f"- stale: no event in the last {stale_minutes:g} minutes")
    else:
        lines.append("- stale: no")

    if error_events:
        lines.append("")
        lines.append("error-looking events:")
        for evt in error_events[-8:]:
            lines.append(f"- {_format_event(evt)}")

    if pending_model_calls:
        lines.append("")
        lines.append("pending model calls:")
        for evt in pending_model_calls[-8:]:
            lines.append(f"- {_format_event(evt)}")

    if monitor_summaries:
        lines.append("")
        lines.append("latest monitor summaries:")
        for evt in monitor_summaries[-5:]:
            payload = evt.get("payload") if isinstance(evt.get("payload"), dict) else {}
            label = payload.get("display_label") or payload.get("agent") or "monitor"
            summary = str(payload.get("summary") or "").replace("\n", " ").strip()
            lines.append(f"- {evt.get('ts', '?')} {label}: {_shorten(summary, 260)}")

    if tail and events:
        lines.append("")
        lines.append(f"recent events (last {min(tail, len(events))}):")
        for evt in events[-tail:]:
            lines.append(f"- {_format_event(evt)}")

    exit_code = 0
    if error_events:
        exit_code = 2
    elif stale:
        exit_code = 3
    return "\n".join(lines), exit_code


def _read_jsonl(path: Path) -> tuple[list[dict[str, Any]], int]:
    if not path.exists():
        return [], 0
    events: list[dict[str, Any]] = []
    malformed = 0
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if isinstance(event, dict):
                events.append(event)
            else:
                malformed += 1
    return events, malformed


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _run_status(metadata: dict[str, Any], events: list[dict[str, Any]]) -> str:
    meta_status = metadata.get("status")
    if meta_status:
        return str(meta_status)
    for evt in reversed(events):
        if evt.get("kind") != "run.end":
            continue
        payload = evt.get("payload") if isinstance(evt.get("payload"), dict) else {}
        return str(payload.get("status") or "ended")
    if events:
        return "running"
    return "not-started"


def _format_event(evt: dict[str, Any]) -> str:
    payload = evt.get("payload") if isinstance(evt.get("payload"), dict) else {}
    parts = [
        str(evt.get("ts") or "?"),
        str(evt.get("kind") or "<missing-kind>"),
    ]
    agent = evt.get("agent_path") or evt.get("agent")
    if agent:
        parts.append(str(agent))
    call_id = evt.get("call_id")
    if call_id:
        parts.append(f"call={call_id}")
    for key in ("status", "type", "via", "model", "role"):
        value = payload.get(key)
        if value not in (None, ""):
            parts.append(f"{key}={_shorten(str(value), 80)}")
    msg = payload.get("msg") or payload.get("summary") or payload.get("error")
    if msg:
        parts.append(f"msg={_shorten(str(msg).replace(chr(10), ' '), 160)}")
    return " | ".join(parts)


def _error_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for evt in events:
        kind = str(evt.get("kind") or "")
        payload = evt.get("payload") if isinstance(evt.get("payload"), dict) else {}
        status = str(payload.get("status") or "").lower()
        has_error_payload = payload.get("error") not in (None, "", False)
        if kind.endswith(".error") or kind == "agent.error" or status == "error" or has_error_payload:
            out.append(evt)
    return out


def _pending_model_calls(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    starts: dict[str, dict[str, Any]] = {}
    done: set[str] = set()
    for evt in events:
        call_id = evt.get("call_id")
        if not call_id:
            continue
        kind = evt.get("kind")
        if kind == "model.call.start":
            starts[str(call_id)] = evt
        elif kind == "model.call":
            done.add(str(call_id))
    return [evt for cid, evt in starts.items() if cid not in done]


def _tool_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for evt in events:
        kind = str(evt.get("kind") or "").lower()
        payload = evt.get("payload") if isinstance(evt.get("payload"), dict) else {}
        if "tool" in kind or any("tool" in str(key).lower() for key in payload):
            out.append(evt)
    return out


def _monitor_summaries(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [evt for evt in events if evt.get("kind") == "monitor.summary"]


def _is_stale(
    events: list[dict[str, Any]],
    metadata: dict[str, Any],
    *,
    stale_minutes: float,
) -> bool:
    if stale_minutes <= 0 or not events:
        return False
    status = _run_status(metadata, events)
    if status in {"ok", "error", "ended"}:
        return False
    last_ts = _parse_ts(events[-1].get("ts"))
    if last_ts is None:
        return False
    age_s = (datetime.now(timezone.utc) - last_ts).total_seconds()
    return age_s > stale_minutes * 60


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _shorten(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)] + "..."


if __name__ == "__main__":
    raise SystemExit(main())
