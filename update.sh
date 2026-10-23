#!/usr/bin/env bash
# Manual refresh: pull the latest prices, regenerate the dashboard, open it.
# Read-only on git; committing and publishing is runner.sh's job.

set -euo pipefail

cd "$(dirname "$0")"

echo "[$(date)] fetching latest prices…"
python3 scripts/fetch.py

echo "[$(date)] regenerating dashboard…"
python3 scripts/report.py

LATEST_REPORT=$(ls -t reports/*.html | head -1)
echo "  dashboard:   $(pwd)/index.html"
echo "  dated snapshot: ${LATEST_REPORT}"

echo "[$(date)] opening dashboard…"
if command -v open >/dev/null 2>&1; then
  open index.html
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open index.html
else
  echo "  → open $(pwd)/index.html in your browser"
fi

echo "[$(date)] done."
