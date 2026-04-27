#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"

CLIENTS=(freshco techparts greenleaf quickship nordicsteel)
CORE_CONFIGS=(C0 C1 C2 C3 C4 C3+C4 C5 C6 C7)
CONVERGENCE_CONFIGS=(C0 C1)
FAILURE_CONFIGS=(C0 C1 C2 C7)

CORE_REPS="${CORE_REPS:-10}"
CONVERGENCE_REPS="${CONVERGENCE_REPS:-30}"
FAILURE_REPS="${FAILURE_REPS:-2}"
SMOKE_REPS="${SMOKE_REPS:-1}"

RESULTS_DIR="evaluation/results"
REPORT_DIR="${RESULTS_DIR}/report"

RUNTIME_SERVICES=(
  api_service
  orchestrator_core
  call-transcription-agent
  deal-summary-agent
  client-necessity-agent
  proposal-template-agent
  warehouse-agent-north
  warehouse-agent-central
  warehouse-agent-south
  cost-estimator-agent
  speed-estimator-agent
  compliance-agent
  graph-builder-agent
  plan-validator-agent
  plan-cache-agent
  llm-strategist-agent
  ethics-checker-agent
)

INFRA_SERVICES=(
  rabbitmq
  couchbase
  couchbase-setup
  postgres
  fuseki
)

join_with_underscore() {
  local joined=""
  local item
  for item in "$@"; do
    if [[ -n "$joined" ]]; then
      joined+="_"
    fi
    joined+="$item"
  done
  printf "%s" "$joined"
}

manifest_path_for() {
  local joined
  joined="$(join_with_underscore "$@")"
  printf "%s/batch_manifest_%s.json" "$RESULTS_DIR" "$joined"
}

ensure_dirs() {
  mkdir -p "$RESULTS_DIR" "$RESULTS_DIR/archive" "$REPORT_DIR/csv"
}

archive_existing_results() {
  ensure_dirs
  local stamp
  stamp="${1:-$(date +%Y%m%d_%H%M%S)}"
  local archive_dir="${RESULTS_DIR}/archive/${stamp}"
  mkdir -p "$archive_dir"

  local moved=0
  local path
  for path in \
    "${RESULTS_DIR}/run_results.json" \
    "${RESULTS_DIR}/run_results.csv" \
    "${RESULTS_DIR}"/batch_manifest_*.json \
    "${REPORT_DIR}"; do
    if compgen -G "$path" > /dev/null 2>&1 || [[ -e "$path" ]]; then
      mv $path "$archive_dir"/
      moved=1
    fi
  done

  if [[ "$moved" -eq 1 ]]; then
    printf "Archived previous outputs to %s\n" "$archive_dir"
  else
    rmdir "$archive_dir"
  fi

  mkdir -p "$REPORT_DIR/csv"
}

reset_current_outputs() {
  rm -f "${RESULTS_DIR}/run_results.json" "${RESULTS_DIR}/run_results.csv"
  rm -f "${RESULTS_DIR}"/batch_manifest_*.json
  rm -rf "$REPORT_DIR"
  mkdir -p "$REPORT_DIR/csv"
}

check_api() {
  local tries=0
  until curl -fsS http://127.0.0.1:8082/health >/dev/null 2>&1; do
    tries=$((tries + 1))
    if [[ "$tries" -ge 60 ]]; then
      printf "API did not become healthy on http://127.0.0.1:8082\n" >&2
      return 1
    fi
    sleep 2
  done
}

bootstrap_stack() {
  printf "Starting infrastructure and runtime services...\n"
  docker compose up -d "${INFRA_SERVICES[@]}"
  docker compose up -d --no-deps "${RUNTIME_SERVICES[@]}"
  check_api
  printf "Stack is ready.\n"
}

collect_manifest() {
  local manifest="$1"
  if [[ ! -f "$manifest" ]]; then
    printf "Manifest not found: %s\n" "$manifest" >&2
    return 1
  fi
  "$PYTHON_BIN" -m evaluation.results_collector --manifest "$manifest" --append
}

run_core_batch() {
  local args=()
  if [[ "${RESUME_FLAG:-0}" -eq 1 ]]; then
    args+=(--resume)
  fi
  "$PYTHON_BIN" -m evaluation.batch_runner \
    --configs "${CORE_CONFIGS[@]}" \
    --clients "${CLIENTS[@]}" \
    --reps "$CORE_REPS" \
    "${args[@]}"
  collect_manifest "$(manifest_path_for "${CORE_CONFIGS[@]}")"
}

run_convergence_batch() {
  local args=()
  if [[ "${RESUME_FLAG:-0}" -eq 1 ]]; then
    args+=(--resume)
  fi
  "$PYTHON_BIN" -m evaluation.batch_runner \
    --configs "${CONVERGENCE_CONFIGS[@]}" \
    --clients "${CLIENTS[@]}" \
    --reps "$CONVERGENCE_REPS" \
    --convergence \
    "${args[@]}"
  collect_manifest "$(manifest_path_for "${CONVERGENCE_CONFIGS[@]}")"
}

run_failure_batch() {
  local args=()
  if [[ "${RESUME_FLAG:-0}" -eq 1 ]]; then
    args+=(--resume)
  fi
  "$PYTHON_BIN" -m evaluation.batch_runner \
    --configs "${FAILURE_CONFIGS[@]}" \
    --clients "${CLIENTS[@]}" \
    --reps "$FAILURE_REPS" \
    --failure-test \
    "${args[@]}"
  collect_manifest "$(manifest_path_for "${FAILURE_CONFIGS[@]}")"
}

run_seeded_validation() {
  "$PYTHON_BIN" -m evaluation.seeded_violations \
    --output "${REPORT_DIR}/csv/seeded_violations.csv"
}

generate_report() {
  "$PYTHON_BIN" -m evaluation.report_generator
}

run_smoke() {
  local args=()
  if [[ "${RESUME_FLAG:-0}" -eq 1 ]]; then
    args+=(--resume)
  fi
  "$PYTHON_BIN" -m evaluation.batch_runner \
    --configs "${CORE_CONFIGS[@]}" \
    --clients freshco \
    --reps "$SMOKE_REPS" \
    "${args[@]}"
  collect_manifest "$(manifest_path_for "${CORE_CONFIGS[@]}")"

  "$PYTHON_BIN" -m evaluation.batch_runner \
    --configs "${FAILURE_CONFIGS[@]}" \
    --clients freshco \
    --reps 1 \
    --failure-test \
    "${args[@]}"
  collect_manifest "$(manifest_path_for "${FAILURE_CONFIGS[@]}")"

  run_seeded_validation
  generate_report
}

run_all() {
  if [[ "${RESUME_FLAG:-0}" -eq 1 ]]; then
    printf "Resume mode enabled; keeping current manifests and result files.\n"
  else
    archive_existing_results
    reset_current_outputs
  fi
  bootstrap_stack
  run_core_batch
  run_convergence_batch
  run_failure_batch
  run_seeded_validation
  generate_report

  printf "\nFull experiment suite completed.\n"
  printf "Results: %s/run_results.json\n" "$RESULTS_DIR"
  printf "CSV:     %s/run_results.csv\n" "$RESULTS_DIR"
  printf "Report:  %s/chapter5_results.md\n" "$REPORT_DIR"
}

show_help() {
  cat <<'EOF'
Usage:
  ./scripts/run_logistics_experiments.sh bootstrap
  ./scripts/run_logistics_experiments.sh smoke [--resume]
  ./scripts/run_logistics_experiments.sh core [--resume]
  ./scripts/run_logistics_experiments.sh convergence [--resume]
  ./scripts/run_logistics_experiments.sh failure [--resume]
  ./scripts/run_logistics_experiments.sh seeded
  ./scripts/run_logistics_experiments.sh report
  ./scripts/run_logistics_experiments.sh all [--resume]

Environment overrides:
  PYTHON_BIN=python3
  CORE_REPS=10
  CONVERGENCE_REPS=30
  FAILURE_REPS=2
  SMOKE_REPS=1

Notes:
  - `all` archives previous outputs under evaluation/results/archive/<timestamp>/ unless `--resume` is used.
  - Add `--resume` to continue from saved batch manifests without rerunning completed runs.
  - `core` runs the 450-run ablation matrix from the design.
  - `convergence` runs the 300-run C0/C1 convergence study.
  - `failure` runs the 40-run failure-injection study.
EOF
}

main() {
  ensure_dirs
  local cmd="${1:-all}"
  shift || true
  RESUME_FLAG=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --resume)
        RESUME_FLAG=1
        ;;
      *)
        printf "Unknown option: %s\n" "$1" >&2
        show_help
        exit 1
        ;;
    esac
    shift
  done

  case "$cmd" in
    bootstrap)
      bootstrap_stack
      ;;
    smoke)
      bootstrap_stack
      run_smoke
      ;;
    core)
      bootstrap_stack
      run_core_batch
      ;;
    convergence)
      bootstrap_stack
      run_convergence_batch
      ;;
    failure)
      bootstrap_stack
      run_failure_batch
      ;;
    seeded)
      run_seeded_validation
      ;;
    report)
      generate_report
      ;;
    all)
      run_all
      ;;
    help|-h|--help)
      show_help
      ;;
    *)
      printf "Unknown command: %s\n\n" "$cmd" >&2
      show_help
      return 1
      ;;
  esac
}

main "$@"
