<#
.SYNOPSIS
    CP-04 -- Build the 20 m telemetry lake, stages 4a to 4c.

.DESCRIPTION
    Runs the three lake stages in order with weighted progress, verifying after
    each one, and reports a completion percentage at the end.

    Stage 4d (2022-2025, 130,718 laps, about 3.8 hours at -Jobs 8) is
    deliberately NOT included. It exists only to give the pass model DRS-era
    priors, which CP-14 needs and nothing before it does. It is 74% of the total
    build cost, so it waits.

    Measured throughput on this machine: 1.59 laps/sec single-threaded.

      stage  scope                              laps    -Jobs 8
      4a     BGP Race, HAM + ANT                 104     ~1 min
      4b     BGP 2026, all sessions            2,644     ~5 min
      4c     2026 remaining events            31,692    ~55 min

    4c skips the British Grand Prix because 4b has already built it. The lake is
    identical either way; this just avoids rebuilding 2,644 laps. Use
    -RebuildAll to force every event in 4c.

.PARAMETER Jobs
    Sessions built in parallel. Default 8. This machine has 12 cores; above
    about 10 the build is disk-bound rather than CPU-bound and extra processes
    only queue. Parallelism never crosses into a single session, because
    MODELS.md section 6.3 requires deterministic output.

.PARAMETER Stages
    Which stages to run. Accepts 4b,4c or "4b,4c" or a single 4c.
    Default is all three. Use this to resume after an interruption instead of
    starting over.

.PARAMETER RebuildAll
    Include the British Grand Prix in 4c even though 4b built it.

.PARAMETER VerifyOnly
    Skip building and just re-run the gates against the existing lake.

.EXAMPLE
    .\runbooks\CP-04.ps1
    All three stages at -Jobs 8. Roughly one hour.

.EXAMPLE
    .\runbooks\CP-04.ps1 -Stages 4a
    Smoke test only, about a minute. Do this first.

.EXAMPLE
    .\runbooks\CP-04.ps1 -Stages 4c -Jobs 10
    Resume at 4c after 4a and 4b have completed.

.EXAMPLE
    .\runbooks\CP-04.ps1 -VerifyOnly
    Re-check the gates without rebuilding anything.
#>
[CmdletBinding()]
param(
    [int]$Jobs = 8,
    [string[]]$Stages = @('4a','4b','4c'),
    [switch]$RebuildAll,
    [switch]$VerifyOnly
)

$ErrorActionPreference = 'Stop'

$Root   = Split-Path -Parent $PSScriptRoot
$Raw    = Join-Path $Root 'data\raw\tracinginsights'
$Lake   = Join-Path $Root 'data\processed\telemetry_20m'
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$Builder = Join-Path $Root 'scripts\data\build_lake.py'
$Verifier = Join-Path $Root 'scripts\data\verify_lake.py'

if (-not (Test-Path $Python))  { throw "No venv at $Python. CP-00 must be complete first." }
if (-not (Test-Path $Raw))     { throw "No raw mirror at $Raw." }
if (-not (Test-Path $Builder)) { throw "Missing $Builder." }

Set-Location $Root
# Accept every natural spelling: -Stages 4b,4c (PowerShell makes that an
# array), -Stages "4b,4c" (one comma-joined string), and -Stages 4b.
$wanted = @($Stages | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim().ToLower() } | Where-Object { $_ })
$bad = $wanted | Where-Object { $_ -notin @('4a','4b','4c') }
if ($bad) { throw "Unknown stage(s): $($bad -join ', '). Valid: 4a, 4b, 4c." }

# Progress is weighted by measured lap count, not by stage count: 4c is 92% of
# the work, so three equal thirds would sit at 33% for most of the hour.
$stageLaps = @{ '4a' = 104; '4b' = 2644; '4c' = 31692 }
$plannedLaps = ($wanted | ForEach-Object { $stageLaps[$_] } | Measure-Object -Sum).Sum
if ($plannedLaps -le 0) { $plannedLaps = 1 }
$lapsDone = 0
$results = [ordered]@{}
$stageTimes = [ordered]@{}

function Pct { param([int]$done) [math]::Round(100.0 * $done / $script:plannedLaps) }

function Banner {
    param([string]$stage, [string]$what, [string]$eta)
    $p = Pct $script:lapsDone
    Write-Host ""
    Write-Host ("=== [{0,3}%] stage {1} -- {2} ===" -f $p, $stage, $what) -ForegroundColor Cyan
    Write-Host ("    {0} laps, about {1} at -Jobs {2}. Live bar with ETA follows." -f $stageLaps[$stage], $eta, $Jobs) -ForegroundColor DarkGray
    Write-Progress -Activity 'CP-04 Build the 20 m lake' -Status "stage $stage -- $what" -PercentComplete $p
}

function Gate {
    param([string]$name, [bool]$ok, [string]$detail)
    $script:results[$name] = @{ ok = $ok; detail = $detail }
    $tag = if ($ok) { 'PASS' } else { 'FAIL' }
    $col = if ($ok) { 'Green' } else { 'Red' }
    Write-Host ("  [{0}] {1}" -f $tag, $name) -ForegroundColor $col
    if ($detail) { Write-Host ("         {0}" -f $detail) -ForegroundColor DarkGray }
}

function Run-Stage {
    param([string]$stage, [string[]]$buildArgs, [string]$what, [string]$eta)
    Banner $stage $what $eta
    $sw = [Diagnostics.Stopwatch]::StartNew()
    & $Python $Builder --raw-root $Raw --output-root $Lake --jobs $Jobs @buildArgs
    $code = $LASTEXITCODE
    $sw.Stop()
    $script:stageTimes[$stage] = $sw.Elapsed
    $script:lapsDone += $stageLaps[$stage]
    Write-Host ("    stage {0} finished in {1:n1} min" -f $stage, $sw.Elapsed.TotalMinutes) -ForegroundColor DarkGray
    if ($code -ne 0) {
        Write-Host "    build_lake.py reported failed sessions; see run_manifest.json failures[]" -ForegroundColor Yellow
    }
    return $code
}

# ---------------------------------------------------------------- build
if (-not $VerifyOnly) {
    if ($wanted -contains '4a') {
        Run-Stage '4a' @('--years','2026','--events','British Grand Prix','--sessions','Race','--drivers','HAM','ANT') `
            'smoke test on the demo battle' '1 min' | Out-Null
    }
    if ($wanted -contains '4b') {
        Run-Stage '4b' @('--years','2026','--events','British Grand Prix') `
            'demo event, all sessions' '5 min' | Out-Null
    }
    if ($wanted -contains '4c') {
        $args4c = @('--years','2026')
        if (-not $RebuildAll -and ($wanted -contains '4b')) {
            # 4b already built this event; rebuilding it changes nothing.
            $args4c += @('--exclude-event','British Grand Prix')
        }
        Run-Stage '4c' $args4c '2026 domain for Chain E and Chain P' '55 min' | Out-Null
    }
} else {
    Write-Host "`n=== VerifyOnly: skipping the build ===" -ForegroundColor Yellow
    $lapsDone = $plannedLaps
}

# ---------------------------------------------------------------- verify
Write-Host ""
Write-Host ("=== [{0,3}%] verification ===" -f (Pct $lapsDone)) -ForegroundColor Cyan
Write-Progress -Activity 'CP-04 Build the 20 m lake' -Status 'verification' -PercentComplete (Pct $lapsDone)

& $Python $Verifier --lake $Lake
$verifyExit = $LASTEXITCODE

$reportPath = Join-Path $Lake 'verify_report.json'
if (-not (Test-Path $reportPath)) {
    Write-Host "`n  verify_lake.py produced no report. Nothing built yet?" -ForegroundColor Red
    Write-Progress -Activity 'CP-04 Build the 20 m lake' -Completed
    exit 2
}
$report = Get-Content $reportPath -Raw | ConvertFrom-Json
foreach ($g in $report.gates) { Gate $g.gate $g.ok $g.detail }

# ---------------------------------------------------------------- determinism
# MODELS.md section 6.3 has both owners building locally and comparing results,
# so a non-deterministic build would silently produce two different lakes.
if (-not $VerifyOnly -and ($wanted -contains '4a')) {
    Write-Host ""
    Write-Host "=== determinism check (rebuilds one small session) ===" -ForegroundColor Cyan
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("ts_determinism_" + [Guid]::NewGuid().ToString('N').Substring(0,8))
    & $Python $Builder --raw-root $Raw --output-root $tmp --years 2026 `
        --events 'British Grand Prix' --sessions Race --drivers HAM ANT --no-progress | Out-Null

    $cmp = Join-Path ([IO.Path]::GetTempPath()) 'ts_cmp.py'
    @'
import glob, hashlib, sys
import pandas as pd
def h(root):
    files = sorted(glob.glob(root + "/**/*.parquet", recursive=True))
    if not files:
        return None
    df = pd.read_parquet(files[0])
    return hashlib.sha256(pd.util.hash_pandas_object(df, index=False).values.tobytes()).hexdigest()
a, b = h(sys.argv[1]), h(sys.argv[2])
print("MATCH" if (a and a == b) else "DIFFER")
print(a)
print(b)
'@ | Set-Content -Path $cmp -Encoding utf8
    $out = & $Python $cmp $Lake $tmp
    Remove-Item $cmp -ErrorAction SilentlyContinue
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    $same = ($out[0] -eq 'MATCH')
    Gate 'two builds produce identical content hashes' $same $(
        if ($same) { "sha256 " + $out[1].Substring(0,32) } else { "A=$($out[1]) B=$($out[2])" })
}

# ---------------------------------------------------------------- summary
Write-Progress -Activity 'CP-04 Build the 20 m lake' -Completed
$failed = @($results.Values | Where-Object { -not $_.ok })
$passed = @($results.Values | Where-Object { $_.ok })
$completion = if ($results.Count) { [math]::Round(100.0 * $passed.Count / $results.Count) } else { 0 }

Write-Host ""
Write-Host "================ CP-04 ================" -ForegroundColor Cyan
Write-Host (" Completion: {0}%   ({1} of {2} gates passed)" -f $completion, $passed.Count, $results.Count) -ForegroundColor $(
    if ($completion -eq 100) { 'Green' } elseif ($completion -ge 70) { 'Yellow' } else { 'Red' })
Write-Host (" Lake: {0} rows across {1} Parquet file(s)" -f $report.rows, $report.parquet_files) -ForegroundColor DarkGray
Write-Host (" Laps: {0} accepted of {1} ({2}%)" -f $report.accepted_laps, $report.discovered_laps, $report.acceptance_rate_pct) -ForegroundColor DarkGray
if ($report.rejection_codes) {
    $codes = ($report.rejection_codes.PSObject.Properties | ForEach-Object { "$($_.Name)=$($_.Value)" }) -join ', '
    Write-Host (" Rejections: {0}" -f $codes) -ForegroundColor DarkGray
    Write-Host " (structural: grid-start lap 1 and pit laps. Judge the codes, not the rate.)" -ForegroundColor DarkGray
}
if ($stageTimes.Count) {
    Write-Host ""
    foreach ($k in $stageTimes.Keys) {
        Write-Host ("  stage {0}: {1:n1} min" -f $k, $stageTimes[$k].TotalMinutes) -ForegroundColor DarkGray
    }
}
Write-Host ""
foreach ($k in $results.Keys) {
    $mark = if ($results[$k].ok) { '[x]' } else { '[ ]' }
    $col  = if ($results[$k].ok) { 'Green' } else { 'Red' }
    Write-Host ("  {0} {1}" -f $mark, $k) -ForegroundColor $col
}
Write-Host ""
Write-Host " Stage 4d (2022-2025, ~3.8 h at -Jobs 8) is not part of this run." -ForegroundColor DarkGray
Write-Host " It supplies DRS-era priors for CP-14 only. Build it then, not now." -ForegroundColor DarkGray
Write-Host ""

$ranAll = -not $VerifyOnly -and (@('4a','4b','4c') | Where-Object { $wanted -notcontains $_ }).Count -eq 0
if ($failed.Count -eq 0 -and $ranAll) {
    Write-Host " ALL GATES PASS -- CP-04 complete for stages 4a to 4c" -ForegroundColor Green
    Write-Host "=======================================" -ForegroundColor Cyan
    exit 0
}
if ($failed.Count -eq 0) {
    $ran = if ($VerifyOnly) { 'none (verify only)' } else { ($wanted -join ', ') }
    $left = (@('4a','4b','4c') | Where-Object { $wanted -notcontains $_ }) -join ', '
    Write-Host (" All gates pass for the stage(s) run: {0}" -f $ran) -ForegroundColor Green
    if ($left -and -not $VerifyOnly) {
        Write-Host (" CP-04 is NOT complete: stage(s) {0} still to build." -f $left) -ForegroundColor Yellow
        Write-Host ("   runbooks/CP-04.ps1 -Stages {0} -Jobs {1}" -f $left, $Jobs) -ForegroundColor Yellow
    }
    Write-Host "=======================================" -ForegroundColor Cyan
    exit 0
}
Write-Host (" {0} GATE(S) FAILED -- see CHECKPOINTS_TANVEER.md CP-04 'If output is bad'" -f $failed.Count) -ForegroundColor Red
Write-Host "=======================================" -ForegroundColor Cyan
exit 1
