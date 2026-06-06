$taskName = "Local Gemma Server"
$shortcutPath = Join-Path ([Environment]::GetFolderPath("Startup")) "Local Gemma Server.lnk"
$removed = $false
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
  Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
  Write-Host "Removed startup task: $taskName"
  $removed = $true
}

if (Test-Path $shortcutPath) {
  Remove-Item -LiteralPath $shortcutPath -Force
  Write-Host "Removed Startup folder shortcut: $shortcutPath"
  $removed = $true
}

if (-not $removed) {
  Write-Host "No startup task or shortcut was installed."
}
