# VRAM process manager for desktop-win
# Usage: manage-vram.ps1 [stop|start]
param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("stop", "start")]
    [string]$Action
)

# Processes known to consume significant VRAM via GPU-accelerated rendering
# Grouped by category so "start" can restore selectively
$VRAM_APPS = @{
    # Browsers - biggest VRAM consumers (GPU compositing, WebGL, video decode)
    browsers = @(
        @{Name="chrome";       StartPath="$env:ProgramFiles\Google\Chrome\Application\chrome.exe"}
        @{Name="firefox";      StartPath="$env:ProgramFiles\Firefox Developer Edition\firefox.exe"}
    )
    # Chat/comm apps with GPU-accelerated UIs
    chat = @(
        @{Name="discord";      StartPath="$env:LOCALAPPDATA\Discord\Update.exe"; Args="--processStart Discord.exe"}
        @{Name="signal";       StartPath="$env:LOCALAPPDATA\Programs\signal-desktop\Signal.exe"}
        @{Name="whatsapp";     StartPath=""}  # Windows Store app, harder to restart
    )
    # Dev tools
    devtools = @(
        @{Name="obsidian";     StartPath="$env:LOCALAPPDATA\Programs\Obsidian\Obsidian.exe"}
    )
    # Game launchers
    gaming = @(
        @{Name="steam";         StartPath=""}  # Steam has complex startup
        @{Name="epicgameslauncher"; StartPath=""}
        @{Name="galaxyclient";  StartPath=""}
        @{Name="playnite";      StartPath=""}
    )
    # Desktop overlays / utilities
    utilities = @(
        @{Name="dropbox";       StartPath="$env:ProgramFiles\Dropbox\Client\Dropbox.exe"}
        @{Name="onecommander";  StartPath="$env:ProgramFiles\OneCommander\OneCommander.exe"}
        @{Name="handy";         StartPath="$env:LOCALAPPDATA\Handy\handy.exe"}
        @{Name="surfshark";     StartPath="$env:ProgramFiles\Surfshark\Surfshark.exe"}
    )
}

$TOTAL_CLOSED = 0
$TOTAL_STARTED = 0

function Stop-App {
    param($ProcessName, $FriendlyName)
    $procs = Get-Process -Name $ProcessName -ErrorAction SilentlyContinue
    if (-not $procs) {
        Write-Host "  [SKIP] $FriendlyName not running" -ForegroundColor DarkGray
        return
    }
    $count = $procs.Count
    $ws = [math]::Round(($procs | Measure-Object WorkingSet64 -Sum).Sum / 1MB, 0)
    try {
        $procs | Stop-Process -Force -ErrorAction Stop
        Write-Host "  [STOP] $FriendlyName ($count instances, ~$ws MB RAM)" -ForegroundColor Yellow
    } catch {
        Write-Host "  [FAIL] $FriendlyName - $_" -ForegroundColor Red
    }
}

function Start-App {
    param($ProcessName, $FriendlyName, $Path, $Args)
    $existing = Get-Process -Name $ProcessName -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "  [SKIP] $FriendlyName already running" -ForegroundColor DarkGray
        return
    }
    if (-not $Path -or -not (Test-Path $Path)) {
        Write-Host "  [SKIP] $FriendlyName - no start path configured" -ForegroundColor DarkGray
        return
    }
    try {
        if ($Args) {
            Start-Process -FilePath $Path -ArgumentList $Args
        } else {
            Start-Process -FilePath $Path
        }
        Write-Host "  [START] $FriendlyName" -ForegroundColor Green
    } catch {
        Write-Host "  [FAIL] $FriendlyName - $_" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "=== VRAM Process Manager: $Action ===" -ForegroundColor Cyan
Write-Host ""

foreach ($group in $VRAM_APPS.Keys) {
    Write-Host "[$group]" -ForegroundColor Magenta
    foreach ($app in $VRAM_APPS[$group]) {
        if ($Action -eq "stop") {
            Stop-App -ProcessName $app.Name -FriendlyName $app.Name
        } else {
            Start-App -ProcessName $app.Name -FriendlyName $app.Name -Path $app.StartPath -Args $app.Args
        }
    }
    Write-Host ""
}

if ($Action -eq "stop") {
    # Also handle Edge WebView processes that aren't critical
    $webviews = Get-Process -Name "msedgewebview2" -ErrorAction SilentlyContinue
    if ($webviews) {
        $ws = [math]::Round(($webviews | Measure-Object WorkingSet64 -Sum).Sum / 1MB, 0)
        Write-Host "[system]" -ForegroundColor Magenta
        Write-Host "  [INFO] msedgewebview2: $($webviews.Count) instances, ~$ws MB RAM (left running for system apps)" -ForegroundColor DarkGray
    }

    Write-Host "=== Done. GPU VRAM should be freed now. ===" -ForegroundColor Cyan
    Write-Host "To restore: manage-vram.ps1 start" -ForegroundColor Cyan
} else {
    Write-Host "=== Done. Apps restarted. ===" -ForegroundColor Cyan
}
