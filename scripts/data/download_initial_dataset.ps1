<#
.SYNOPSIS
Download the portable TrackShift initial telemetry scope from TracingInsights.

.DESCRIPTION
PowerShell-native equivalent of download_initial_dataset.sh. Creates or extends a
sparse, read-only raw mirror. The default scope follows TrackShift AGENTS.md:

  2022-2025  Qualifying, Sprint Qualifying/Shootout, Sprint, Race
  2026       Practice 1 plus the sessions above

Raw files are immutable. This script only adds sparse-checkout paths and never
deletes or reorganises existing mirrors. It is resumable and additive.

.EXAMPLE
.\scripts\data\download_initial_dataset.ps1 -DryRun

.EXAMPLE
.\scripts\data\download_initial_dataset.ps1 -Years 2024

.EXAMPLE
.\scripts\data\download_initial_dataset.ps1 -Years 2024 -Events "British Grand Prix" -Sessions "Race"
#>
[CmdletBinding()]
param(
    [string]$Years = "2022,2023,2024,2025,2026",
    [string]$Events = "",
    [string]$Sessions = "",
    [string]$RawRoot,
    [string]$ManifestRoot,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $RawRoot)      { $RawRoot      = Join-Path $Root "data\raw\tracinginsights" }
if (-not $ManifestRoot) { $ManifestRoot = Join-Path $Root "artifacts\download_manifests" }

function Split-Csv {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return @() }
    $parts = $Value -split ',' | ForEach-Object { $_.Trim() }
    foreach ($p in $parts) {
        if ([string]::IsNullOrEmpty($p)) { throw "empty CSV value in: $Value" }
    }
    return @($parts)
}

function Invoke-Git {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GitArgs)
    & git @GitArgs
    if ($LASTEXITCODE -ne 0) { throw "git $($GitArgs -join ' ') failed with exit code $LASTEXITCODE" }
}

function Get-SessionNames {
    param([string]$Year, [string[]]$Override)
    if ($Override.Count -gt 0) { return $Override }
    $names = @("Qualifying", "Sprint Qualifying", "Sprint Shootout", "Sprint", "Race")
    if ($Year -eq "2026") { $names = @("Practice 1") + $names }
    return $names
}

function Get-SparsePatterns {
    param([string]$Year, [string[]]$EventList, [string[]]$Override)
    $sessions = Get-SessionNames -Year $Year -Override $Override
    $patterns = @("/README.md", "/data_dictionary.json")
    if ($EventList.Count -eq 0) {
        foreach ($s in $sessions) { $patterns += "/*/$s/" }
    } else {
        foreach ($e in $EventList) {
            foreach ($s in $sessions) { $patterns += "/$e/$s/" }
        }
    }
    return $patterns
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "git is required" }

$YearList     = Split-Csv $Years
$EventList    = Split-Csv $Events
$SessionOverride = Split-Csv $Sessions

foreach ($y in $YearList) {
    if ($y -notmatch '^202[2-6]$') { throw "unsupported year: $y" }
}

$MetadataNames = @('laptimes.json','weather.json','rcm.json','drivers.json','corners.json','session_laptimes.json')

foreach ($year in $YearList) {
    $destination = Join-Path $RawRoot $year
    $upstream    = "https://github.com/TracingInsights/$year.git"
    $patterns    = Get-SparsePatterns -Year $year -EventList $EventList -Override $SessionOverride

    Write-Host "[$year] source: $upstream"
    Write-Host "[$year] sessions: $($patterns -join ' ')"
    if ($DryRun) { continue }

    if (-not (Test-Path $RawRoot)) { New-Item -ItemType Directory -Force -Path $RawRoot | Out-Null }

    if (Test-Path (Join-Path $destination ".git")) {
        $actualUpstream = (& git -C $destination remote get-url origin).Trim()
        if ($actualUpstream -ne $upstream) { throw "[$year] origin differs from expected source: $actualUpstream" }
        $dirty = & git -C $destination status --porcelain
        if ($dirty) { throw "[$year] checkout has local changes: $destination" }
        Write-Host "[$year] refreshing existing sparse checkout"
        Invoke-Git -C $destination pull --ff-only origin main
        Invoke-Git -C $destination sparse-checkout add @patterns
    }
    elseif (Test-Path $destination) {
        throw "[$year] destination exists but is not a Git checkout: $destination"
    }
    else {
        Write-Host "[$year] creating sparse checkout"
        Invoke-Git clone --depth 1 --filter=blob:none --no-checkout $upstream $destination
        Invoke-Git -C $destination sparse-checkout init --no-cone
        Invoke-Git -C $destination sparse-checkout set --no-cone @patterns
        Invoke-Git -C $destination checkout main
    }

    $allFiles       = @(Get-ChildItem -Path $destination -Recurse -File -Force -ErrorAction SilentlyContinue |
                        Where-Object { $_.FullName.Split([char]92) -notcontains '.git' })
    $telemetryCount = @($allFiles | Where-Object { $_.Name -like '*_tel.json' }).Count
    $metadataCount  = @($allFiles | Where-Object { $MetadataNames -contains $_.Name }).Count
    $sourceCommit   = (& git -C $destination rev-parse HEAD).Trim()

    if (-not (Test-Path $ManifestRoot)) { New-Item -ItemType Directory -Force -Path $ManifestRoot | Out-Null }
    $eventsField   = if ($Events)   { $Events }   else { "all" }
    $sessionsField = if ($Sessions) { $Sessions } else { $null }
    $payload = [ordered]@{
        dataset         = "trackshift_initial_tracinginsights_scope"
        source          = $upstream
        source_commit   = $sourceCommit
        year            = $year
        destination     = $destination
        requested_scope = [ordered]@{
            years             = $Years
            events            = $eventsField
            sessions_override = $sessionsField
        }
        sparse_patterns = $patterns
        available_files = [ordered]@{
            telemetry                 = $telemetryCount
            session_or_driver_metadata = $metadataCount
        }
        raw_data_policy = "immutable source mirror; sparse paths only added"
        created_utc     = (Get-Date).ToUniversalTime().ToString("o")
    }
    $manifestPath = Join-Path $ManifestRoot "initial_dataset_$year.json"
    # Out-File -Encoding utf8 writes a BOM on Windows PowerShell 5.1, which breaks
    # strict JSON parsers such as Python's json.load. Write UTF-8 without BOM, LF endings.
    $json = ($payload | ConvertTo-Json -Depth 6).Replace("`r`n", "`n") + "`n"
    [System.IO.File]::WriteAllText($manifestPath, $json, (New-Object System.Text.UTF8Encoding $false))

    Write-Host "[$year] available telemetry files: $telemetryCount; metadata files: $metadataCount"
}

if ($DryRun) {
    Write-Host "Dry run only. No files were created or changed."
} else {
    Write-Host "Initial raw dataset is ready under: $RawRoot"
    Write-Host "Manifest files: $ManifestRoot"
    Write-Host "Next: python scripts/audit_raw_data.py --raw-root `"$RawRoot`" --output artifacts/schema_audit"
}
