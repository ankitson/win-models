function Ensure-Directory {
  param([Parameter(Mandatory)] [string] $Path)
  New-Item -ItemType Directory -Force -Path $Path | Out-Null
}

function Download-File {
  param(
    [Parameter(Mandatory)] [string] $Url,
    [Parameter(Mandatory)] [string] $OutFile
  )

  Ensure-Directory (Split-Path -Parent $OutFile)
  if (Test-Path $OutFile) {
    Write-Host "Resuming or verifying: $OutFile"
  } else {
    Write-Host "Downloading: $OutFile"
  }
  curl.exe -L -C - $Url -o $OutFile
  if ($LASTEXITCODE -ne 0) {
    throw "curl failed with exit code $LASTEXITCODE for $Url"
  }
}

function Get-LlamaRunDir {
  param([Parameter(Mandatory)] [string] $ModelRoot)
  $run = Join-Path $ModelRoot "llama-b9536-cuda12\run"
  $exe = Join-Path $run "llama-server.exe"
  if (-not (Test-Path $exe)) {
    throw "Missing llama.cpp server at $exe. Download/extract llama.cpp b9536 CUDA first."
  }
  return $run
}

function Wait-OpenAiServer {
  param(
    [Parameter(Mandatory)] [string] $BaseUrl,
    [int] $TimeoutSeconds = 90
  )

  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  do {
    try {
      $result = Invoke-RestMethod -Uri "$BaseUrl/models" -Method Get -TimeoutSec 5
      return $result
    } catch {
      Start-Sleep -Seconds 2
    }
  } while ((Get-Date) -lt $deadline)

  throw "Timed out waiting for $BaseUrl/models"
}

