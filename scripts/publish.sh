#!/usr/bin/env bash
# Daily gas-dashboard refresh, run on the stocks server (replaces the old
# GitHub Actions workflow to avoid paying for Actions minutes + a FlareSolverr
# service container on every run). Fetches prices, bakes them into index.html,
# and pushes straight to main -- Pages serves directly from main/root.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${REPO_ROOT}/.logs"
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/publish.log"

log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "${LOG_FILE}"; }

cd "${REPO_ROOT}"

log "starting gas-dashboard refresh"

git pull --ff-only origin main >>"${LOG_FILE}" 2>&1

for i in $(seq 1 30); do
  curl -sf http://localhost:8191/health >/dev/null && break
  sleep 2
done

FLARESOLVERR_URL="http://localhost:8191/v1" "${REPO_ROOT}/.venv/bin/python" scripts/fetch_prices.py >>"${LOG_FILE}" 2>&1

git add prices.json index.html
if git diff --staged --quiet; then
  log "no changes, nothing to commit"
else
  git commit -q -m "chore: update gas prices [skip ci]"
  git push origin main >>"${LOG_FILE}" 2>&1
  log "pushed updated prices"
fi
