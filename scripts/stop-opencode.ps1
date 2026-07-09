param([string]$Port = "4096")

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Stops the OpenCode headless server started by start-opencode.ps1.
# Matches the opencode serve process bound to the configured port.

$RepoRoot = Split-Path -Parent $PSScriptRoot
$CfgSecret = Join-Path $RepoRoot ".env.secret"
. (Join-Path $PSScriptRoot "Import-DotEnvSecret.ps1")
Import-WinModelsDotEnvSecret -Path $CfgSecret

$killed = 0
# Match by command line so we only kill the opencode serve we started.
$targets = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'opencode[^\n]*serve' -and $_.CommandLine -match "--port\s+$Port" }
foreach ($t in $targets) {
    try {
        Stop-Process -Id $t.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Output ("Stopped OpenCode PID {0}" -f $t.ProcessId)
        $killed++
    }
    catch {
        Write-Output ("Could not stop PID {0}: {1}" -f $t.ProcessId, $_.Exception.Message)
    }
}

# Fallback: anything still listening on the port.
$listeners = @(Get-NetTCPConnection -State Listen -LocalPort ([int]$Port) -ErrorAction SilentlyContinue)
foreach ($l in $listeners) {
    try {
        Stop-Process -Id $l.OwningProcess -Force -ErrorAction SilentlyContinue
        Write-Output ("Stopped listener PID {0} on port {1}" -f $l.OwningProcess, $Port)
        $killed++
    }
    catch {
    }
}

if ($killed -eq 0) {
    Write-Output ("No OpenCode server found listening on port {0}." -f $Port)
}
else {
    Write-Output ("Stopped {0} OpenCode process(es)." -f $killed)
}
