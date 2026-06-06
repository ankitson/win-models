# Launch the Unsloth Studio web UI with a model already loaded on startup.
#
# Plain `unsloth studio` has no "default model" — it boots with nothing loaded.
# `unsloth studio run --model X` boots the same web UI with X preloaded. This is
# the reproducible way to have gemma-4-12b ready the moment the page opens.
#
# Runs in the foreground so startup failures and server logs are visible.

param(
  [string] $StudioHome = "E:\root\projects\unsloth",
  [string] $ModelRoot  = "E:\root\projects\models",
  # Defaults to the gemma-4-12b-it QAT GGUF under ModelRoot; override with a path.
  [string] $Model      = "",
  [string] $BindHost   = "127.0.0.1",
  [int]    $Port       = 8888,
  [ValidateSet("DEBUG","INFO","WARNING","ERROR")] [string] $LogLevel = "INFO",
  [switch] $VerboseLlama,  # pass --verbose through to llama-server (full prompt logs)
  [switch] $EnableTools,   # allow tool calling (default off for a plain chat default)
  [switch] $Lan,
  [switch] $Open
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\unsloth-common.ps1"

$unsloth = Resolve-UnslothExe $StudioHome
if ($Lan) { $BindHost = "0.0.0.0" }
if (-not $Model) {
  $Model = Join-Path $ModelRoot "gemma-4-12b-it-qat-q4_0\gemma-4-12b-it-qat-q4_0.gguf"
}
if (-not (Test-Path $Model)) { throw "Model not found: $Model" }

$env:UNSLOTH_STUDIO_HOME = $StudioHome
$env:LOG_LEVEL = $LogLevel
$env:ENVIRONMENT_TYPE = "production"

if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
  Write-Host "A server is already listening on port $Port (stop it first: just studio-stop)." -ForegroundColor Yellow
  if ($Open) { Start-Process "http://localhost:$Port" }
  return
}

# --yes clears the network-bind prompt; tools default off so it never prompts.
$argList = @("studio", "run", "--model", $Model, "-H", $BindHost, "-p", "$Port", "--yes")
$argList += if ($EnableTools) { "--enable-tools" } else { "--disable-tools" }
if ($VerboseLlama) { $argList += "--verbose" }  # unknown flag -> passes to llama-server

Write-Host "Starting Unsloth Studio (model preloaded) on http://$BindHost`:$Port  (LOG_LEVEL=$LogLevel) ..."
Write-Host "Model:           $Model"
Write-Host "Backend output will stream in this terminal. Press Ctrl+C to stop."
Write-Host "llama-server log: $StudioHome\logs\llama-server\llama-*-port-*.log"
if ($Open) {
  Write-Host "Opening http://localhost:$Port; refresh once Studio finishes starting if needed."
  Start-Process "http://localhost:$Port"
}

& $unsloth @argList
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
