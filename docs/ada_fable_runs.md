# Ada Fable Runs

This is the server-side operating recipe for long Fable Author/Critic runs on
the D-MATH `ada-*` machines.

## Goal

For live tests, use one of two safe starts:

1. Run a cheap smoke test with a dummy problem and low-cost model overrides.
2. Start the real workflow and have Codex or Claude monitor the first hour or
   two carefully, reading the run logs for failed calls, broken tools, stalled
   model calls, sandbox failures, and missing monitor summaries.

The scripts below are designed for that exact loop.

## Server Setup

On an `ada-*` host, use `tmux` so the run survives laptop, Wi-Fi, or VPN loss.

```bash
tmux new -As fable
mkdir -p ~/work
cd ~/work
git clone https://github.com/ukreitner/proof-council.git
cd proof-council
git checkout codex/podman-monitoring
uv sync
```

If the repo is already cloned:

```bash
cd ~/work/proof-council
git fetch origin
git checkout codex/podman-monitoring
git pull --ff-only
uv sync
```

## Podman Sandbox Images

The D-MATH compute worker should use containers created by Podman, not Docker.
The Fable workflow presets set `compute_container_runtime: podman`.

Build the sandbox images before a real run:

```bash
podman build -t proofstack-sandbox:latest deploy/sandbox/
podman build -t proofstack-pwc-sandbox:latest -f deploy/sandbox/Dockerfile.pwc deploy/sandbox/
podman image inspect proofstack-pwc-sandbox:latest >/dev/null
```

If the Podman build fails, stop and fix that before launching a long run.

## Cheap Smoke Test

This checks the workflow runner, event log, dashboard data, external monitor
script, and basic API plumbing without burning a real Fable run.

```bash
scripts/run_fable_big.sh smoke --run-id fable-smoke-001
scripts/monitor_fable_run.py fable-smoke-001
```

The smoke mode uses a dummy problem, one review round, no council, no compute
worker, cheap model overrides, and a `$1` run budget by default. It is not
meant to validate Fable reasoning quality.

By default, monitoring means the external log reader
`scripts/monitor_fable_run.py`, which does not make model calls. If you also
want in-workflow LLM monitor summaries, pass `--llm-monitor`; that can add
extra API calls and latency.

## Real Monitored Run

Start the real run from inside `tmux`:

```bash
scripts/run_fable_big.sh run \
  --workflow author_critic_fable_author \
  --problem problems/my_problem.tex \
  --run-id fable-real-001 \
  --run-name "Fable real run 001"
```

Detach from tmux with `Ctrl-b` then `d`. Reattach later with:

```bash
tmux attach -t fable
```

## First-Hour Monitoring

In a second tmux pane or a second SSH session:

```bash
cd ~/work/proof-council
scripts/monitor_fable_run.py fable-real-001
tail -f outputs/fable-real-001/terminal.log
```

If the dashboard is useful on the host:

```bash
scripts/run_dashboard.sh 5005
```

Then inspect with:

```bash
scripts/watch_run.sh fable-real-001 5005
scripts/run_status.sh fable-real-001 5005
```

During the first hour or two, explicitly check:

- `scripts/monitor_fable_run.py` reports zero error-looking events.
- Pending model calls are plausible for the model being used, not silently stuck
  after repeated failures.
- Monitor summaries appear after completed nodes when `--monitor` is enabled.
- `outputs/<run-id>/terminal.log` does not show provider errors, sandbox errors,
  missing binaries, auth failures, or Podman image failures.
- `outputs/<run-id>/resume_cache/` starts accumulating completed nodes.
- If a compute worker is requested, Podman starts `proofstack-pwc-sandbox:latest`
  and the compute worker writes its response file.

If anything looks wrong, stop the run from the dashboard or interrupt it in tmux,
fix the issue, and resume with:

```bash
uv run python scripts/run_workflow.py --workflow author_critic_fable_author --restart-from fable-real-001
```

## Quick Health Command

This is the command to give a monitoring Codex/Claude agent:

```bash
cd ~/work/proof-council
scripts/monitor_fable_run.py fable-real-001
tail -80 outputs/fable-real-001/terminal.log
find outputs/fable-real-001/resume_cache -maxdepth 1 -type f | wc -l
```
