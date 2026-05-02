# Capture real screenshots of Sentinel dashboard and report
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Logos = Join-Path $Root "logos"
$ReportPath = Join-Path $Root "SENTINEL_REPORT.html"

# Generate HTML report if missing
if (-not (Test-Path $ReportPath)) {
    Push-Location $Root
    python sentinel.py report . --format html
    Pop-Location
}

# Start HTTP server in background for report
$ServerJob = Start-Job -ScriptBlock {
    param($Dir)
    Push-Location $Dir
    python -m http.server 8899
    Pop-Location
} -ArgumentList $Root

Start-Sleep -Seconds 2

# Open report in browser
Start-Process "http://localhost:8899/SENTINEL_REPORT.html"
Start-Sleep -Seconds 4

# Take screenshot
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
$graphics = [System.Drawing.Graphics]::FromImage($bmp)
$graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
$bmp.Save("$Logos/report-screenshot.png", [System.Drawing.Imaging.ImageFormat]::Png)
$graphics.Dispose()
$bmp.Dispose()

# Now open dashboard
Push-Location $Root
$DashJob = Start-Job -ScriptBlock {
    param($Dir)
    Push-Location $Dir
    python sentinel.py dashboard . --fast
    Pop-Location
} -ArgumentList $Root

Start-Sleep -Seconds 5

# Open dashboard
Start-Process "http://127.0.0.1:8765"
Start-Sleep -Seconds 5

# Take dashboard screenshot
$bmp2 = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
$graphics2 = [System.Drawing.Graphics]::FromImage($bmp2)
$graphics2.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
$bmp2.Save("$Logos/dashboard-screenshot.png", [System.Drawing.Imaging.ImageFormat]::Png)
$graphics2.Dispose()
$bmp2.Dispose()

# Cleanup
Stop-Job $DashJob
Remove-Job $DashJob

Pop-Location
Stop-Job $ServerJob
Remove-Job $ServerJob

Write-Host "Screenshots saved to logos/report-screenshot.png and logos/dashboard-screenshot.png"
