#!/usr/bin/env bash
# Start a monitored Author/Critic run with server-friendly logging.
#
# Modes:
#   smoke  - cheap one-round dummy run for plumbing checks
#   run    - actual long Fable-capable run on a supplied problem
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/run_fable_big.sh smoke [options]
  scripts/run_fable_big.sh run --problem problems/example.txt [options]
  scripts/run_fable_big.sh run --problem-text "..." --problem-id my_problem [options]

Options:
  --workflow NAME_OR_PATH          Workflow preset (default: author_critic_fable_author)
  --run-id RUN                    Run id (default: fable-MODE-YYYYMMDD-HHMMSS)
  --run-name NAME                 Dashboard display name
  --output DIR                    Outputs root (default: outputs)
  --problem PATH                  Problem file for run mode
  --problem-text TEXT             Inline problem text for run mode
  --problem-id ID                 Stable problem id
  --llm-monitor                   Also enable in-workflow LLM monitor summaries
  --monitor-model MODEL           Monitor model config (default: models/openai/gpt-54-mini)
  --budget-usd USD                Override budget
  --additional-instructions TEXT  Extra run instructions
  --input KEY=VALUE               Extra preset input override, repeatable
  --model AGENT=MODEL             Extra model override, repeatable
  --component AGENT.KEY=VALUE     Extra component override, repeatable

Examples:
  scripts/run_fable_big.sh smoke
  scripts/run_fable_big.sh run --problem problems/my_problem.tex --run-id fable-real-001

After launch, check the run with:
  scripts/monitor_fable_run.py RUN_ID --output outputs
  scripts/watch_run.sh RUN_ID 5005
EOF
}

now_iso() {
  date '+%Y-%m-%dT%H:%M:%S%z'
}

has_env_key() {
  key="$1"
  eval "value=\${$key:-}"
  if [[ -n "$value" ]]; then
    return 0
  fi
  if [[ "${PROOFSTACK_RUN_FABLE_DISABLE_DOTENV:-}" != "1" ]] \
    && [[ -f .env ]] \
    && grep -Eq "^[[:space:]]*(export[[:space:]]+)?${key}=" .env; then
    return 0
  fi
  return 1
}

require_env_key() {
  key="$1"
  if has_env_key "$key"; then
    return 0
  fi
  echo "missing required environment key: $key" >&2
  echo "Set it in the shell or in .env before starting this monitored run." >&2
  return 78
}

if [[ $# -lt 1 ]]; then
  usage
  exit 64
fi

MODE="$1"
shift
case "$MODE" in
  smoke|run) ;;
  -h|--help)
    usage
    exit 0
    ;;
  *)
    echo "unknown mode: $MODE" >&2
    usage >&2
    exit 64
    ;;
esac

WORKFLOW="author_critic_fable_author"
OUTPUT_ROOT="outputs"
RUN_ID=""
RUN_NAME=""
PROBLEM_ARG=()
PROBLEM_ID=""
MONITOR_MODEL="models/openai/gpt-54-mini"
LLM_MONITOR=false
BUDGET_USD=""
ADDITIONAL_INSTRUCTIONS=""
PASSTHROUGH=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workflow)
      WORKFLOW="${2:?missing value for --workflow}"
      shift 2
      ;;
    --run-id)
      RUN_ID="${2:?missing value for --run-id}"
      shift 2
      ;;
    --run-name)
      RUN_NAME="${2:?missing value for --run-name}"
      shift 2
      ;;
    --output)
      OUTPUT_ROOT="${2:?missing value for --output}"
      shift 2
      ;;
    --problem)
      PROBLEM_ARG=(--problem "${2:?missing value for --problem}")
      shift 2
      ;;
    --problem-text)
      PROBLEM_ARG=(--problem-text "${2:?missing value for --problem-text}")
      shift 2
      ;;
    --problem-id)
      PROBLEM_ID="${2:?missing value for --problem-id}"
      shift 2
      ;;
    --llm-monitor)
      LLM_MONITOR=true
      shift
      ;;
    --monitor-model)
      MONITOR_MODEL="${2:?missing value for --monitor-model}"
      shift 2
      ;;
    --budget-usd)
      BUDGET_USD="${2:?missing value for --budget-usd}"
      shift 2
      ;;
    --additional-instructions)
      ADDITIONAL_INSTRUCTIONS="${2:?missing value for --additional-instructions}"
      shift 2
      ;;
    --input|--model|--component)
      PASSTHROUGH+=("$1" "${2:?missing value for $1}")
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 64
      ;;
  esac
done

timestamp="$(date +%Y%m%d-%H%M%S)"
if [[ -z "$RUN_ID" ]]; then
  RUN_ID="fable-${MODE}-${timestamp}"
fi

DEFAULT_ARGS=()
if [[ "$MODE" == "smoke" ]]; then
  PROBLEM_ARG=(
    --problem-text
    "Infrastructure smoke test. Produce a tiny correct proof that 2+2=4. Keep the output short. If your answer is complete, include <ready>true</ready>."
  )
  PROBLEM_ID="${PROBLEM_ID:-fable_smoke_dummy}"
  RUN_NAME="${RUN_NAME:-Fable infrastructure smoke}"
  WORKFLOW="${WORKFLOW:-author_critic}"
  DEFAULT_ARGS=(
    --input n_rounds=1
    --input full_critic_interval=20
    --input enable_council=false
    --input enable_compute=false
    --input enable_final_critic=false
    --input stop_after_review_round=true
    --model Author=models/openai/gpt-54-mini
    --model ACCritic=models/openai/gpt-54-mini
  )
  BUDGET_USD="${BUDGET_USD:-1}"
fi

if [[ "$MODE" == "run" && ${#PROBLEM_ARG[@]} -eq 0 ]]; then
  echo "run mode requires --problem or --problem-text" >&2
  usage >&2
  exit 64
fi

if [[ "$MODE" == "smoke" ]]; then
  require_env_key OPENAI_API_KEY
fi

mkdir -p "$OUTPUT_ROOT/$RUN_ID"
LOG="$OUTPUT_ROOT/$RUN_ID/terminal.log"

cmd=(
  uv run python scripts/run_workflow.py
  --workflow "$WORKFLOW"
  "${PROBLEM_ARG[@]}"
  --problem-id "${PROBLEM_ID:-$RUN_ID}"
  --run-id "$RUN_ID"
  --output "$OUTPUT_ROOT"
)

if [[ "$LLM_MONITOR" == "true" ]]; then
  cmd+=(--monitor --monitor-model "$MONITOR_MODEL")
fi

if [[ -n "$RUN_NAME" ]]; then
  cmd+=(--run-name "$RUN_NAME")
fi
if [[ -n "$BUDGET_USD" ]]; then
  cmd+=(--budget-usd "$BUDGET_USD")
fi
if [[ -n "$ADDITIONAL_INSTRUCTIONS" ]]; then
  cmd+=(--additional-instructions "$ADDITIONAL_INSTRUCTIONS")
fi
if [[ ${#DEFAULT_ARGS[@]} -gt 0 ]]; then
  cmd+=("${DEFAULT_ARGS[@]}")
fi
if [[ ${#PASSTHROUGH[@]} -gt 0 ]]; then
  cmd+=("${PASSTHROUGH[@]}")
fi

{
  echo "started_at: $(now_iso)"
  echo "host: $(hostname)"
  echo "run_id: $RUN_ID"
  echo "workflow: $WORKFLOW"
  echo "output: $OUTPUT_ROOT/$RUN_ID"
  echo "command:"
  printf ' %q' "${cmd[@]}"
  echo
  echo
} | tee -a "$LOG"

set +e
"${cmd[@]}" 2>&1 | tee -a "$LOG"
status=${PIPESTATUS[0]}
set -e

{
  echo
  echo "finished_at: $(now_iso)"
  echo "exit_status: $status"
  echo
  echo "Post-run monitor:"
} | tee -a "$LOG"

set +e
scripts/monitor_fable_run.py "$RUN_ID" --output "$OUTPUT_ROOT" 2>&1 | tee -a "$LOG"
monitor_status=${PIPESTATUS[0]}
set -e

{
  echo
  echo "Next checks:"
  echo "  scripts/monitor_fable_run.py $RUN_ID --output $OUTPUT_ROOT"
  echo "  scripts/watch_run.sh $RUN_ID 5005"
} | tee -a "$LOG"

if [[ "$status" -ne 0 ]]; then
  exit "$status"
fi
exit "$monitor_status"
