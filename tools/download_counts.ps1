<#
.SYNOPSIS
    Vypise pocty stiahnuti suborov z GitHub Releases pre repozitar Level_Sensor.

.DESCRIPTION
    Pocty stiahnuti sa v GitHub web UI nezobrazuju - tento skript ich nacita
    cez verejne GitHub REST API. Na verejny repozitar NETREBA token ani gh CLI.
    (Limit verejneho API je 60 dotazov za hodinu na IP - viac nez dost.)

    Spustenie:
        powershell -ExecutionPolicy Bypass -File tools\download_counts.ps1
    alebo v otvorenom PowerShell:
        .\tools\download_counts.ps1
#>

[CmdletBinding()]
param(
    # Repozitar v tvare "vlastnik/nazov" (pozor: nazov je malymi pismenami).
    [string]$Repo = "Orimslav/level_sensor"
)

$ErrorActionPreference = "Stop"

$headers = @{
    "User-Agent" = "level-sensor-counter"
    "Accept"     = "application/vnd.github+json"
}
$uri = "https://api.github.com/repos/$Repo/releases"

try {
    $releases = Invoke-RestMethod -Uri $uri -Headers $headers
}
catch {
    Write-Host "Chyba volania GitHub API: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

if (-not $releases -or $releases.Count -eq 0) {
    Write-Host "Repozitar '$Repo' zatial nema ziadne vydania (releases)." -ForegroundColor Yellow
    exit 0
}

Write-Host ""
Write-Host "=== Pocty stiahnuti: $Repo ===" -ForegroundColor Cyan

foreach ($r in $releases) {
    Write-Host ""
    Write-Host "[$($r.tag_name)]  ($($r.published_at))" -ForegroundColor Green
    if (-not $r.assets -or $r.assets.Count -eq 0) {
        Write-Host "  (bez prilozenych suborov)"
        continue
    }
    foreach ($a in $r.assets) {
        Write-Host ("  {0,-42} {1,6}x" -f $a.name, $a.download_count)
    }
}

$total = ($releases.assets | Measure-Object -Property download_count -Sum).Sum
if (-not $total) { $total = 0 }

Write-Host ""
Write-Host "=== SPOLU vsetky subory/verzie: $total stiahnuti ===" -ForegroundColor Cyan
Write-Host ""
