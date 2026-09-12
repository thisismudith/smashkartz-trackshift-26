<#
.SYNOPSIS
    CP-01 -- Local data audit. Run this, then report the PASS/FAIL summary.

.DESCRIPTION
    Everything CP-01 needs, in order, with its acceptance gates checked
    automatically. See CHECKPOINTS_TANVEER.md CP-01 for the reasoning.

    Two jobs run here, and they answer different questions:

      inventory.py        what sessions exist and how complete each one is.
                          Never opens *_tel.json, so it covers the whole
                          mirror in ~17 seconds.

      audit_raw_data.py   what the telemetry fields look like. Parses all
                          177,288 lap files single-threaded, so the full
                          run takes roughly 35-40 minutes.

    Because of that gap, the default here audits 2026 only (~8 min), which
    is enough to unblock CP-04. Use -FullAudit when you can leave it running.

.PARAMETER FullAudit
    Audit all five seasons instead of 2026 only. Expect 35-40 minutes.

.PARAMETER SkipAudit
    Run the inventory and the gates only. Useful for re-checking results
    without re-parsing 177k files.

.EXAMPLE
    .\runbooks\CP-01.ps1
    2026 only. Roughly 8 minutes.

.EXAMPLE
    .\runbooks\CP-01.ps1 -FullAudit
    All five seasons. Roughly 40 minutes. This is what CP-01 requires to pass.

.EXAMPLE
    .\runbooks\CP-01.ps1 -SkipAudit
    Re-check the gates against an audit you already ran.
#>
[CmdletBinding()]
param(
    [switch]$FullAudit,
    [switch]$SkipAudit
)

$ErrorActionPreference = 'Stop'

$Root   = Split-Path -Parent $PSScriptRoot
$Raw    = Join-Path $Root 'data\raw\tracinginsights'
$Out    = Join-Path $Root 'artifacts\schema_audit'
$Python = Join-Path $Root '.venv\Scripts\python.exe'

if (-not (Test-Path $Python)) {
    throw "No venv at $Python. CP-00 must be complete first: python -m venv .venv; pip install -r requirements.txt"
}
if (-not (Test-Path $Raw)) {
    throw "No raw mirror at $Raw. Expected the AGENTS.md section 7 layout."
}

Set-Location $Root
$results = [ordered]@{}

# CP-01 progress is weighted by wall-clock share, not by step count: the schema
# audit dominates, so four equal quarters would sit at 25% for most of the run
# and then jump. These weights reflect a -FullAudit run.
$phaseWeights = [ordered]@{
    'Session inventory' = 5
    'Schema audit'      = 80
    'Test suite'        = 5
    'Acceptance gates'  = 10
}
$phaseDone = 0
$totalWeight = ($phaseWeights.Values | Measure-Object -Sum).Sum

function Step([string]$name, [int]$index) {
    $pct = [math]::Round(100.0 * $script:phaseDone / $script:totalWeight)
    Write-Host ("`n=== [{0,3}%] {1}/4  {2} ===" -f $pct, $index, $name) -ForegroundColor Cyan
    Write-Progress -Activity 'CP-01 Local data audit' -Status $name -PercentComplete $pct
}
function EndStep([string]$name) {
    $script:phaseDone += $script:phaseWeights[$name]
}
function Gate([string]$name, [bool]$ok, [string]$detail) {
    $script:results[$name] = @{ ok = $ok; detail = $detail }
    $tag = if ($ok) { 'PASS' } else { 'FAIL' }
    $col = if ($ok) { 'Green' } else { 'Red' }
    Write-Host ("  [{0}] {1}" -f $tag, $name) -ForegroundColor $col
    if ($detail) { Write-Host ("         {0}" -f $detail) -ForegroundColor DarkGray }
}

# ---------------------------------------------------------------- 1. inventory
Step 'Session inventory' 1
Write-Host "    ~17s for all five seasons; stats files only, never parses telemetry." -ForegroundColor DarkGray
& $Python scripts\data\inventory.py --raw-root $Raw --output $Out
if ($LASTEXITCODE -ne 0) { throw "inventory.py failed with exit code $LASTEXITCODE" }
EndStep 'Session inventory'

# ---------------------------------------------------------------- 2. schema audit
if ($SkipAudit) {
    Step 'Schema audit' 2
    Write-Host "    SKIPPED (-SkipAudit)" -ForegroundColor Yellow
} else {
    $scope = if ($FullAudit) { 'all five seasons, ~35-40 min' } else { '2026 only, ~8 min' }
    Step 'Schema audit' 2
    Write-Host "    $scope. Started $(Get-Date -Format 'HH:mm:ss')." -ForegroundColor DarkGray
    Write-Host "    A live bar with percent, rate and ETA follows." -ForegroundColor DarkGray
    $sw = [Diagnostics.Stopwatch]::StartNew()
    if ($FullAudit) {
        & $Python scripts\audit_raw_data.py --raw-root $Raw --output $Out
    } else {
        & $Python scripts\audit_raw_data.py --raw-root $Raw --output $Out --years 2026
    }
    if ($LASTEXITCODE -ne 0) { throw "audit_raw_data.py failed with exit code $LASTEXITCODE" }
    $sw.Stop()
    Write-Host ("    Finished in {0:n1} min" -f $sw.Elapsed.TotalMinutes) -ForegroundColor DarkGray
}
EndStep 'Schema audit'

# ---------------------------------------------------------------- 3. tests
Step 'Test suite' 3
& $Python -m pytest -q
Gate 'test suite passes' ($LASTEXITCODE -eq 0) 'includes test_guards.py and test_progress.py'
EndStep 'Test suite'

# ---------------------------------------------------------------- 4. gates
Step 'Acceptance gates' 4

$inv = Get-Content (Join-Path $Out 'inventory.json') -Raw | ConvertFrom-Json

$repPath = Join-Path $Out 'repository_summary.json'
if (-not (Test-Path $repPath)) {
    Write-Host "`n  No schema audit found at $repPath." -ForegroundColor Yellow
    Write-Host "  -SkipAudit only re-checks an audit you already ran." -ForegroundColor Yellow
    Write-Host "  Run 'runbooks/CP-01.ps1' (2026 only) or add -FullAudit (all seasons) first.`n" -ForegroundColor Yellow
    exit 2
}
$rep = Get-Content $repPath -Raw | ConvertFrom-Json

# Every expected artifact exists. These are the real file names; an earlier
# draft of CP-01 listed four that the script never writes.
$expected = @(
    'inventory.csv', 'inventory.json',
    'field_availability.csv', 'events_sessions.csv', 'data_quality_summary.csv',
    'repository_summary.json', 'schema_differences.json', 'canonical_schema.json'
)
$missing = $expected | Where-Object { -not (Test-Path (Join-Path $Out $_)) }
Gate 'all audit artifacts present' ($missing.Count -eq 0) $(
    if ($missing.Count) { "missing: $($missing -join ', ')" } else { "$($expected.Count) files in artifacts\schema_audit" })

# The mirror is whole. 177,288 is also the download-manifest total, so this
# cross-checks the download against the inventory.
Gate 'mirror holds 177,288 lap files' ($inv.totals.lap_files -eq 177288) `
    "found $($inv.totals.lap_files) across $($inv.totals.sessions) sessions, $($inv.totals.total_gb) GB"

Gate 'all five seasons present' ($inv.years_missing_from_mirror.Count -eq 0) $(
    if ($inv.years_missing_from_mirror.Count) { "missing: $($inv.years_missing_from_mirror -join ', ')" } else { '2022-2026' })

# 13, not 14. The 17 directories are 13 complete GPs, 3 Pre-Season Testing
# dirs, and Spanish GP (Practice 1 only, no corners.json -- open item T3).
$gp2026 = $inv.by_year.'2026'.events_with_all_required_core_sessions
Gate '2026 has 13 complete Grands Prix' ($gp2026 -eq 13) `
    "$gp2026 complete of $($inv.by_year.'2026'.events) event directories"

Gate 'no malformed telemetry files' ($rep.malformed.Count -eq 0) $(
    if ($rep.malformed.Count) { "$($rep.malformed.Count) parse errors -- see repository_summary.json" } else { 'zero parse errors' })

# distance_monotonic, broken down by session type.
#
# The original >99% gate is not achievable and never was: non-monotonic distance
# is structural, not corruption. Grid starts, pit in/out laps and qualifying
# out/in laps all traverse a different path length, and the validator rejects
# them by design. Measured on the 2026 mirror: Race 94.96%, Sprint 93.95%,
# Sprint Qualifying 84.89%, Practice 1 83.51%, Qualifying 75.69%.
#
# So this gates on provisional per-type floors that catch real corruption while
# tolerating the expected losses. See scripts/data/quality_report.py.
& $Python scripts\data\quality_report.py --audit $Out
$qualityOk = ($LASTEXITCODE -eq 0)
$qr = Get-Content (Join-Path $Out 'quality_report.json') -Raw | ConvertFrom-Json
$flagCount = @($qr.flags).Count
Gate 'distance_monotonic within expected range per session type' ($qualityOk -and $flagCount -eq 0) `
    ("overall {0}% monotonic; {1} session type(s) below the provisional floor" -f $qr.overall.monotonic_pct, $flagCount)

if (-not $rep.partial_audit) {
    Gate 'audit covered all five seasons' ($true) "years_audited: $($rep.years_audited -join ', ')"
} else {
    Write-Host "  [NOTE] partial audit: $($rep.years_audited -join ', '). Re-run with -FullAudit for the CP-01 gate." -ForegroundColor Yellow
}

# ---------------------------------------------------------------- summary
EndStep 'Acceptance gates'
Write-Progress -Activity 'CP-01 Local data audit' -Completed

$failed = @($results.Values | Where-Object { -not $_.ok })
$passed = @($results.Values | Where-Object { $_.ok })

# A partial audit cannot satisfy CP-01 however many gates it clears, so it is
# scored as its own outstanding requirement rather than quietly ignored.
$requirements = $results.Count + $(if ($rep.partial_audit) { 1 } else { 0 })
$met = $passed.Count
$completion = [math]::Round(100.0 * $met / $requirements)

Write-Host "`n================ CP-01 ================" -ForegroundColor Cyan
Write-Host (" Completion: {0}%   ({1} of {2} requirements met)" -f $completion, $met, $requirements) -ForegroundColor $(
    if ($completion -eq 100) { 'Green' } elseif ($completion -ge 70) { 'Yellow' } else { 'Red' })
Write-Host (" Gates: {0} passed, {1} failed" -f $passed.Count, $failed.Count) -ForegroundColor DarkGray
if ($rep.partial_audit) {
    Write-Host (" Audit scope: {0} only -- CP-01 needs all five seasons" -f ($rep.years_audited -join ', ')) -ForegroundColor Yellow
}
Write-Host ""
foreach ($k in $results.Keys) {
    $mark = if ($results[$k].ok) { '[x]' } else { '[ ]' }
    $col  = if ($results[$k].ok) { 'Green' } else { 'Red' }
    Write-Host ("  {0} {1}" -f $mark, $k) -ForegroundColor $col
}
Write-Host ""

if ($failed.Count -eq 0 -and -not $rep.partial_audit) {
    Write-Host " ALL REQUIREMENTS MET -- CP-01 complete" -ForegroundColor Green
} elseif ($failed.Count -eq 0) {
    Write-Host " Gates pass, but the audit was partial." -ForegroundColor Yellow
    Write-Host " Re-run with -FullAudit before ticking CP-01." -ForegroundColor Yellow
} else {
    Write-Host " $($failed.Count) GATE(S) FAILED -- see CHECKPOINTS_TANVEER.md CP-01 'If output is bad'" -ForegroundColor Red
    exit 1
}
Write-Host "=======================================`n" -ForegroundColor Cyan
