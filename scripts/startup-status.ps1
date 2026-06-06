$taskName = "Win Models Server"
$shortcutPath = Join-Path ([Environment]::GetFolderPath("Startup")) "Win Models Server.lnk"
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
  $info = Get-ScheduledTaskInfo -TaskName $taskName
  Write-Host "Scheduled Task:"
  $task | Select-Object TaskName,State | Format-List
  $info | Select-Object LastRunTime,LastTaskResult,NextRunTime,NumberOfMissedRuns | Format-List
  $task.Actions | Select-Object Execute,Arguments,WorkingDirectory | Format-List
}

if (Test-Path $shortcutPath) {
  Write-Host "Startup folder shortcut:"
  $shell = New-Object -ComObject WScript.Shell
  $shortcut = $shell.CreateShortcut($shortcutPath)
  [PSCustomObject]@{
    Path = $shortcutPath
    TargetPath = $shortcut.TargetPath
    Arguments = $shortcut.Arguments
    WorkingDirectory = $shortcut.WorkingDirectory
  } | Format-List
}

if (-not $task -and -not (Test-Path $shortcutPath)) {
  Write-Host "Startup task/shortcut is not installed: $taskName"
}
