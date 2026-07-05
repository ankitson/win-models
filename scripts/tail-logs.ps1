param(
    [Parameter(Mandatory = $true)]
    [string]$LogDir,
    [string]$UnslothHome = $(if ($env:UNSLOTH_STUDIO_HOME) { $env:UNSLOTH_STUDIO_HOME } else { "E:\root\projects\unsloth" }),
    [int]$Lines = 120,
    [int]$PollMs = 350
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [Console]::OutputEncoding

$fileNames = @(
    # "caddy.access.log", too noisy
    "caddy.err.log",
    "caddy.out.log",
    "edge-unsloth.err.log",
    "edge-unsloth.out.log",
    "lmstudio.err.log",
    "lmstudio.out.log",
    "parakeet-asr.err.log",
    "parakeet-asr.out.log",
    "comfyui.err.log",
    "comfyui.out.log"
)

$palette = @(
    "Cyan",
    "DarkCyan",
    "Yellow",
    "DarkYellow",
    "Green",
    "DarkGreen",
    "Red",
    "Magenta",
    "Blue"
)

function Write-TaggedLine {
    param(
        [string]$Label,
        [string]$Color,
        [string]$Line
    )

    Write-Host ("[{0}] {1}" -f $Label, $Line) -ForegroundColor $Color
}

$states = @{}
$dynamicLogs = @(
    [pscustomobject]@{
        Name      = "llama-server"
        Directory = Join-Path $UnslothHome "logs\llama-server"
        Filter    = "llama-*-port-*.log"
        Color     = "White"
        Path      = $null
    }
)

function Add-LogState {
    param(
        [string]$Path,
        [string]$Label,
        [string]$Color,
        [switch]$Create
    )

    if ($states.ContainsKey($Path)) {
        return
    }

    if (-not (Test-Path $Path)) {
        if (-not $Create) {
            return
        }
        New-Item -ItemType File -Path $Path | Out-Null
    }

    $tailLines = @(Get-Content -Path $Path -Encoding UTF8 -Tail $Lines -ErrorAction SilentlyContinue)
    foreach ($line in $tailLines) {
        Write-TaggedLine -Label $Label -Color $Color -Line $line
    }

    $states[$Path] = [pscustomobject]@{
        Label     = $Label
        Color     = $Color
        Offset    = (Get-Item $Path).Length
        Remainder = ""
    }
}

function Sync-DynamicLogs {
    foreach ($spec in $dynamicLogs) {
        if (-not (Test-Path $spec.Directory)) {
            continue
        }

        $latest = Get-ChildItem -Path $spec.Directory -Filter $spec.Filter -File -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if (-not $latest) {
            continue
        }

        if ($spec.Path -and $spec.Path -ne $latest.FullName -and $states.ContainsKey($spec.Path)) {
            $states.Remove($spec.Path)
        }

        $spec.Path = $latest.FullName
        Add-LogState -Path $latest.FullName -Label $spec.Name -Color $spec.Color
    }
}

for ($i = 0; $i -lt $fileNames.Count; $i++) {
    $path = Join-Path $LogDir $fileNames[$i]
    $label = [System.IO.Path]::GetFileName($path)
    $color = $palette[$i % $palette.Count]
    Add-LogState -Path $path -Label $label -Color $color -Create
}

Sync-DynamicLogs
Write-Host ("Tailing logs from {0}" -f $LogDir) -ForegroundColor White
Write-Host ("Tailing latest llama-server log from {0}" -f (Join-Path $UnslothHome "logs\llama-server")) -ForegroundColor White

while ($true) {
    Sync-DynamicLogs
    foreach ($path in @($states.Keys)) {
        $state = $states[$path]
        $item = Get-Item $path -ErrorAction SilentlyContinue
        if (-not $item) {
            continue
        }

        if ($item.Length -lt $state.Offset) {
            $state.Offset = 0
            $state.Remainder = ""
        }

        if ($item.Length -le $state.Offset) {
            continue
        }

        $stream = [System.IO.File]::Open($path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        try {
            $null = $stream.Seek($state.Offset, [System.IO.SeekOrigin]::Begin)
            $reader = [System.IO.StreamReader]::new($stream, [System.Text.UTF8Encoding]::UTF8, $true, 4096, $true)
            try {
                $chunk = $reader.ReadToEnd()
            }
            finally {
                $reader.Dispose()
            }
            $state.Offset = $stream.Position
        }
        finally {
            $stream.Dispose()
        }

        if (-not $chunk) {
            continue
        }

        $text = $state.Remainder + $chunk
        $endsWithNewline = $text.EndsWith("`n") -or $text.EndsWith("`r")
        $parts = $text -split "`r?`n", -1

        if ($endsWithNewline) {
            $state.Remainder = ""
            $linesToWrite = if ($parts.Length -gt 1) { $parts[0..($parts.Length - 2)] } else { @() }
        }
        else {
            $state.Remainder = $parts[-1]
            $linesToWrite = if ($parts.Length -gt 1) { $parts[0..($parts.Length - 2)] } else { @() }
        }

        foreach ($line in $linesToWrite) {
            Write-TaggedLine -Label $state.Label -Color $state.Color -Line $line
        }
    }

    Start-Sleep -Milliseconds $PollMs
}
