Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot

. (Join-Path $PSScriptRoot "Import-DotEnvSecret.ps1")
Import-WinModelsDotEnvSecret -Path (Join-Path $RepoRoot ".env.secret")

$StudioHome = if ($env:UNSLOTH_STUDIO_HOME) { $env:UNSLOTH_STUDIO_HOME } else { "E:\root\projects\unsloth" }
$StudioPort = if ($env:WIN_MODELS_STUDIO_PORT) { $env:WIN_MODELS_STUDIO_PORT } else { "8888" }
$LlamaPort = if ($env:UNSLOTH_LLAMA_PORT) { $env:UNSLOTH_LLAMA_PORT } elseif ($env:WIN_MODELS_LLAMA_PORT) { $env:WIN_MODELS_LLAMA_PORT } else { "8080" }
$AsrPort = if ($env:WIN_MODELS_ASR_PORT) { $env:WIN_MODELS_ASR_PORT } else { "8891" }

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

$just = Get-Command just -ErrorAction Stop
$uv = Get-Command uv -ErrorAction Stop

& $uv.Source --project $RepoRoot run win-models unsloth stop --studio-home $StudioHome --port $StudioPort
$llamaConnections = @(Get-NetTCPConnection -State Listen -LocalPort ([int]$LlamaPort) -ErrorAction SilentlyContinue)
foreach ($connection in $llamaConnections) {
    $process = Get-Process -Id $connection.OwningProcess -ErrorAction SilentlyContinue
    if ($process -and $process.ProcessName -eq "llama-server") {
        Stop-Process -Id $process.Id -Force
        Write-Output ("Stopped orphaned llama-server PID {0} listening on port {1}." -f $process.Id, $LlamaPort)
    }
}
& $uv.Source --project $RepoRoot run win-models lmstudio stop
& $uv.Source --project $RepoRoot run win-models parakeet stop --port $AsrPort
& $just.Source comfy stop

Write-Output "Stopped edge stack."
