# Builds the Windows installer: frozen engine + bundled ffmpeg, then the Electron app.
# Usage (from anywhere): powershell -ExecutionPolicy Bypass -File scripts\build_all.ps1
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$appDir = Join-Path $repoRoot "app"

function Invoke-Checked {
    param([string]$File, [string[]]$Arguments, [string]$WorkingDirectory)
    Push-Location $WorkingDirectory
    try {
        & $File @Arguments
        if ($LASTEXITCODE -ne 0) { throw "Command failed (exit $LASTEXITCODE): $File $($Arguments -join ' ')" }
    }
    finally {
        Pop-Location
    }
}

# Prefer the project venv (has PyInstaller and the engine deps); fall back to python on PATH.
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

Write-Host "==> Building engine (PyInstaller + ffmpeg)"
Invoke-Checked -File $python -Arguments @("scripts/build_engine.py") -WorkingDirectory $repoRoot

Write-Host "==> Installing app dependencies"
if (Test-Path (Join-Path $appDir "package-lock.json")) {
    Invoke-Checked -File "npm" -Arguments @("ci") -WorkingDirectory $appDir
} else {
    Invoke-Checked -File "npm" -Arguments @("install") -WorkingDirectory $appDir
}

Write-Host "==> Packaging installer (npm run dist)"
Invoke-Checked -File "npm" -Arguments @("run", "dist") -WorkingDirectory $appDir

Write-Host "==> Done. Installer(s):"
Get-ChildItem (Join-Path $appDir "release") -Filter "*.exe" | ForEach-Object { Write-Host "  $($_.FullName)" }
