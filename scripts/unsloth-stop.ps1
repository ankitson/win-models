# Stop the background Unsloth Studio server cleanly.
#
# Why this exists: the server is launched detached, so Ctrl+C on `just studio-serve`
# can't reach it. Studio's built-in `unsloth studio stop` is also broken on Windows
# (it crashes in os.kill(pid, 0) with WinError 87 before terminating anything). So
# we stop it ourselves: identify the server by listening port + studio.pid, kill the
# whole process tree (so the spawned child llama-server doesn't orphan), preferring a
# graceful request and only forcing if it won't exit, then clear the stale pid file.

param(
  [string] $StudioHome = "E:\root\projects\unsloth",
  [int]    $Port       = 8888
)

$ErrorActionPreference = "Stop"

function Test-PortListening { param([int] $P)
  [bool](Get-NetTCPConnection -LocalPort $P -State Listen -ErrorAction SilentlyContinue)
}

# Collect candidate roots: whatever owns the port, plus the recorded pid.
$pids = @()
Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
  ForEach-Object { $pids += [int]$_.OwningProcess }
$pidFile = Join-Path $StudioHome "studio.pid"
if (Test-Path $pidFile) {
  $fp = (Get-Content $pidFile -Raw).Trim()
  if ($fp -match '^\d+$') { $pids += [int]$fp }
}
$pids = $pids | Where-Object { $_ -gt 0 } | Sort-Object -Unique

if (-not $pids) {
  Write-Host "No Unsloth Studio server found on port $Port."
  if (Test-Path $pidFile) { Remove-Item $pidFile -Force -ErrorAction SilentlyContinue; Write-Host "Removed stale studio.pid." }
  return
}

# Run taskkill via cmd so its stderr/exit code can't trip PowerShell 5.1's
# native-command error wrapping (which would abort the script).
function Invoke-TaskKill { param([int] $P, [switch] $Force)
  $f = if ($Force) { "/F " } else { "" }
  & cmd.exe /c "taskkill $f/PID $P /T >nul 2>&1"
}

# 1. Graceful tree termination (no /F): asks the tree to exit. A hidden-console
# server often can't be closed this way, so it's just a best-effort first try.
foreach ($p in $pids) { Invoke-TaskKill -P $p }

# Wait up to ~6s for the port to free.
$deadline = (Get-Date).AddSeconds(6)
while ((Get-Date) -lt $deadline -and (Test-PortListening $Port)) { Start-Sleep -Milliseconds 500 }

# 2. Force the tree if it's still up (safe here -- Studio state lives in sqlite).
if (Test-PortListening $Port) {
  Write-Host "Forcing shutdown (Windows can't deliver a graceful signal to a hidden-console server)..." -ForegroundColor Yellow
  foreach ($p in $pids) { Invoke-TaskKill -P $p -Force }
  Start-Sleep -Seconds 2
}

# 3. Sweep any orphaned Studio-owned llama-server child, then clear the pid file.
Get-Process -Name "llama-server" -ErrorAction SilentlyContinue |
  Where-Object { $_.Path -match [regex]::Escape($StudioHome) } |
  Stop-Process -Force -ErrorAction SilentlyContinue
if (Test-Path $pidFile) { Remove-Item $pidFile -Force -ErrorAction SilentlyContinue }

if (Test-PortListening $Port) {
  throw "Port $Port is still in use after stop -- check for another process bound to it."
}
Write-Host "Stopped Unsloth Studio (port $Port)." -ForegroundColor Green
