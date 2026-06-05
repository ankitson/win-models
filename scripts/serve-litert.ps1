param(
  [Parameter(Mandatory)] [ValidateSet("litert-e4b", "litert-12b")] [string] $Variant,
  [string] $ModelRoot = "E:\root\models",
  [string] $BindHost = "0.0.0.0",
  [int] $Port = 9379
)

. "$PSScriptRoot\common.ps1"

switch ($Variant) {
  "litert-e4b" {
    $dir = Join-Path $ModelRoot "litert-gemma-4-e4b-it"
    $file = Join-Path $dir "gemma-4-E4B-it.litertlm"
    $id = "gemma-4-E4B-it"
  }
  "litert-12b" {
    $dir = Join-Path $ModelRoot "litert-gemma-4-12b-it"
    $file = Join-Path $dir "gemma-4-12B-it.litertlm"
    $id = "gemma-4-12B-it"
  }
}

if (-not (Test-Path $file)) {
  throw "Missing $file. Run: just download $Variant"
}

Get-Process llama-server,litert-lm -ErrorAction SilentlyContinue | Stop-Process -Force
litert-lm import $file $id
if ($LASTEXITCODE -ne 0) {
  throw "litert-lm import failed"
}

$outLog = Join-Path $dir "server.out.log"
$errLog = Join-Path $dir "server.err.log"
$process = Start-Process -FilePath "litert-lm" -ArgumentList @("serve", "--host", $BindHost, "--port", "$Port", "--verbose") -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
Write-Host "Started LiteRT-LM $Variant on http://$BindHost`:$Port/v1 (PID $($process.Id))"
Write-Host "Use model id: $id,gpu"
Wait-OpenAiServer "http://127.0.0.1:$Port/v1" 60 | Out-Null
Write-Host "Ready: $id,gpu"
