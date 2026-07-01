Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $RepoRoot "logs"
$CaddyConfig = Join-Path $RepoRoot "Caddyfile"
$CloudflareOpRef = "op://clankers/cloudflare-ankitson-dns/password"

$StudioHome = if ($env:UNSLOTH_STUDIO_HOME) { $env:UNSLOTH_STUDIO_HOME } else { "E:\root\projects\unsloth" }
$ModelRoot = if ($env:WIN_MODELS_MODEL_ROOT) { $env:WIN_MODELS_MODEL_ROOT } else { "E:\root\projects\models" }
$HfCache = if ($env:UNSLOTH_HF_CACHE) { $env:UNSLOTH_HF_CACHE } else { $ModelRoot }
$DefaultModel = if ($env:UNSLOTH_DEFAULT_MODEL) { $env:UNSLOTH_DEFAULT_MODEL } else { "unsloth/gemma-4-26B-A4B-it-qat-GGUF:UD-Q4_K_XL" }
$ContextLength = if ($env:UNSLOTH_CONTEXT_LENGTH) { $env:UNSLOTH_CONTEXT_LENGTH } else { "131072" }
$Parallel = if ($env:UNSLOTH_PARALLEL) { $env:UNSLOTH_PARALLEL } else { "1" }
$CacheTypeKv = if ($env:UNSLOTH_CACHE_TYPE_KV) { $env:UNSLOTH_CACHE_TYPE_KV } else { "q8_0" }
$ReasoningFormat = if ($env:UNSLOTH_REASONING_FORMAT) { $env:UNSLOTH_REASONING_FORMAT } else { "deepseek" }
$ChatTemplateFile = if ($env:UNSLOTH_CHAT_TEMPLATE_FILE) { $env:UNSLOTH_CHAT_TEMPLATE_FILE } else { Join-Path $RepoRoot "src\unsloth\chat-templates\gemma-4-31b-it-pr118.jinja" }
$StudioPort = if ($env:WIN_MODELS_STUDIO_PORT) { $env:WIN_MODELS_STUDIO_PORT } else { "8888" }

$CaddyOutLog = Join-Path $LogDir "caddy.out.log"
$CaddyErrLog = Join-Path $LogDir "caddy.err.log"
$CaddyAccessLog = Join-Path $LogDir "caddy.access.log"
$UnslothOutLog = Join-Path $LogDir "edge-unsloth.out.log"
$UnslothErrLog = Join-Path $LogDir "edge-unsloth.err.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
foreach ($path in @($CaddyOutLog, $CaddyErrLog, $CaddyAccessLog, $UnslothOutLog, $UnslothErrLog)) {
    if (-not (Test-Path $path)) {
        New-Item -ItemType File -Path $path | Out-Null
    }
}
foreach ($path in @($CaddyOutLog, $CaddyErrLog, $UnslothOutLog, $UnslothErrLog)) {
    Clear-Content -Path $path -ErrorAction SilentlyContinue
}

$Op = Get-Command op -ErrorAction Stop
$Just = Get-Command just -ErrorAction Stop
$Uv = Get-Command uv -ErrorAction Stop
$Caddy = Get-Command caddy -ErrorAction Stop

$env:CLOUDFLARE_API_TOKEN = (& $Op.Source read $CloudflareOpRef).Trim()
if (-not $env:CLOUDFLARE_API_TOKEN) {
    throw "1Password returned an empty Cloudflare API token from $CloudflareOpRef"
}

& $Caddy.Source validate --config $CaddyConfig

try {
    & $Caddy.Source stop 2>$null | Out-Null
} catch {
}

if (Get-NetTCPConnection -State Listen -LocalPort 443 -ErrorAction SilentlyContinue) {
    throw "Port 443 is already in use after attempting to stop Caddy. Free the port, then re-run just serve-edge."
}

& $Uv.Source --project $RepoRoot run win-models unsloth sync-mcp --studio-home $StudioHome
& $Just.Source comfy serve

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
    "--port", $StudioPort
)
if ($ChatTemplateFile) {
    $unslothArgs += @("--chat-template-file", $ChatTemplateFile)
}

$unslothProcess = Start-Process -FilePath $Uv.Source -ArgumentList $unslothArgs -WorkingDirectory $RepoRoot -WindowStyle Hidden -RedirectStandardOutput $UnslothOutLog -RedirectStandardError $UnslothErrLog -PassThru
$caddyProcess = Start-Process -FilePath $Caddy.Source -ArgumentList @("run", "--config", $CaddyConfig) -WorkingDirectory $RepoRoot -WindowStyle Hidden -RedirectStandardOutput $CaddyOutLog -RedirectStandardError $CaddyErrLog -PassThru

Write-Output ("Started ComfyUI via `just comfy serve`.")
Write-Output ("Started Unsloth background PID {0}. Logs: {1}, {2}" -f $unslothProcess.Id, $UnslothOutLog, $UnslothErrLog)
Write-Output ("Started Caddy background PID {0}. Logs: {1}, {2}, access {3}" -f $caddyProcess.Id, $CaddyOutLog, $CaddyErrLog, $CaddyAccessLog)
