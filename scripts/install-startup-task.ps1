param(
  [ValidateSet("google-qat12", "ggml-12b-q4km", "unsloth-26b-q3km")] [string] $Variant = "google-qat12",
  [string] $ModelRoot = "E:\root\models",
  [string] $BindHost = "0.0.0.0",
  [int] $Port = 8080,
  [ValidateSet("on", "off", "auto")] [string] $Reasoning = "on",
  [int] $ContextSize = 8192,
  [string] $GpuLayers = "all",
  [int] $CacheRam = 8192,
  [switch] $RunNow
)

$ErrorActionPreference = "Stop"

$taskName = "Local Gemma Server"
$shortcutName = "Local Gemma Server.lnk"
$startupDir = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startupDir $shortcutName
$repoRoot = Split-Path -Parent $PSScriptRoot
$serveScript = Join-Path $PSScriptRoot "serve-llama.ps1"
$argument = @(
  "-NoProfile",
  "-ExecutionPolicy", "Bypass",
  "-File", "`"$serveScript`"",
  "-Variant", $Variant,
  "-ModelRoot", "`"$ModelRoot`"",
  "-BindHost", $BindHost,
  "-Port", "$Port",
  "-Reasoning", $Reasoning,
  "-ContextSize", "$ContextSize",
  "-GpuLayers", "$GpuLayers",
  "-CacheRam", "$CacheRam"
) -join " "

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argument -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
  -MultipleInstances IgnoreNew `
  -RestartCount 3 `
  -RestartInterval (New-TimeSpan -Minutes 1)

try {
  Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Force | Out-Null

  Write-Host "Installed startup task: $taskName"
  Write-Host "Command: powershell.exe $argument"
} catch {
  Write-Warning "Could not register Scheduled Task ($($_.Exception.Message)). Falling back to Startup folder shortcut."
  $shell = New-Object -ComObject WScript.Shell
  $shortcut = $shell.CreateShortcut($shortcutPath)
  $shortcut.TargetPath = "powershell.exe"
  $shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$serveScript`" -Variant $Variant -ModelRoot `"$ModelRoot`" -BindHost $BindHost -Port $Port -Reasoning $Reasoning -ContextSize $ContextSize -GpuLayers $GpuLayers -CacheRam $CacheRam"
  $shortcut.WorkingDirectory = $repoRoot
  $shortcut.WindowStyle = 7
  $shortcut.Description = "Start local Gemma llama.cpp server"
  $shortcut.Save()
  Write-Host "Installed Startup folder shortcut: $shortcutPath"
}

if ($RunNow) {
  & $serveScript -Variant $Variant -ModelRoot $ModelRoot -BindHost $BindHost -Port $Port -Reasoning $Reasoning -ContextSize $ContextSize -GpuLayers $GpuLayers -CacheRam $CacheRam
}
