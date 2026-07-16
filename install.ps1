<#
.SYNOPSIS
    gaming - installer / dependency bootstrap for Windows (PowerShell).

.DESCRIPTION
    Creates an isolated virtual environment, installs the package into it, and
    drops a gaming.cmd launcher so you can start the interactive tool by typing
    `.\gaming` (or `gaming` if you add the folder to your PATH).

    The tool has no third-party runtime dependencies (standard library only),
    so this bootstrap is fast and works offline once Python is present.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install.ps1
#>

$ErrorActionPreference = "Stop"

$RepoDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$VenvDir = Join-Path $RepoDir ".venv"

function Find-Python {
    foreach ($candidate in @("py -3", "python", "python3")) {
        $parts = $candidate.Split(" ")
        $exe = $parts[0]
        $exeArgs = $parts[1..($parts.Length - 1)]
        if (Get-Command $exe -ErrorAction SilentlyContinue) {
            try {
                $check = & $exe @exeArgs -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" 2>$null
                if ($LASTEXITCODE -eq 0) { return $candidate }
            } catch { }
        }
    }
    return $null
}

$Python = Find-Python
if (-not $Python) {
    Write-Error "Python 3.11+ is required but was not found. Install it from https://www.python.org/downloads/ and re-run."
    exit 1
}

$pyParts = $Python.Split(" ")
$pyExe = $pyParts[0]
$pyArgs = @()
if ($pyParts.Length -gt 1) { $pyArgs = $pyParts[1..($pyParts.Length - 1)] }

$verInfo = & $pyExe @pyArgs --version 2>&1
Write-Host "Using Python: $verInfo"

# ---- create the virtual environment --------------------------------------
if (-not (Test-Path $VenvDir)) {
    Write-Host "Creating virtual environment in $VenvDir ..."
    & $pyExe @pyArgs -m venv $VenvDir
}

$VenvPy = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
    Write-Error "Could not locate the virtual environment's Python at $VenvPy"
    exit 1
}

# ---- install the package --------------------------------------------------
Write-Host "Upgrading pip ..."
& $VenvPy -m pip install --quiet --upgrade pip

Write-Host "Installing gaming ..."
& $VenvPy -m pip install --quiet $RepoDir

# ---- create a convenient launcher ----------------------------------------
$Launcher = Join-Path $RepoDir "gaming.cmd"
$launcherBody = "@echo off`r`n""$VenvPy"" -m gaming %*`r`n"
Set-Content -Path $Launcher -Value $launcherBody -Encoding ASCII
Write-Host "Created launcher: $Launcher"

Write-Host ""
Write-Host "Installation complete."
Write-Host "Start the interactive tool with:"
Write-Host "    .\gaming.cmd"
Write-Host "(Add this folder to your PATH to launch it as 'gaming' from anywhere.)"
