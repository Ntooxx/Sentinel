param(
    [string]$Target = ".",
    [switch]$Help
)

if ($Help) {
    @"
Sentinel 2.0 Installer for Windows

Usage:
  powershell -ExecutionPolicy Bypass -File install.ps1
  powershell -ExecutionPolicy Bypass -File install.ps1 -Target "C:\path\to\project"

What it does:
  1. Installs Sentinel in editable mode
  2. Scans the target directory (default: current dir)
  3. Writes a self-contained HTML report to .sentinel/quality.html

Requirements: Python 3.10+
"@
    exit
}

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "=== Installing Sentinel ===" -ForegroundColor Cyan
python -m pip install -e "$Root"
if ($LASTEXITCODE -ne 0) {
    Write-Host "pip install failed. Make sure Python 3.10+ and pip are installed." -ForegroundColor Red
    exit 1
}

$ReportPath = Join-Path $Target ".sentinel\quality.html"
Write-Host ""
Write-Host "=== Scanning: $Target ===" -ForegroundColor Cyan
python "$Root/sentinel.py" scan "$Target" --format html --out "$ReportPath" --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "Scan failed." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Done. Open $ReportPath in your browser to view the report." -ForegroundColor Green
Write-Host "Run 'project-sentinel --help' to see the three supported commands." -ForegroundColor Green
