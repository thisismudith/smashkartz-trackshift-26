#!/usr/bin/env bash
# Fetch the five upstream telemetry repositories without modifying their files.
# Run from any directory: bash scripts/fetch_phase1_raw.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RAW_ROOT="$ROOT/data/raw"
YEARS=(2022 2023 2024 2025 2026)

mkdir -p "$RAW_ROOT"

for year in "${YEARS[@]}"; do
  destination="$RAW_ROOT/$year"
  upstream="https://github.com/TracingInsights/$year.git"

  if [[ -d "$destination/.git" ]]; then
    echo "[$year] already present: fetching upstream changes"
    git -C "$destination" pull --ff-only origin main
  elif [[ -e "$destination" ]]; then
    echo "[$year] exists but is not a valid git checkout: $destination" >&2
    echo "Move it aside after checking it, then rerun this script." >&2
    exit 1
  else
    echo "[$year] cloning. This repository can be large and may take time."
    git clone --depth 1 "$upstream" "$destination"
  fi
done

echo
echo "Raw mirrors are ready. Run:"
echo "  python scripts/audit_raw_data.py --raw-root data/raw --output artifacts/schema_audit"
