# Capture a real screenshot of Sentinel's own HTML quality report.
#
# Produces, in docs/demo/:
#   sentinel-report.html   the self-contained report artifact (inline CSS, no server)
#   sentinel-report.png    a rendered image for the README
#
# Uses the local Chrome or Edge in headless mode. No HTTP server, no dashboard,
# no whole-screen capture: the image is deterministic for a given commit.
#
# Usage:
#   pwsh tools/capture_screenshots.ps1
#   pwsh tools/capture_screenshots.ps1 -Path . -Width 1240 -Height 1150 -Scale 1.5

param(
    [string]$Path = ".",
    [int]$Width = 1240,
    [int]$Height = 1520,
    [double]$Scale = 1.4
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$OutDir = Join-Path $Root "docs/demo"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$HtmlPath = Join-Path $OutDir "sentinel-report.html"
$PngPath = Join-Path $OutDir "sentinel-report.png"

# 1. Render the report. Run from the repository root so import resolution and
#    project-relative paths behave exactly as they do for a user.
Push-Location $Root
try {
    $env:PYTHONPATH = "src"
    python -m sentinel2 scan $Path --format html --out $HtmlPath --quiet
    if ($LASTEXITCODE -ne 0) { throw "sentinel2 scan failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}

# 2. Find a Chromium-based browser.
$browser = @(
    (Join-Path $env:ProgramFiles "Google/Chrome/Application/chrome.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Google/Chrome/Application/chrome.exe"),
    (Join-Path $env:ProgramFiles "Microsoft/Edge/Application/msedge.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Microsoft/Edge/Application/msedge.exe")
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

if (-not $browser) {
    throw "No Chrome or Edge found. The HTML report is at $HtmlPath; open it and screenshot it manually."
}

# 3. Screenshot the file directly. A file:// URL avoids standing up a server.
$url = "file:///" + ($HtmlPath -replace '\\', '/')
if (Test-Path $PngPath) { Remove-Item $PngPath -Force }

# Chrome writes progress ("N bytes written") to stderr. Under
# $ErrorActionPreference = 'Stop', PowerShell 5.1 turns that into a terminating
# NativeCommandError, so relax it for the call and restore it afterwards.
$chromeArgs = @(
    "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-sandbox",
    "--force-device-scale-factor=$Scale",
    "--window-size=$Width,$Height",
    "--screenshot=$PngPath",
    $url
)
$ErrorActionPreference = "Continue"
& $browser @chromeArgs 2>$null | Out-Null
$ErrorActionPreference = "Stop"

# Chrome can return before the file is flushed; wait for it rather than racing it.
$deadline = (Get-Date).AddSeconds(30)
while (-not (Test-Path $PngPath) -and (Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 250
}

if (-not (Test-Path $PngPath)) {
    throw "Screenshot failed. The HTML report is at $HtmlPath; open it and screenshot it manually."
}

Write-Host "Wrote:"
Write-Host "  $HtmlPath"
Write-Host "  $PngPath"
