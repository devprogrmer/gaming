<#
.SYNOPSIS
    gaming - in-place updater for Windows (PowerShell).

.DESCRIPTION
    Upgrades an existing installation to a new release without deleting the
    previous one first. It reuses the virtual environment created by
    install.ps1, optionally pulls the latest source with git, and reinstalls
    the package with `pip install --upgrade`. Your scan history, settings, and
    custom ranges live outside the install tree and are never touched.

    Prerequisite: the project was already installed with install.ps1 (a .venv
    exists in this directory).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\update.ps1
    powershell -ExecutionPolicy Bypass -File .\update.ps1 -NoPull
    powershell -ExecutionPolicy Bypass -File .\update.ps1 -Version v0.1.0
    powershell -ExecutionPolicy Bypass -File .\update.ps1 -List
#>

param(
    [switch]$NoPull,
    [string]$Version,
    [switch]$List
)

$ErrorActionPreference = "Stop"

$RepoDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$VenvDir = Join-Path $RepoDir ".venv"

# ---- locate the existing virtual environment -----------------------------
$VenvPy = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
    $VenvPy = Join-Path $VenvDir "bin/python"
}
if (-not (Test-Path $VenvPy)) {
    Write-Error "No virtual environment found in $VenvDir. Run install.ps1 first to create the initial installation."
    exit 1
}

Write-Host "Using existing installation: $VenvPy"

# ---- update in place via the package's own updater -----------------------
$updateArgs = @("-m", "gaming", "update", "--source", $RepoDir)
if ($NoPull) { $updateArgs += "--no-pull" }
if ($List) { $updateArgs += "--list" }
if ($Version) { $updateArgs += @("--version", $Version) }

& $VenvPy @updateArgs
exit $LASTEXITCODE
