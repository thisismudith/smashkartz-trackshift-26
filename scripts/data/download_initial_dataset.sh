#!/usr/bin/env bash
# Download the portable TrackShift initial telemetry scope from TracingInsights.
#
# The default scope follows TrackShift AGENTS.md:
#   2022-2025: Qualifying, Sprint Qualifying/Shootout, Sprint, Race
#   2026:      Practice 1 plus the sessions above
#
# Raw files are immutable. This script only adds sparse-checkout paths and never
# touches the older data/raw/<year> mirrors used by earlier phases.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RAW_ROOT="$ROOT/data/raw/tracinginsights"
MANIFEST_ROOT="$ROOT/artifacts/download_manifests"
YEARS_CSV="2022,2023,2024,2025,2026"
EVENTS_CSV=""
SESSIONS_CSV=""
DRY_RUN=0

usage() {
  cat <<'EOF'
Usage: bash scripts/data/download_initial_dataset.sh [options]

Create or extend a sparse, read-only raw mirror from TracingInsights. By default
the script downloads the project core telemetry scope for every available event:

  2022-2025  Qualifying, Sprint Qualifying/Shootout, Sprint, Race
  2026       Practice 1, Qualifying, Sprint Qualifying/Shootout, Sprint, Race

Options:
  --years CSV        Years to download (default: 2022,2023,2024,2025,2026)
  --events CSV       Exact event directory names, for example
                       "British Grand Prix,Italian Grand Prix"
  --sessions CSV     Exact session directory names. Overrides the default
                       session policy for every selected year.
  --raw-root PATH    Destination root (default: data/raw/tracinginsights)
  --manifest-root P  Download manifest directory (default: artifacts/download_manifests)
  --dry-run          Print the selected scope without cloning or updating files
  -h, --help         Show this help message

The script is resumable and additive. It will not delete or reorganize existing
raw mirrors. Session directories that do not exist upstream are simply absent.
EOF
}

die() { echo "error: $*" >&2; exit 2; }

require_value() {
  [[ $# -eq 2 && -n "$2" ]] || die "$1 requires a value"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --years) require_value "$1" "${2:-}"; YEARS_CSV="$2"; shift 2 ;;
    --events) require_value "$1" "${2:-}"; EVENTS_CSV="$2"; shift 2 ;;
    --sessions) require_value "$1" "${2:-}"; SESSIONS_CSV="$2"; shift 2 ;;
    --raw-root) require_value "$1" "${2:-}"; RAW_ROOT="$2"; shift 2 ;;
    --manifest-root) require_value "$1" "${2:-}"; MANIFEST_ROOT="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

command -v git >/dev/null 2>&1 || die "git is required"

split_csv() {
  local value="$1"
  local -n result="$2"
  result=()
  IFS=',' read -r -a result <<< "$value"
  for i in "${!result[@]}"; do
    result[$i]="${result[$i]#"${result[$i]%%[![:space:]]*}"}"
    result[$i]="${result[$i]%"${result[$i]##*[![:space:]]}"}"
    [[ -n "${result[$i]}" ]] || die "empty CSV value in: $value"
  done
}

declare -a YEARS=() EVENTS=() OVERRIDE_SESSIONS=()
split_csv "$YEARS_CSV" YEARS
[[ -z "$EVENTS_CSV" ]] || split_csv "$EVENTS_CSV" EVENTS
[[ -z "$SESSIONS_CSV" ]] || split_csv "$SESSIONS_CSV" OVERRIDE_SESSIONS

for year in "${YEARS[@]}"; do
  [[ "$year" =~ ^202[2-6]$ ]] || die "unsupported year: $year"
done

session_names_for_year() {
  local year="$1"
  local -n result="$2"
  if [[ ${#OVERRIDE_SESSIONS[@]} -gt 0 ]]; then
    result=("${OVERRIDE_SESSIONS[@]}")
  else
    result=("Qualifying" "Sprint Qualifying" "Sprint Shootout" "Sprint" "Race")
    [[ "$year" == "2026" ]] && result=("Practice 1" "${result[@]}")
  fi
}

sparse_patterns_for_year() {
  local year="$1"
  local -n result="$2"
  local -a sessions
  session_names_for_year "$year" sessions
  result=("/README.md" "/data_dictionary.json")

  local session event
  if [[ ${#EVENTS[@]} -eq 0 ]]; then
    for session in "${sessions[@]}"; do
      result+=("/*/$session/")
    done
  else
    for event in "${EVENTS[@]}"; do
      for session in "${sessions[@]}"; do
        result+=("/$event/$session/")
      done
    done
  fi
}

write_manifest() {
  local year="$1" destination="$2" upstream="$3" source_commit="$4" telemetry_count="$5" metadata_count="$6"
  shift 6
  python3 - "$MANIFEST_ROOT" "$year" "$destination" "$upstream" "$source_commit" "$telemetry_count" "$metadata_count" "$YEARS_CSV" "$EVENTS_CSV" "$SESSIONS_CSV" "$@" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

(
    root,
    year,
    destination,
    upstream,
    source_commit,
    telemetry_count,
    metadata_count,
    years,
    events,
    sessions,
    *patterns,
) = sys.argv[1:]
output = Path(root) / f"initial_dataset_{year}.json"
output.parent.mkdir(parents=True, exist_ok=True)
payload = {
    "dataset": "trackshift_initial_tracinginsights_scope",
    "source": upstream,
    "source_commit": source_commit,
    "year": year,
    "destination": destination,
    "requested_scope": {"years": years, "events": events or "all", "sessions_override": sessions or None},
    "sparse_patterns": patterns,
    "available_files": {"telemetry": int(telemetry_count), "session_or_driver_metadata": int(metadata_count)},
    "raw_data_policy": "immutable source mirror; sparse paths only added",
    "created_utc": datetime.now(timezone.utc).isoformat(),
}
output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY
}

for year in "${YEARS[@]}"; do
  destination="$RAW_ROOT/$year"
  upstream="https://github.com/TracingInsights/$year.git"
  declare -a patterns
  sparse_patterns_for_year "$year" patterns

  echo "[$year] source: $upstream"
  echo "[$year] sessions: ${patterns[*]}"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    continue
  fi

  mkdir -p "$RAW_ROOT"
  if [[ -d "$destination/.git" ]]; then
    actual_upstream="$(git -C "$destination" remote get-url origin)"
    [[ "$actual_upstream" == "$upstream" ]] || die "[$year] origin differs from expected source: $actual_upstream"
    [[ -z "$(git -C "$destination" status --porcelain)" ]] || die "[$year] checkout has local changes: $destination"
    echo "[$year] refreshing existing sparse checkout"
    git -C "$destination" pull --ff-only origin main
    git -C "$destination" sparse-checkout add --no-cone "${patterns[@]}"
  elif [[ -e "$destination" ]]; then
    die "[$year] destination exists but is not a Git checkout: $destination"
  else
    echo "[$year] creating sparse checkout"
    git clone --depth 1 --filter=blob:none --no-checkout "$upstream" "$destination"
    git -C "$destination" sparse-checkout init --no-cone
    git -C "$destination" sparse-checkout set --no-cone "${patterns[@]}"
    git -C "$destination" checkout main
  fi

  telemetry_count="$(find "$destination" -type f -name '*_tel.json' | wc -l | tr -d ' ')"
  metadata_count="$(find "$destination" -type f \( -name 'laptimes.json' -o -name 'weather.json' -o -name 'rcm.json' -o -name 'drivers.json' -o -name 'corners.json' -o -name 'session_laptimes.json' \) | wc -l | tr -d ' ')"
  source_commit="$(git -C "$destination" rev-parse HEAD)"
  write_manifest "$year" "$destination" "$upstream" "$source_commit" "$telemetry_count" "$metadata_count" "${patterns[@]}"
  echo "[$year] available telemetry files: $telemetry_count; metadata files: $metadata_count"
done

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "Dry run only. No files were created or changed."
else
  echo "Initial raw dataset is ready under: $RAW_ROOT"
  echo "Manifest files: $MANIFEST_ROOT"
  echo "Next: python3 scripts/audit_raw_data.py --raw-root \"$RAW_ROOT\" --output artifacts/schema_audit"
fi
