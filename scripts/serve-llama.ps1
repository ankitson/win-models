param(
  [Parameter(Mandatory)] [ValidateSet("google-qat12", "ggml-12b-q4km", "unsloth-26b-q3km")] [string] $Variant,
  [string] $ModelRoot = "E:\root\models",
  [string] $BindHost = "0.0.0.0",
  [int] $Port = 8080,
  [ValidateSet("on", "off", "auto")] [string] $Reasoning = "on",
  [int] $ContextSize = 8192,
  [string] $GpuLayers = "all",
  [int] $CacheRam = 8192
)

. "$PSScriptRoot\common.ps1"

$run = Get-LlamaRunDir $ModelRoot

switch ($Variant) {
  "google-qat12" {
    $dir = Join-Path $ModelRoot "gemma-4-12b-it-qat-q4_0"
    $model = Join-Path $dir "gemma-4-12b-it-qat-q4_0.gguf"
    $mmproj = Join-Path $dir "mmproj-gemma-4-12b-it-qat-q4_0.gguf"
    $alias = "gemma-4-12b-qat"
  }
  "ggml-12b-q4km" {
    $dir = Join-Path $ModelRoot "ggml-org-gemma-4-12b-it-q4km"
    $model = Join-Path $dir "gemma-4-12B-it-Q4_K_M.gguf"
    $mmproj = Join-Path $dir "mmproj-gemma-4-12B-it-Q8_0.gguf"
    $alias = "gemma-4-12b-ggml-q4km"
  }
  "unsloth-26b-q3km" {
    $dir = Join-Path $ModelRoot "unsloth-gemma-4-26b-a4b-it-ud-q3km"
    $model = Join-Path $dir "gemma-4-26B-A4B-it-UD-Q3_K_M.gguf"
    $mmproj = Join-Path $dir "mmproj-F16.gguf"
    $alias = "gemma-4-26b-a4b-unsloth-q3km"
  }
}

foreach ($path in @($model, $mmproj)) {
  if (-not (Test-Path $path)) {
    throw "Missing $path. Run: just download $Variant"
  }
}

Get-Process llama-server,litert-lm -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 1

$media = Join-Path $dir "media"
Ensure-Directory $media
$outLog = Join-Path $dir "server.out.log"
$errLog = Join-Path $dir "server.err.log"

$args = @(
  "-m", $model,
  "--mmproj", $mmproj,
  "--host", $BindHost,
  "--port", "$Port",
  "--alias", $alias,
  "--ctx-size", "$ContextSize",
  "--parallel", "1",
  "--n-gpu-layers", "$GpuLayers",
  "--flash-attn", "on",
  "--cache-type-k", "q8_0",
  "--cache-type-v", "q8_0",
  "--media-path", $media,
  "--cache-ram", "$CacheRam",
  "--jinja",
  "--reasoning", $Reasoning,
  "--reasoning-format", "deepseek",
  "--sleep-idle-seconds", "120"
)

$process = Start-Process -FilePath (Join-Path $run "llama-server.exe") -ArgumentList $args -WorkingDirectory $run -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
Write-Host "Started $Variant as $alias on http://$BindHost`:$Port/v1 (PID $($process.Id))"
Write-Host "Logs: $errLog"
Wait-OpenAiServer "http://127.0.0.1:$Port/v1" 120 | Out-Null
Write-Host "Ready: $alias"
