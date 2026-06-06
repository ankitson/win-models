param(
  [int] $Port = 8080,
  [string] $DisplayName = "Win Models llama.cpp 8080"
)

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
  Write-Host "This script must run from an elevated PowerShell window to modify Windows Firewall."
  Write-Host "Run this as Administrator:"
  Write-Host "  just utils firewall-allow $Port"
  exit 1
}

$existing = Get-NetFirewallRule -DisplayName $DisplayName -ErrorAction SilentlyContinue
if ($existing) {
  Set-NetFirewallRule -DisplayName $DisplayName -Enabled True -Direction Inbound -Action Allow
  Set-NetFirewallPortFilter -AssociatedNetFirewallRule $existing -Protocol TCP -LocalPort $Port
  Write-Host "Updated firewall rule: $DisplayName TCP $Port"
  exit 0
}

New-NetFirewallRule `
  -DisplayName $DisplayName `
  -Direction Inbound `
  -Action Allow `
  -Protocol TCP `
  -LocalPort $Port `
  -Profile Private `
  -Description "Allow LAN access to local llama.cpp OpenAI-compatible model server." | Out-Null

Write-Host "Created firewall rule: $DisplayName TCP $Port"

