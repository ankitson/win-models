param(
  [int] $IntervalSeconds = 2,
  [int] $Top = 18
)

function Get-ProcessNameForId {
  param([int] $ProcessId)
  $p = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
  if ($p) { return $p.ProcessName }
  return "<protected>"
}

while ($true) {
  Clear-Host
  $now = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  Write-Host "VRAM monitor - $now - refresh ${IntervalSeconds}s - Ctrl+C to stop"
  Write-Host ""

  nvidia-smi --query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu,pstate,power.draw --format=csv,noheader
  Write-Host ""

  $samples = (Get-Counter "\GPU Process Memory(*)\Dedicated Usage").CounterSamples |
    Where-Object { $_.CookedValue -gt 0 }

  $rows = foreach ($sample in $samples) {
    if ($sample.InstanceName -match "pid_(\d+)") {
      $processId = [int] $matches[1]
      [PSCustomObject]@{
        MiB = [math]::Round($sample.CookedValue / 1MB, 1)
        PID = $processId
        Process = Get-ProcessNameForId $processId
      }
    }
  }

  $rows |
    Group-Object PID,Process |
    ForEach-Object {
      $first = $_.Group[0]
      [PSCustomObject]@{
        MiB = [math]::Round(($_.Group | Measure-Object MiB -Sum).Sum, 1)
        PID = $first.PID
        Process = $first.Process
      }
    } |
    Sort-Object MiB -Descending |
    Select-Object -First $Top |
    Format-Table -AutoSize

  Start-Sleep -Seconds $IntervalSeconds
}

