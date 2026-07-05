Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $RepoRoot "logs"
$CaddyConfig = Join-Path $RepoRoot "Caddyfile"
$CloudflareOpRef = "op://clankers/cloudflare-ankitson-dns/password"

. (Join-Path $PSScriptRoot "Import-DotEnvSecret.ps1")
Import-WinModelsDotEnvSecret -Path (Join-Path $RepoRoot ".env.secret")

$StudioHome = if ($env:UNSLOTH_STUDIO_HOME) { $env:UNSLOTH_STUDIO_HOME } else { "E:\root\projects\unsloth" }
$ModelRoot = if ($env:WIN_MODELS_MODEL_ROOT) { $env:WIN_MODELS_MODEL_ROOT } else { "E:\root\projects\models" }
$HfCache = if ($env:UNSLOTH_HF_CACHE) { $env:UNSLOTH_HF_CACHE } else { $ModelRoot }
$DefaultModel = if ($env:UNSLOTH_DEFAULT_MODEL) { $env:UNSLOTH_DEFAULT_MODEL } else { "unsloth/gemma-4-26B-A4B-it-qat-GGUF:UD-Q4_K_XL" }
$ContextLength = if ($env:UNSLOTH_CONTEXT_LENGTH) { $env:UNSLOTH_CONTEXT_LENGTH } else { "131072" }
$Parallel = if ($env:UNSLOTH_PARALLEL) { $env:UNSLOTH_PARALLEL } else { "1" }
$CacheTypeKv = if ($env:UNSLOTH_CACHE_TYPE_KV) { $env:UNSLOTH_CACHE_TYPE_KV } else { "q8_0" }
$ReasoningFormat = if ($env:UNSLOTH_REASONING_FORMAT) { $env:UNSLOTH_REASONING_FORMAT } else { "deepseek" }
$SpeculativeType = if ($env:UNSLOTH_SPECULATIVE_TYPE) { $env:UNSLOTH_SPECULATIVE_TYPE } else { "off" }
$ChatTemplateFile = if ($env:UNSLOTH_CHAT_TEMPLATE_FILE) { $env:UNSLOTH_CHAT_TEMPLATE_FILE } else { Join-Path $RepoRoot "src\unsloth\chat-templates\gemma-4-31b-it-pr118.jinja" }
$StudioPort = if ($env:WIN_MODELS_STUDIO_PORT) { $env:WIN_MODELS_STUDIO_PORT } else { "8888" }
$AsrEnabled = if ($env:WIN_MODELS_ASR_ENABLED) { $env:WIN_MODELS_ASR_ENABLED } else { "1" }
$AsrHome = if ($env:WIN_MODELS_PARAKEET_HOME) { $env:WIN_MODELS_PARAKEET_HOME } else { "E:\root\projects\parakeet-asr" }
$AsrHost = if ($env:WIN_MODELS_ASR_HOST) { $env:WIN_MODELS_ASR_HOST } else { "127.0.0.1" }
$AsrPort = if ($env:WIN_MODELS_ASR_PORT) { $env:WIN_MODELS_ASR_PORT } else { "8891" }
$AsrModel = if ($env:WIN_MODELS_ASR_MODEL) { $env:WIN_MODELS_ASR_MODEL } else { "nvidia/parakeet-tdt-0.6b-v2" }

$CaddyOutLog = Join-Path $LogDir "caddy.out.log"
$CaddyErrLog = Join-Path $LogDir "caddy.err.log"
$CaddyAccessLog = Join-Path $LogDir "caddy.access.log"
$UnslothOutLog = Join-Path $LogDir "edge-unsloth.out.log"
$UnslothErrLog = Join-Path $LogDir "edge-unsloth.err.log"
$AsrOutLog = Join-Path $LogDir "parakeet-asr.out.log"
$AsrErrLog = Join-Path $LogDir "parakeet-asr.err.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
foreach ($path in @($CaddyOutLog, $CaddyErrLog, $CaddyAccessLog, $UnslothOutLog, $UnslothErrLog, $AsrOutLog, $AsrErrLog)) {
    if (-not (Test-Path $path)) {
        New-Item -ItemType File -Path $path | Out-Null
    }
}
foreach ($path in @($CaddyOutLog, $CaddyErrLog, $UnslothOutLog, $UnslothErrLog, $AsrOutLog, $AsrErrLog)) {
    Clear-Content -Path $path -ErrorAction SilentlyContinue
}

$Just = Get-Command just -ErrorAction Stop
$Uv = Get-Command uv -ErrorAction Stop
$Caddy = Get-Command caddy -ErrorAction Stop

function Resolve-RequiredSecret {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$OpRef,
        [Parameter(Mandatory = $true)][string]$Purpose
    )

    $existing = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ($existing) {
        return
    }

    $Op = Get-Command op -ErrorAction SilentlyContinue
    if (-not $Op) {
        throw "$Name is missing. Add $Name=... to $RepoRoot\.env.secret or install/sign in to the 1Password CLI for $OpRef. Needed for: $Purpose."
    }

    $value = $null
    try {
        $value = (& $Op.Source read $OpRef 2>$null)
    } catch {
        $value = $null
    }
    $value = if ($null -eq $value) { "" } else { [string]($value -join "`n").Trim() }
    if (-not $value) {
        throw "$Name is missing. It was not found in $RepoRoot\.env.secret, and 1Password could not resolve $OpRef. Needed for: $Purpose."
    }

    [Environment]::SetEnvironmentVariable($Name, $value, "Process")
}

Resolve-RequiredSecret -Name "CLOUDFLARE_API_TOKEN" -OpRef $CloudflareOpRef -Purpose "Caddy DNS challenge for the HTTPS edge proxy"

& $Caddy.Source validate --config $CaddyConfig

try {
    & $Caddy.Source stop 2>$null | Out-Null
} catch {
}

if (Get-NetTCPConnection -State Listen -LocalPort 443 -ErrorAction SilentlyContinue) {
    throw "Port 443 is already in use after attempting to stop Caddy. Free the port, then re-run just serve."
}

& $Uv.Source --project $RepoRoot run win-models unsloth sync-mcp --studio-home $StudioHome
& $Just.Source comfy serve

$asrProcess = $null
if ($AsrEnabled -ne "0") {
    $asrPython = Join-Path $AsrHome ".venv\Scripts\python.exe"
    if (Test-Path $asrPython) {
        $existingAsr = @(Get-NetTCPConnection -State Listen -LocalPort ([int]$AsrPort) -ErrorAction SilentlyContinue)
        $env:UNSLOTH_ASR_FALLBACK_URL = "http://$AsrHost`:$AsrPort"
        if ($existingAsr.Count -gt 0) {
            Write-Output ("Parakeet ASR already listening on {0}. Using it for Studio voice fallback." -f $env:UNSLOTH_ASR_FALLBACK_URL)
        } else {
            $priorPythonPath = $env:PYTHONPATH
            $env:PYTHONPATH = if ($priorPythonPath) { "$RepoRoot\src;$priorPythonPath" } else { "$RepoRoot\src" }
            $asrArgs = @(
                "-m", "win_models.parakeet_asr",
                "serve",
                "--host", $AsrHost,
                "--port", $AsrPort,
                "--model", $AsrModel,
                "--cache-dir", $HfCache
            )
            $asrProcess = Start-Process -FilePath $asrPython -ArgumentList $asrArgs -WorkingDirectory $RepoRoot -WindowStyle Hidden -RedirectStandardOutput $AsrOutLog -RedirectStandardError $AsrErrLog -PassThru
            if ($null -eq $priorPythonPath) {
                Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue
            } else {
                $env:PYTHONPATH = $priorPythonPath
            }
        }
    } else {
        Write-Output ("Parakeet ASR venv not found at {0}. Run `just parakeet setup` to enable voice transcription fallback." -f $asrPython)
        Remove-Item Env:\UNSLOTH_ASR_FALLBACK_URL -ErrorAction SilentlyContinue
    }
} else {
    Remove-Item Env:\UNSLOTH_ASR_FALLBACK_URL -ErrorAction SilentlyContinue
}

$unslothArgs = @(
    "--project", $RepoRoot,
    "run", "win-models", "unsloth", "serve",
    "--studio-home", $StudioHome,
    "--hf-cache-dir", $HfCache,
    "--model", $DefaultModel,
    "--max-seq-length", $ContextLength,
    "--parallel", $Parallel,
    "--cache-type-kv", $CacheTypeKv,
    "--reasoning-format", $ReasoningFormat,
    "--speculative-type", $SpeculativeType,
    "--port", $StudioPort
)
if ($ChatTemplateFile) {
    $unslothArgs += @("--chat-template-file", $ChatTemplateFile)
}

$unslothProcess = Start-Process -FilePath $Uv.Source -ArgumentList $unslothArgs -WorkingDirectory $RepoRoot -WindowStyle Hidden -RedirectStandardOutput $UnslothOutLog -RedirectStandardError $UnslothErrLog -PassThru
$caddyProcess = Start-Process -FilePath $Caddy.Source -ArgumentList @("run", "--config", $CaddyConfig) -WorkingDirectory $RepoRoot -WindowStyle Hidden -RedirectStandardOutput $CaddyOutLog -RedirectStandardError $CaddyErrLog -PassThru

Write-Output ("Started ComfyUI via `just comfy serve`.")
if ($asrProcess) {
    Write-Output ("Started Parakeet ASR background PID {0}. Logs: {1}, {2}" -f $asrProcess.Id, $AsrOutLog, $AsrErrLog)
}
Write-Output ("Started Unsloth background PID {0}. Logs: {1}, {2}" -f $unslothProcess.Id, $UnslothOutLog, $UnslothErrLog)
Write-Output ("Started Caddy background PID {0}. Logs: {1}, {2}, access {3}" -f $caddyProcess.Id, $CaddyOutLog, $CaddyErrLog, $CaddyAccessLog)
