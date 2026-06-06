# Launch the Unsloth Studio web server.
#
# Bind 127.0.0.1 by default. Use -Lan to bind 0.0.0.0 for other machines, but
# note: browser mic / secure-context features only work when the page is opened
# via localhost/127.0.0.1 (or HTTPS), never via a plain-http LAN IP.

param(
  [string] $StudioHome = "E:\root\projects\unsloth",
  [string] $BindHost = "127.0.0.1",
  [string] $Model = $StudioHome + "\gemma-4-12b-it-qat-q4_0\gemma-4-12b-it-qat-q4_0.gguf",
  [int]    $Port = 8888,
  # Backend log verbosity (DEBUG | INFO | WARNING | ERROR). DEBUG is very chatty.
  [ValidateSet("DEBUG", "INFO", "WARNING", "ERROR")] [string] $LogLevel = "INFO",
  [switch] $Lan,       # bind 0.0.0.0 instead of 127.0.0.1
  [switch] $Open       # open the browser at http://localhost:<port>
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\unsloth-common.ps1"

$unsloth = Resolve-UnslothExe $StudioHome
if ($Lan) { $BindHost = "0.0.0.0" }
$env:UNSLOTH_STUDIO_HOME = $StudioHome
# Structured logs: LOG_LEVEL sets verbosity; production => JSON lines (parseable).
$env:LOG_LEVEL = $LogLevel
$env:ENVIRONMENT_TYPE = "production"

if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
  Write-Host "A server is already listening on port $Port." -ForegroundColor Yellow
  if ($Open) { Start-Process "http://localhost:$Port" }
  return
}

$argList = @("studio", "-H", $BindHost, "-p", "$Port", "--model", "$Model", "--enable-tools", "--yes")

Write-Host "Starting Unsloth Studio on http://$BindHost`:$Port  (LOG_LEVEL=$LogLevel) ..."
Write-Host "Backend output will stream in this terminal. Press Ctrl+C to stop."
Write-Host "llama-server log: $StudioHome\logs\llama-server\llama-*-port-*.log"
if ($Lan) {
  Write-Host "Bound to 0.0.0.0 -- reachable on the LAN, but open it via localhost on THIS machine" -ForegroundColor Yellow
  Write-Host "for mic/secure-context features; LAN clients over plain http cannot use the mic."
}
if ($Open) {
  Write-Host "Opening http://localhost:$Port; refresh once Studio finishes starting if needed."
  Start-Process "http://localhost:$Port"
}

& $unsloth @argList
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
