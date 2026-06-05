param(
  [Parameter(Mandatory)] [ValidateSet("google-qat12", "ggml-12b-q4km", "litert-e4b", "litert-12b", "unsloth-26b-q3km")] [string] $Variant,
  [string] $ModelRoot = "E:\root\models"
)

. "$PSScriptRoot\common.ps1"

Ensure-Directory $ModelRoot

switch ($Variant) {
  "google-qat12" {
    $dir = Join-Path $ModelRoot "gemma-4-12b-it-qat-q4_0"
    Download-File "https://huggingface.co/google/gemma-4-12B-it-qat-q4_0-gguf/resolve/main/gemma-4-12b-it-qat-q4_0.gguf?download=true" (Join-Path $dir "gemma-4-12b-it-qat-q4_0.gguf")
    Download-File "https://huggingface.co/google/gemma-4-12B-it-qat-q4_0-gguf/resolve/main/mmproj-gemma-4-12b-it-qat-q4_0.gguf?download=true" (Join-Path $dir "mmproj-gemma-4-12b-it-qat-q4_0.gguf")
  }
  "ggml-12b-q4km" {
    $dir = Join-Path $ModelRoot "ggml-org-gemma-4-12b-it-q4km"
    Download-File "https://huggingface.co/ggml-org/gemma-4-12B-it-GGUF/resolve/main/gemma-4-12B-it-Q4_K_M.gguf?download=true" (Join-Path $dir "gemma-4-12B-it-Q4_K_M.gguf")
    Download-File "https://huggingface.co/ggml-org/gemma-4-12B-it-GGUF/resolve/main/mmproj-gemma-4-12B-it-Q8_0.gguf?download=true" (Join-Path $dir "mmproj-gemma-4-12B-it-Q8_0.gguf")
  }
  "litert-e4b" {
    $dir = Join-Path $ModelRoot "litert-gemma-4-e4b-it"
    Download-File "https://huggingface.co/litert-community/gemma-4-E4B-it-litert-lm/resolve/main/gemma-4-E4B-it.litertlm?download=true" (Join-Path $dir "gemma-4-E4B-it.litertlm")
  }
  "litert-12b" {
    $dir = Join-Path $ModelRoot "litert-gemma-4-12b-it"
    Download-File "https://huggingface.co/litert-community/gemma-4-12B-it-litert-lm/resolve/main/gemma-4-12B-it.litertlm?download=true" (Join-Path $dir "gemma-4-12B-it.litertlm")
  }
  "unsloth-26b-q3km" {
    $dir = Join-Path $ModelRoot "unsloth-gemma-4-26b-a4b-it-ud-q3km"
    Download-File "https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF/resolve/main/gemma-4-26B-A4B-it-UD-Q3_K_M.gguf?download=true" (Join-Path $dir "gemma-4-26B-A4B-it-UD-Q3_K_M.gguf")
    Download-File "https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF/resolve/main/mmproj-F16.gguf?download=true" (Join-Path $dir "mmproj-F16.gguf")
  }
}

Write-Host "Downloaded $Variant under $ModelRoot"

