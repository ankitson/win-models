# Shared helpers for the Unsloth Studio (web UI) recipes.
# Dot-sourced by the unsloth-*.ps1 scripts.

# Reuse the repo's base helpers (Ensure-Directory, Download-File, etc.).
. "$PSScriptRoot\common.ps1"

function Resolve-UnslothExe {
  param([Parameter(Mandatory)] [string] $StudioHome)
  $exe = Join-Path $StudioHome "unsloth_studio\Scripts\unsloth.exe"
  if (-not (Test-Path $exe)) {
    throw @"
Unsloth Studio is not installed at $StudioHome.
Install it first per Unsloth's official docs (https://unsloth.ai) -- that creates
the venv at <StudioHome>\unsloth_studio. Then re-run this. Override the location
with -StudioHome if you installed it elsewhere.
"@
  }
  return $exe
}

function Resolve-StudioPython {
  param([Parameter(Mandatory)] [string] $StudioHome)
  $py = Join-Path $StudioHome "unsloth_studio\Scripts\python.exe"
  if (-not (Test-Path $py)) { throw "Missing venv python at $py" }
  return $py
}

function Get-StudioPackageDir {
  param([Parameter(Mandatory)] [string] $StudioHome)
  return Join-Path $StudioHome "unsloth_studio\Lib\site-packages\studio"
}

# Idempotently teach install_llama_prebuilt.py to reuse an already-downloaded
# release asset of the same name instead of fetching it from GitHub (which
# throttles the large CUDA zips). The official sha256 check + post-install
# smoke test still run on the copied file, so a bad local file is still caught.
function Apply-LlamaLocalZipShim {
  param([Parameter(Mandatory)] [string] $StudioHome)

  $pkg = Get-StudioPackageDir $StudioHome
  $target = Join-Path $pkg "install_llama_prebuilt.py"
  if (-not (Test-Path $target)) { throw "Missing $target" }

  $text = Get-Content -LiteralPath $target -Raw
  if ($text -match "UNSLOTH_LOCAL_ZIP_SHIM" -or $text -match "using local asset") {
    Write-Host "  llama local-zip shim already present"
    return
  }

  $anchor = "def download_file(url: str, destination: Path) -> None:`n    destination.parent.mkdir(parents = True, exist_ok = True)`n"
  if (-not $text.Contains($anchor)) {
    throw "Could not find the download_file() anchor in $target -- the installer changed; patch manually or update this script."
  }

  $shim = @"
def download_file(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents = True, exist_ok = True)
    # UNSLOTH_LOCAL_ZIP_SHIM: reuse a manually-downloaded asset of the same name
    # (GitHub throttles the large release zips). Searches UNSLOTH_LLAMA_LOCAL_DIR
    # (os.pathsep-separated) then ~/Downloads. sha256/validation still run.
    _shim_dirs = []
    _shim_env = os.environ.get("UNSLOTH_LLAMA_LOCAL_DIR")
    if _shim_env:
        _shim_dirs.extend(p for p in _shim_env.split(os.pathsep) if p.strip())
    _shim_home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    if _shim_home:
        _shim_dirs.append(os.path.join(_shim_home, "Downloads"))
    for _shim_dir in _shim_dirs:
        try:
            _shim_cand = Path(_shim_dir) / destination.name
            if _shim_cand.is_file() and _shim_cand.stat().st_size > 0:
                log(f"using local asset {_shim_cand} instead of downloading {url}")
                shutil.copyfile(_shim_cand, destination)
                return
        except OSError:
            continue
"@ -replace "`r`n", "`n"

  $patched = $text.Replace($anchor, $shim + "`n")
  # Write LF-only UTF-8 (no BOM) so Python is happy.
  [System.IO.File]::WriteAllText($target, $patched, (New-Object System.Text.UTF8Encoding($false)))
  Write-Host "  applied llama local-zip shim to install_llama_prebuilt.py"
}

# Locate the prebuilt bin zip + paired cudart zip for a given tag/runtime.
function Find-LlamaZips {
  param(
    [Parameter(Mandatory)] [string] $ZipDir,
    [Parameter(Mandatory)] [string] $ReleaseTag,
    [Parameter(Mandatory)] [string] $Runtime
  )
  $binName    = "llama-$ReleaseTag-bin-win-cuda-$Runtime-x64.zip"
  $cudartName = "cudart-llama-bin-win-cuda-$Runtime-x64.zip"
  $bin    = Join-Path $ZipDir $binName
  $cudart = Join-Path $ZipDir $cudartName
  $missing = @()
  if (-not (Test-Path $bin))    { $missing += $binName }
  if (-not (Test-Path $cudart)) { $missing += $cudartName }
  if ($missing.Count -gt 0) {
    $base = "https://github.com/ggml-org/llama.cpp/releases/download/$ReleaseTag"
    $lines = $missing | ForEach-Object { "  $base/$_" }
    throw @"
Missing prebuilt zip(s) in ${ZipDir}:
$($missing -join "`n")
Download them (a fast downloader such as aria2c avoids GitHub throttling), e.g.:
$($lines -join "`n")
Then re-run, or pass -ZipDir to point at the folder that holds them.
"@
  }
  return [pscustomobject]@{ Bin = $bin; Cudart = $cudart; BinName = $binName }
}

# Register a directory as an Unsloth Studio custom model-scan folder
# (persisted in studio.db -- the same thing the UI's "Add folder" does).
function Register-ScanFolder {
  param(
    [Parameter(Mandatory)] [string] $StudioHome,
    [Parameter(Mandatory)] [string] $Path
  )
  if (-not (Test-Path $Path)) { throw "Scan folder does not exist: $Path" }
  $py  = Resolve-StudioPython $StudioHome
  $pkg = Get-StudioPackageDir $StudioHome
  $env:UNSLOTH_STUDIO_HOME = $StudioHome
  $code = @"
import sys
sys.path.insert(0, r"$pkg\backend")
from storage.studio_db import add_scan_folder, list_scan_folders
add_scan_folder(r"$Path")
print("scan folders:", [f["path"] for f in list_scan_folders()])
"@
  $code | & $py -
  if ($LASTEXITCODE -ne 0) { throw "Failed to register scan folder $Path" }
}

# (Re)install the ~\.local\bin\unsloth.cmd launcher that pins UNSLOTH_STUDIO_HOME.
function Install-UnslothWrapper {
  param([Parameter(Mandatory)] [string] $StudioHome)
  $binDir = Join-Path $env:USERPROFILE ".local\bin"
  New-Item -ItemType Directory -Force -Path $binDir | Out-Null
  $cmd = @"
@echo off
rem unsloth launcher wrapper (generated by win-models scripts/unsloth-setup.ps1)
rem Sets UNSLOTH_STUDIO_HOME and forwards all args to the real unsloth CLI.
if not defined UNSLOTH_STUDIO_HOME set "UNSLOTH_STUDIO_HOME=$StudioHome"
"%UNSLOTH_STUDIO_HOME%\unsloth_studio\Scripts\unsloth.exe" %*
exit /b %ERRORLEVEL%
"@ -replace "`n", "`r`n"
  Set-Content -LiteralPath (Join-Path $binDir "unsloth.cmd") -Value $cmd -Encoding ASCII
  Write-Host "  wrote $binDir\unsloth.cmd (ensure $binDir is on PATH)"
}
