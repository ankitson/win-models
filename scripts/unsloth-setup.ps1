# Reproducible build/install of the Unsloth Studio server on this machine.
#
# Prereq: Unsloth Studio itself must already be installed (its venv at
# <StudioHome>\unsloth_studio) via Unsloth's official installer. This script
# does the parts that were otherwise fiddly / non-reproducible:
#   1. patch the llama.cpp prebuilt installer to reuse locally-downloaded zips
#      (GitHub throttles the large CUDA zips),
#   2. install the pinned cuda-13.3 llama.cpp build from those zips,
#   3. mark it Studio-owned and run `unsloth studio setup` to completion,
#   4. register the model folder so the web UI lists local GGUF models,
#   5. install the ~\.local\bin\unsloth.cmd launcher.
#
# Idempotent: safe to re-run.

param(
  [string] $StudioHome = "E:\root\projects\unsloth",
  [string] $ModelRoot  = "E:\root\projects\models",
  [string] $ZipDir     = "$env:USERPROFILE\Downloads",
  [string] $ReleaseTag = "b9536",
  [string] $Runtime    = "13.3",
  [switch] $SkipBase    # pass SKIP_STUDIO_BASE=1 (skip torch/deps re-check if already installed)
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\unsloth-common.ps1"

Write-Host "=== Unsloth Studio setup ===" -ForegroundColor Cyan
Write-Host "StudioHome=$StudioHome  ModelRoot=$ModelRoot  tag=$ReleaseTag  cuda=$Runtime"

# 1. Prereqs ----------------------------------------------------------------
$unsloth = Resolve-UnslothExe $StudioHome
$py      = Resolve-StudioPython $StudioHome
$llamaDir = Join-Path $StudioHome "llama.cpp"

# 2. Patch the prebuilt installer to use local zips -------------------------
Write-Host "`n[1/6] Patching llama.cpp prebuilt installer (local-zip shim)..."
Apply-LlamaLocalZipShim $StudioHome

# 3. Confirm the zips are present -------------------------------------------
Write-Host "`n[2/6] Locating prebuilt zips in $ZipDir ..."
$zips = Find-LlamaZips -ZipDir $ZipDir -ReleaseTag $ReleaseTag -Runtime $Runtime
Write-Host "  bin:    $($zips.Bin)"
Write-Host "  cudart: $($zips.Cudart)"

# 4. Install pinned llama.cpp from local zips -------------------------------
Write-Host "`n[3/6] Installing llama.cpp $ReleaseTag (cuda-$Runtime) from local zips..."
# Clear only partial staging dirs (always safe); leave any good install in place.
$staging = Join-Path $StudioHome ".staging"
if (Test-Path $staging) {
  Get-ChildItem $staging -Directory -Force -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-Item -Recurse -Force $_.FullName -ErrorAction SilentlyContinue }
}
$env:UNSLOTH_STUDIO_HOME      = $StudioHome
$env:UNSLOTH_LLAMA_LOCAL_DIR  = $ZipDir
$env:UNSLOTH_LLAMA_RELEASE_TAG = $ReleaseTag
$env:UNSLOTH_VERBOSE          = "1"
& $py (Join-Path (Get-StudioPackageDir $StudioHome) "install_llama_prebuilt.py") `
    --install-dir $llamaDir `
    --llama-tag $ReleaseTag `
    --published-repo "ggml-org/llama.cpp" `
    --published-release-tag $ReleaseTag `
    --simple-policy
if ($LASTEXITCODE -ne 0) { throw "llama.cpp prebuilt install failed (exit $LASTEXITCODE)" }

# 5. Mark Studio-owned + finish full setup ----------------------------------
Write-Host "`n[4/6] Marking install Studio-owned and running 'unsloth studio setup'..."
[System.IO.File]::WriteAllText((Join-Path $llamaDir ".unsloth-studio-owned"), "")
if ($SkipBase) { $env:SKIP_STUDIO_BASE = "1" } else { Remove-Item Env:\SKIP_STUDIO_BASE -ErrorAction SilentlyContinue }
& $unsloth studio setup --verbose
if ($LASTEXITCODE -ne 0) { throw "unsloth studio setup failed (exit $LASTEXITCODE)" }

# 6. Register the model folder so the web UI lists local models -------------
Write-Host "`n[5/6] Registering model folder $ModelRoot as a Studio scan folder..."
Register-ScanFolder -StudioHome $StudioHome -Path $ModelRoot

# 7. Launcher wrapper -------------------------------------------------------
Write-Host "`n[6/6] Installing unsloth.cmd launcher..."
Install-UnslothWrapper $StudioHome

Write-Host "`nDone. Launch the web UI with:  just studio-serve" -ForegroundColor Green
Write-Host "Then open http://localhost:8888 (use localhost, not the LAN IP, or mic/secure-context features stay disabled)."
