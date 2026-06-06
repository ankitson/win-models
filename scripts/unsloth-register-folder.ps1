# Register a directory as an Unsloth Studio custom model-scan folder, so the
# web UI's Models page lists local GGUF/HF models under it. Same effect as the
# UI's "Models -> Custom Folders -> Add folder". Persisted in studio.db.
#
# Only GGUF (and HF safetensors/bin dirs) are recognized; .litertlm and plain
# binary-build folders are correctly ignored.

param(
  [string] $StudioHome = "E:\root\projects\unsloth",
  [string] $Path       = "E:\root\projects\models"
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\unsloth-common.ps1"

Resolve-UnslothExe $StudioHome | Out-Null
Register-ScanFolder -StudioHome $StudioHome -Path $Path
Write-Host "Registered $Path. Refresh the Models page in the web UI to see it." -ForegroundColor Green
