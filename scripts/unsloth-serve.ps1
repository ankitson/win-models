# Launch the Unsloth Studio web server.
#
# Bind 127.0.0.1 by default. Use -Lan to bind 0.0.0.0 for other machines, but
# note: browser mic / secure-context features only work when the page is opened
# via localhost/127.0.0.1 (or HTTPS), never via a plain-http LAN IP.

param(
  [string] $StudioHome = "E:\root\projects\unsloth",
  [string] $BindHost   = "127.0.0.1",
  [int]    $Port       = 8888,
  [string] $LogDir     = (Join-Path (Split-Path $PSScriptRoot -Parent) "logs"),
  [switch] $Lan,       # bind 0.0.0.0 instead of 127.0.0.1
  [switch] $Open       # open the browser at http://localhost:<port>
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\unsloth-common.ps1"

$unsloth = Resolve-UnslothExe $StudioHome
if ($Lan) { $BindHost = "0.0.0.0" }
$env:UNSLOTH_STUDIO_HOME = $StudioHome

if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
  Write-Host "A server is already listening on port $Port." -ForegroundColor Yellow
  if ($Open) { Start-Process "http://localhost:$Port" }
  return
}

Ensure-Directory $LogDir
$outLog = Join-Path $LogDir "studio-$Port.out.log"
$errLog = Join-Path $LogDir "studio-$Port.err.log"

Write-Host "Starting Unsloth Studio on http://$BindHost`:$Port ..."
Write-Host "Logs: $outLog"
# Detached background process; stdout/stderr captured to the log files.
$proc = Start-Process -FilePath $unsloth `
  -ArgumentList @("studio", "-H", $BindHost, "-p", "$Port") `
  -WindowStyle Hidden -PassThru `
  -RedirectStandardOutput $outLog -RedirectStandardError $errLog

# Wait for /api/health.
$base = "http://127.0.0.1:$Port"
$deadline = (Get-Date).AddSeconds(180)
$ready = $false
while ((Get-Date) -lt $deadline) {
  try {
    $h = Invoke-RestMethod -Uri "$base/api/health" -TimeoutSec 5
    if ($h.status -eq "healthy") { $ready = $true; break }
  } catch { Start-Sleep -Seconds 3 }
}
if (-not $ready) {
  throw "Studio did not become healthy on $base within the timeout (PID $($proc.Id))."
}

Write-Host "Ready: $base  (open http://localhost:$Port in Chrome/Edge)" -ForegroundColor Green
Write-Host "Running detached (PID $($proc.Id)). Stop it with:  just studio-stop"
if ($Lan) {
  Write-Host "Bound to 0.0.0.0 -- reachable on the LAN, but open it via localhost on THIS machine" -ForegroundColor Yellow
  Write-Host "for mic/secure-context features; LAN clients over plain http cannot use the mic."
}
if ($Open) { Start-Process "http://localhost:$Port" }
