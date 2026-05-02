param(
    [string]$Target = ".",
    [switch]$Help
)

if ($Help) {
    @"
Sentinel Installer for Windows

Usage:
  powershell -ExecutionPolicy Bypass -File install.ps1
  powershell -ExecutionPolicy Bypass -File install.ps1 -Target "C:\path\to\project"

What it does:
  1. Installs Sentinel via pip in editable mode
  2. Scans the target directory (default: current dir)
  3. Generates an HTML report

Requirements: Python 3.8+
"@
    exit
}

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "=== Installing Sentinel ===" -ForegroundColor Cyan
pip install -e "$Root" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "pip install failed. Make sure Python 3.8+ and pip are installed." -ForegroundColor Red
    exit 1
}

Write-Host "✓ Sentinel installed" -ForegroundColor Green
Write-Host ""
Write-Host "=== Scanning: $Target ===" -ForegroundColor Cyan
python "$Root/sentinel.py" scan "$Target" --fast

Write-Host ""
Write-Host "=== Generating HTML Report ===" -ForegroundColor Cyan
python "$Root/sentinel.py" report "$Target" --format html

Write-Host ""
Write-Host "Done! Open SENTINEL_REPORT.html in your browser to view the report." -ForegroundColor Green
Write-Host "Run 'project-sentinel --help' to see all commands." -ForegroundColor Green
