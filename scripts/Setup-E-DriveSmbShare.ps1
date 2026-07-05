[CmdletBinding()]
param(
    [string]$ShareName = "E",
    [string]$Path = "E:\",
    [string]$Account = ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name),
    [string[]]$PrivateInterfaceAliases = @("Ethernet", "Wi-Fi", "Tailscale"),
    [string]$FirewallRuleName = "SMB Inbound (LAN and Tailscale)"
)

$ErrorActionPreference = "Stop"

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script from an elevated PowerShell session."
}

if (-not (Test-Path -LiteralPath $Path)) {
    throw "Share path '$Path' does not exist."
}

$server = Get-Service -Name LanmanServer
if ($server.Status -ne "Running") {
    Start-Service -Name LanmanServer
}
Set-Service -Name LanmanServer -StartupType Automatic

$profiles = Get-NetConnectionProfile | Where-Object { $_.InterfaceAlias -in $PrivateInterfaceAliases }
foreach ($profile in $profiles) {
    if ($profile.NetworkCategory -ne "Private") {
        Set-NetConnectionProfile -InterfaceIndex $profile.InterfaceIndex -NetworkCategory Private
    }
}

$existingShare = Get-SmbShare -Name $ShareName -ErrorAction SilentlyContinue
if ($existingShare) {
    if ($existingShare.Path -ne $Path) {
        throw "SMB share '$ShareName' already exists for '$($existingShare.Path)'. Choose a different share name."
    }
} else {
    New-SmbShare -Name $ShareName -Path $Path -FullAccess $Account -FolderEnumerationMode AccessBased | Out-Null
}

$access = Get-SmbShareAccess -Name $ShareName | Where-Object { $_.AccountName -eq $Account -and $_.AccessControlType -eq "Allow" }
if (-not ($access | Where-Object { $_.AccessRight -eq "Full" })) {
    Grant-SmbShareAccess -Name $ShareName -AccountName $Account -AccessRight Full -Force | Out-Null
}

# exFAT has no NTFS ACLs, so access control for this drive is enforced by SMB share permissions.
$oldRule = Get-NetFirewallRule -DisplayName $FirewallRuleName -ErrorAction SilentlyContinue
if ($oldRule) {
    Remove-NetFirewallRule -DisplayName $FirewallRuleName
}

New-NetFirewallRule `
    -DisplayName $FirewallRuleName `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort 445 `
    -Profile Private `
    -RemoteAddress LocalSubnet,100.64.0.0/10 `
    -Description "Allow SMB to this machine from the private LAN and Tailscale clients only." | Out-Null

Set-SmbServerConfiguration -EnableSMB1Protocol $false -Force | Out-Null

$ips = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.InterfaceAlias -in $PrivateInterfaceAliases -and $_.IPAddress -notlike "169.254*" } |
    Select-Object InterfaceAlias, IPAddress, PrefixLength

[pscustomobject]@{
    ComputerName = $env:COMPUTERNAME
    SharePath = "\\$env:COMPUTERNAME\$ShareName"
    Account = $Account
    FirewallRule = $FirewallRuleName
    Addresses = $ips
} | Format-List
