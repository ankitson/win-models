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
$ComfyHome = if ($env:COMFYUI_HOME) { $env:COMFYUI_HOME } else { "E:\root\projects\comfyui" }
$ComfyModelRoot = if ($env:COMFYUI_MODEL_ROOT) { $env:COMFYUI_MODEL_ROOT } else { Join-Path $ModelRoot "comfyui" }
$ComfyPort = if ($env:COMFYUI_PORT) { $env:COMFYUI_PORT } else { "8188" }
$ComfyMemory = if ($env:COMFYUI_MEMORY) { $env:COMFYUI_MEMORY } else { "auto" }
$DefaultModel = if ($env:UNSLOTH_DEFAULT_MODEL) { $env:UNSLOTH_DEFAULT_MODEL } else { "unsloth/gemma-4-26B-A4B-it-qat-GGUF:UD-Q4_K_XL" }
$ContextLength = if ($env:UNSLOTH_CONTEXT_LENGTH) { $env:UNSLOTH_CONTEXT_LENGTH } else { "131072" }
$Parallel = if ($env:UNSLOTH_PARALLEL) { $env:UNSLOTH_PARALLEL } else { "1" }
$CacheTypeKv = if ($env:UNSLOTH_CACHE_TYPE_KV) { $env:UNSLOTH_CACHE_TYPE_KV } else { "q8_0" }
$ReasoningFormat = if ($env:UNSLOTH_REASONING_FORMAT) { $env:UNSLOTH_REASONING_FORMAT } else { "deepseek" }
$SpeculativeType = if ($env:UNSLOTH_SPECULATIVE_TYPE) { $env:UNSLOTH_SPECULATIVE_TYPE } else { "off" }
$LlamaExtraArgs = if ($env:UNSLOTH_LLAMA_EXTRA_ARGS) { $env:UNSLOTH_LLAMA_EXTRA_ARGS } else { "--no-mmap --batch-size 256 --ubatch-size 512" }
$ChatTemplateFile = if ($env:UNSLOTH_CHAT_TEMPLATE_FILE) { $env:UNSLOTH_CHAT_TEMPLATE_FILE } else { Join-Path $RepoRoot "src\unsloth\chat-templates\gemma-4-31b-it-pr118.jinja" }
$PromptLog = if ($env:UNSLOTH_PROMPT_LOG) { $env:UNSLOTH_PROMPT_LOG } else { "1" }
$PromptLogFile = if ($env:UNSLOTH_PROMPT_LOG_FILE) { $env:UNSLOTH_PROMPT_LOG_FILE } else { Join-Path $LogDir "unsloth-prompts.jsonl" }
$StudioPort = if ($env:WIN_MODELS_STUDIO_PORT) { $env:WIN_MODELS_STUDIO_PORT } else { "8888" }
$LlamaPort = if ($env:UNSLOTH_LLAMA_PORT) { $env:UNSLOTH_LLAMA_PORT } elseif ($env:WIN_MODELS_LLAMA_PORT) { $env:WIN_MODELS_LLAMA_PORT } else { "8080" }
$UnslothSourceRepo = if ($env:UNSLOTH_SOURCE_REPO) { $env:UNSLOTH_SOURCE_REPO } else { "" }
$UnslothSourceBuildFrontend = if ($env:UNSLOTH_SOURCE_BUILD_FRONTEND) { $env:UNSLOTH_SOURCE_BUILD_FRONTEND } else { "0" }
$LmstudioEnabled = if ($env:WIN_MODELS_LMSTUDIO_ENABLED) { $env:WIN_MODELS_LMSTUDIO_ENABLED } else { "0" }
$LmstudioHost = if ($env:LMSTUDIO_HOST) { $env:LMSTUDIO_HOST } else { "127.0.0.1" }
$LmstudioPort = if ($env:LMSTUDIO_PORT) { $env:LMSTUDIO_PORT } else { "1234" }
$LmstudioModelRoot = if ($env:LMSTUDIO_MODEL_ROOT) { $env:LMSTUDIO_MODEL_ROOT } else { Join-Path $env:USERPROFILE ".lmstudio\models" }
$LmstudioContextLength = if ($env:LMSTUDIO_CONTEXT_LENGTH) { $env:LMSTUDIO_CONTEXT_LENGTH } elseif ($env:UNSLOTH_CONTEXT_LENGTH) { $env:UNSLOTH_CONTEXT_LENGTH } else { "131072" }
$LmstudioGpu = if ($env:LMSTUDIO_GPU) { $env:LMSTUDIO_GPU } else { "max" }
$LmstudioDefaultModel = if ($env:LMSTUDIO_DEFAULT_MODEL) { $env:LMSTUDIO_DEFAULT_MODEL } else { "" }
$AsrEnabled = if ($env:WIN_MODELS_ASR_ENABLED) { $env:WIN_MODELS_ASR_ENABLED } else { "0" }
$AsrHome = if ($env:WIN_MODELS_PARAKEET_HOME) { $env:WIN_MODELS_PARAKEET_HOME } else { "E:\root\projects\parakeet-asr" }
$AsrHost = if ($env:WIN_MODELS_ASR_HOST) { $env:WIN_MODELS_ASR_HOST } else { "127.0.0.1" }
$AsrPort = if ($env:WIN_MODELS_ASR_PORT) { $env:WIN_MODELS_ASR_PORT } else { "8891" }
$AsrModel = if ($env:WIN_MODELS_ASR_MODEL) { $env:WIN_MODELS_ASR_MODEL } else { "nvidia/parakeet-tdt-0.6b-v2" }

$CaddyOutLog = Join-Path $LogDir "caddy.out.log"
$CaddyErrLog = Join-Path $LogDir "caddy.err.log"
$CaddyAccessLog = Join-Path $LogDir "caddy.access.log"
$UnslothOutLog = Join-Path $LogDir "edge-unsloth.out.log"
$UnslothErrLog = Join-Path $LogDir "edge-unsloth.err.log"
$LmstudioOutLog = Join-Path $LogDir "lmstudio.out.log"
$LmstudioErrLog = Join-Path $LogDir "lmstudio.err.log"
$AsrOutLog = Join-Path $LogDir "parakeet-asr.out.log"
$AsrErrLog = Join-Path $LogDir "parakeet-asr.err.log"
$WinModelsPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $WinModelsPython)) {
    throw "Missing repo venv Python at $WinModelsPython. Run `uv sync` once before using just serve."
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$env:UNSLOTH_PROMPT_LOG = $PromptLog
$env:UNSLOTH_PROMPT_LOG_FILE = $PromptLogFile
foreach ($path in @($CaddyOutLog, $CaddyErrLog, $CaddyAccessLog, $UnslothOutLog, $UnslothErrLog, $LmstudioOutLog, $LmstudioErrLog, $AsrOutLog, $AsrErrLog)) {
    if (-not (Test-Path $path)) {
        New-Item -ItemType File -Path $path | Out-Null
    }
}
foreach ($path in @($CaddyOutLog, $CaddyErrLog, $UnslothOutLog, $UnslothErrLog, $LmstudioOutLog, $LmstudioErrLog, $AsrOutLog, $AsrErrLog)) {
    Clear-Content -Path $path -ErrorAction SilentlyContinue
}

$Caddy = Get-Command caddy -ErrorAction Stop
$priorPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = if ($priorPythonPath) { "$RepoRoot\src;$priorPythonPath" } else { "$RepoRoot\src" }

function Invoke-WinModels {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & $WinModelsPython -m win_models.cli @Arguments
}

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

Invoke-WinModels unsloth sync-mcp --studio-home $StudioHome
# ComfyUI disabled -- uncomment to re-enable
# $comfyArgs = @(
#     "-m", "win_models.cli",
#     "comfy", "serve",
#     "--comfy-home", $ComfyHome,
#     "--model-root", $ComfyModelRoot,
#     "--host", "127.0.0.1",
#     "--port", $ComfyPort,
#     "--log-dir", $LogDir,
#     "--memory-mode", $ComfyMemory
# )
# $comfyProcess = Start-Process -FilePath $WinModelsPython -ArgumentList $comfyArgs -WorkingDirectory $RepoRoot -WindowStyle Hidden -PassThru
$comfyProcess = $null

$lmstudioProcess = $null
if ($LmstudioEnabled -ne "0") {
    $lmSetupArgs = @(
        "-m", "win_models.cli",
        "lmstudio", "setup",
        "--hf-cache", $HfCache,
        "--lmstudio-model-root", $LmstudioModelRoot
    )
    $lmSetup = Start-Process -FilePath $WinModelsPython -ArgumentList $lmSetupArgs -WorkingDirectory $RepoRoot -WindowStyle Hidden -RedirectStandardOutput $LmstudioOutLog -RedirectStandardError $LmstudioErrLog -PassThru -Wait
    if ($lmSetup.ExitCode -ne 0) {
        Write-Output ("LM Studio model sync exited with code {0}; see {1} and {2}." -f $lmSetup.ExitCode, $LmstudioOutLog, $LmstudioErrLog)
    }

    $lmServeArgs = @(
        "-m", "win_models.cli",
        "lmstudio", "serve",
        "--host", $LmstudioHost,
        "--port", $LmstudioPort,
        "--context-length", $LmstudioContextLength,
        "--gpu", $LmstudioGpu
    )
    if ($LmstudioDefaultModel) {
        $lmServeArgs += @("--model", $LmstudioDefaultModel)
    }
    $lmstudioProcess = Start-Process -FilePath $WinModelsPython -ArgumentList $lmServeArgs -WorkingDirectory $RepoRoot -WindowStyle Hidden -RedirectStandardOutput $LmstudioOutLog -RedirectStandardError $LmstudioErrLog -PassThru
} else {
    Write-Output "LM Studio integration disabled by WIN_MODELS_LMSTUDIO_ENABLED=0."
}

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
    "-m", "win_models.cli",
    "unsloth", "serve",
    "--studio-home", $StudioHome,
    "--hf-cache-dir", $HfCache,
    "--model", $DefaultModel,
    "--max-seq-length", $ContextLength,
    "--parallel", $Parallel,
    "--cache-type-kv", $CacheTypeKv,
    "--reasoning-format", $ReasoningFormat,
    "--speculative-type", $SpeculativeType,
    "--port", $StudioPort,
    "--llama-port", $LlamaPort
)
if ($ChatTemplateFile) {
    $unslothArgs += @("--chat-template-file", $ChatTemplateFile)
}
if ($UnslothSourceRepo) {
    $unslothArgs += @("--source-repo", $UnslothSourceRepo)
    if ($UnslothSourceBuildFrontend -in @("1", "true", "yes", "on")) {
        $unslothArgs += @("--source-build-frontend")
    }
}
if ($LlamaExtraArgs) {
    $unslothArgs += @("--") + ($LlamaExtraArgs -split '\s+')
}

$unslothProcess = Start-Process -FilePath $WinModelsPython -ArgumentList $unslothArgs -WorkingDirectory $RepoRoot -WindowStyle Hidden -RedirectStandardOutput $UnslothOutLog -RedirectStandardError $UnslothErrLog -PassThru
$caddyProcess = Start-Process -FilePath $Caddy.Source -ArgumentList @("run", "--config", $CaddyConfig) -WorkingDirectory $RepoRoot -WindowStyle Hidden -RedirectStandardOutput $CaddyOutLog -RedirectStandardError $CaddyErrLog -PassThru

if ($null -eq $priorPythonPath) {
    Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue
} else {
    $env:PYTHONPATH = $priorPythonPath
}

# ComfyUI disabled -- Write-Output ("Started ComfyUI background PID {0}. Logs: {1}, {2}" -f $comfyProcess.Id, (Join-Path $LogDir "comfyui.out.log"), (Join-Path $LogDir "comfyui.err.log"))
if ($asrProcess) {
    Write-Output ("Started Parakeet ASR background PID {0}. Logs: {1}, {2}" -f $asrProcess.Id, $AsrOutLog, $AsrErrLog)
}
if ($lmstudioProcess) {
    Write-Output ("Started/confirmed LM Studio background helper PID {0}. API: http://{1}:{2}/v1. Logs: {3}, {4}" -f $lmstudioProcess.Id, $LmstudioHost, $LmstudioPort, $LmstudioOutLog, $LmstudioErrLog)
}
Write-Output ("Started Unsloth background PID {0}. Logs: {1}, {2}" -f $unslothProcess.Id, $UnslothOutLog, $UnslothErrLog)
Write-Output ("Embedded llama-server target: http://127.0.0.1:{0}/v1 via https://llama.win.ankitson.com/v1 after the model loads." -f $LlamaPort)
Write-Output ("Started Caddy background PID {0}. Logs: {1}, {2}, access {3}" -f $caddyProcess.Id, $CaddyOutLog, $CaddyErrLog, $CaddyAccessLog)
