#!/usr/bin/env bash
# Daily gas-dashboard refresh, run on the stocks server (replaces the old
# GitHub Actions workflow to avoid paying for Actions minutes + a FlareSolverr
# service container on every run). Fetches prices + local headlines, bakes
# them into index.html/news.html, and pushes straight to main -- Pages
# serves directly from main/root.
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

# Each fetch leaves its own output files untouched on failure (see their
# "No .../ returned" guards), so a failure in one must not block the other
# from being committed -- hence the `|| log ...` instead of letting `set -e`
# abort the whole script.
FLARESOLVERR_URL="http://localhost:8191/v1" "${REPO_ROOT}/.venv/bin/python" scripts/fetch_prices.py >>"${LOG_FILE}" 2>&1 \
  || log "fetch_prices.py failed, leaving prices.json/index.html untouched"

"${REPO_ROOT}/.venv/bin/python" scripts/fetch_news.py >>"${LOG_FILE}" 2>&1 \
  || log "fetch_news.py failed, leaving news.json/news.html untouched"

# Reads history.json (written by fetch_prices.py above) — no network calls,
# so only worth running if that succeeded and left fresh history behind.
"${REPO_ROOT}/.venv/bin/python" scripts/fetch_trends.py >>"${LOG_FILE}" 2>&1 \
  || log "fetch_trends.py failed, leaving trends.json/trends.html untouched"

git add prices.json index.html news.json news.html history.json trends.json trends.html
if git diff --staged --quiet; then
  log "no changes, nothing to commit"
else
  git commit -q -m "chore: update gas prices + local news + trends [skip ci]"
  git push origin main >>"${LOG_FILE}" 2>&1
  log "pushed updates"
fi
