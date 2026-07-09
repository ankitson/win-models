param(
    [string]$Port = "4096",
    [string]$Hostname = "127.0.0.1"
)

# Starts the OpenCode headless server and writes its output to the repo logs dir.
# OpenCode executes shell commands, so the server is protected with HTTP basic
# auth via OPENCODE_SERVER_PASSWORD (loaded from .env.secret). Caddy (see the
# opencode.win.ankitson.com route in the Caddyfile) reverse-proxies to it and
# forwards the browser's Authorization header.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $RepoRoot "logs"
$CfgSecret = Join-Path $RepoRoot ".env.secret"

. (Join-Path $PSScriptRoot "Import-DotEnvSecret.ps1")
Import-WinModelsDotEnvSecret -Path $CfgSecret

function Resolve-OpencodeLaunch {
    param([array]$Arguments)
    # Prefer the real .exe (npm global root package bin).
    try {
        $globalRoot = (& npm root -g 2>$null)
        if ($globalRoot) {
            $candidate = Join-Path $globalRoot "opencode-ai\bin\opencode.exe"
            if (Test-Path -LiteralPath $candidate) {
                return @{ FilePath = $candidate; Args = $Arguments }
            }
        }
    }
    catch {
    }
    # Fall back to whatever `opencode` resolves to on PATH.
    $cmd = Get-Command opencode -ErrorAction SilentlyContinue
    if ($cmd) {
        if ($cmd.Source -match '\.exe$') {
            return @{ FilePath = $cmd.Source; Args = $Arguments }
        }
        # .cmd / .ps1 wrappers must be hosted by their interpreter.
        if ($cmd.Source -match '\.cmd$') {
            return @{ FilePath = "cmd.exe"; Args = @("/c", $cmd.Source) + $Arguments }
        }
        if ($cmd.Source -match '\.ps1$') {
            return @{ FilePath = "powershell.exe"; Args = @("-NoProfile", "-File", $cmd.Source) + $Arguments }
        }
    }
    throw "opencode binary not found on PATH and not in npm global root. Install it with: npm install -g opencode-ai@latest"
}

$launch = Resolve-OpencodeLaunch -Arguments @("serve", "--port", $Port, "--hostname", $Hostname)
$OcExe = $launch.FilePath
$argsList = $launch.Args

# Ensure the password is present in THIS process so the child opencode inherits
# it. Import-DotEnvSecret sets Process env, but we also read it directly to be
# safe: opencode generates a RANDOM password when the var is absent, which would
# make the server unreachable with our known credentials (and still auth-gated).
if (-not $env:OPENCODE_SERVER_PASSWORD) {
    $secretLine = Get-Content -LiteralPath $CfgSecret -ErrorAction SilentlyContinue |
        Where-Object { $_ -match '^OPENCODE_SERVER_PASSWORD=' } | Select-Object -Last 1
    if ($secretLine) {
        $env:OPENCODE_SERVER_PASSWORD = ($secretLine -replace '^OPENCODE_SERVER_PASSWORD=').Trim()
    }
}
if (-not $env:OPENCODE_SERVER_USERNAME) {
    $env:OPENCODE_SERVER_USERNAME = "opencode"
}
if (-not $env:OPENCODE_SERVER_PASSWORD) {
    throw "OPENCODE_SERVER_PASSWORD is not set. Add it to $CfgSecret (or export it) so the server requires basic auth. Without it the server is open to the internet via Caddy."
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$OutLog = Join-Path $LogDir "opencode.out.log"
$ErrLog = Join-Path $LogDir "opencode.err.log"

# Fail fast if the port is already taken by something other than a prior opencode.
$existing = @(Get-NetTCPConnection -State Listen -LocalPort ([int]$Port) -ErrorAction SilentlyContinue)
if ($existing.Count -gt 0) {
    Write-Output ("Port {0} is already in use (PID {1}). Use the existing server or stop it first." -f $Port, ($existing[0].OwningProcess))
    exit 1
}

$argsList = @("serve", "--port", $Port, "--hostname", $Hostname, "--cors", "https://opencode.dev.ankitson.com")
$process = Start-Process -FilePath $OcExe -ArgumentList $argsList -WorkingDirectory $RepoRoot -WindowStyle Hidden -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog -PassThru

# Give it a moment, then confirm it actually bound the port.
Start-Sleep -Seconds 3
$listening = @(Get-NetTCPConnection -State Listen -LocalPort ([int]$Port) -ErrorAction SilentlyContinue)
if ($listening.Count -eq 0 -and -not $process.HasExited) {
    Write-Output ("OpenCode started (PID {0}) but is not listening on {1} yet. Check {2}." -f $process.Id, $Port, $ErrLog)
}
elseif ($process.HasExited) {
    Write-Output ("OpenCode exited early with code {0}. See {1}:" -f $process.ExitCode, $ErrLog)
    Get-Content $ErrLog | Select-Object -Last 20 | ForEach-Object { Write-Output $_ }
    exit 1
}

Write-Output ("Started OpenCode server PID {0} on http://{1}:{2} (basic auth required)." -f $process.Id, $Hostname, $Port)
Write-Output ("Logs: {0}, {1}" -f $OutLog, $ErrLog)
