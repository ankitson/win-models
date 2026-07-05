Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot

. (Join-Path $PSScriptRoot "Import-DotEnvSecret.ps1")
Import-WinModelsDotEnvSecret -Path (Join-Path $RepoRoot ".env.secret")

$StudioHome = if ($env:UNSLOTH_STUDIO_HOME) { $env:UNSLOTH_STUDIO_HOME } else { "E:\root\projects\unsloth" }
$StudioPort = if ($env:WIN_MODELS_STUDIO_PORT) { $env:WIN_MODELS_STUDIO_PORT } else { "8888" }
$LlamaPort = if ($env:UNSLOTH_LLAMA_PORT) { $env:UNSLOTH_LLAMA_PORT } elseif ($env:WIN_MODELS_LLAMA_PORT) { $env:WIN_MODELS_LLAMA_PORT } else { "8080" }
$AsrPort = if ($env:WIN_MODELS_ASR_PORT) { $env:WIN_MODELS_ASR_PORT } else { "8891" }
$ComfyPort = if ($env:COMFYUI_PORT) { $env:COMFYUI_PORT } else { "8188" }
$WinModelsPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $WinModelsPython)) {
    throw "Missing repo venv Python at $WinModelsPython. Run `uv sync` once before using just stop."
}
$priorPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = if ($priorPythonPath) { "$RepoRoot\src;$priorPythonPath" } else { "$RepoRoot\src" }

function Invoke-WinModels {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & $WinModelsPython -m win_models.cli @Arguments
}

function Stop-StaleWinModelsConsoleScripts {
    $escapedRepo = [regex]::Escape($RepoRoot)
    $processes = Get-CimInstance Win32_Process |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine -match $escapedRepo -and
            (
                $_.CommandLine -match '\\.venv\\Scripts\\win-models\.exe' -or
                $_.CommandLine -match '\\buv(?:\\.exe)?\\b.*\\brun\\s+win-models\\b'
            )
        }
    foreach ($process in $processes) {
        try {
            Stop-Process -Id $process.ProcessId -Force
            Write-Output ("Stopped stale win-models console-script wrapper PID {0}." -f $process.ProcessId)
        } catch {
            Write-Output ("Could not stop stale win-models wrapper PID {0}: {1}" -f $process.ProcessId, $_.Exception.Message)
        }
    }
}

function Stop-CaddyForEdge {
    $caddy = Get-Command caddy -ErrorAction SilentlyContinue
    if ($caddy) {
        try {
            & $caddy.Source stop 2>$null | Out-Null
            Write-Output "Stopped Caddy via caddy stop."
        } catch {
            Write-Output "caddy stop did not complete cleanly; checking port 443."
        }
    } else {
        Write-Output "caddy command not found; checking port 443."
    }

    $connections = @(Get-NetTCPConnection -State Listen -LocalPort 443 -ErrorAction SilentlyContinue)
    foreach ($connection in $connections) {
        $process = Get-Process -Id $connection.OwningProcess -ErrorAction SilentlyContinue
        if ($process -and $process.ProcessName -eq "caddy") {
            Stop-Process -Id $process.Id -Force
            Write-Output ("Stopped Caddy PID {0} listening on port 443." -f $process.Id)
        }
    }
}

Stop-CaddyForEdge

Invoke-WinModels unsloth stop --studio-home $StudioHome --port $StudioPort
$llamaConnections = @(Get-NetTCPConnection -State Listen -LocalPort ([int]$LlamaPort) -ErrorAction SilentlyContinue)
foreach ($connection in $llamaConnections) {
    $process = Get-Process -Id $connection.OwningProcess -ErrorAction SilentlyContinue
    if ($process -and $process.ProcessName -eq "llama-server") {
        Stop-Process -Id $process.Id -Force
        Write-Output ("Stopped orphaned llama-server PID {0} listening on port {1}." -f $process.Id, $LlamaPort)
    }
}
Invoke-WinModels lmstudio stop
Invoke-WinModels parakeet stop --port $AsrPort
Invoke-WinModels comfy stop --port $ComfyPort
Stop-StaleWinModelsConsoleScripts

if ($null -eq $priorPythonPath) {
    Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue
} else {
    $env:PYTHONPATH = $priorPythonPath
}

Write-Output "Stopped edge stack."
