param(
    [Parameter(Mandatory = $true)]
    [string]$LogDir,
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

for ($i = 0; $i -lt $fileNames.Count; $i++) {
    $path = Join-Path $LogDir $fileNames[$i]
    if (-not (Test-Path $path)) {
        New-Item -ItemType File -Path $path | Out-Null
    }

    $label = [System.IO.Path]::GetFileName($path)
    $color = $palette[$i % $palette.Count]
    $tailLines = @(Get-Content -Path $path -Encoding UTF8 -Tail $Lines -ErrorAction SilentlyContinue)
    foreach ($line in $tailLines) {
        Write-TaggedLine -Label $label -Color $color -Line $line
    }

    $states[$path] = [pscustomobject]@{
        Label     = $label
        Color     = $color
        Offset    = (Get-Item $path).Length
        Remainder = ""
    }
}

Write-Host ("Tailing logs from {0}" -f $LogDir) -ForegroundColor White

while ($true) {
    foreach ($path in $states.Keys) {
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
