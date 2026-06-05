param(
  [int] $LlamaPort = 8080,
  [int] $LiteRtPort = 9379
)

Write-Host "Processes"
Get-Process llama-server,litert-lm -ErrorAction SilentlyContinue | Select-Object Id,ProcessName,StartTime,CPU | Format-Table -AutoSize

Write-Host "Ports"
Get-NetTCPConnection -LocalPort $LlamaPort,$LiteRtPort -ErrorAction SilentlyContinue | Select-Object LocalAddress,LocalPort,State,OwningProcess | Format-Table -AutoSize

Write-Host "GPU"
nvidia-smi --query-gpu=name,memory.total,memory.used,utilization.gpu --format=csv,noheader

Write-Host "LAN IPs"
Get-NetIPAddress -AddressFamily IPv4 |
  Where-Object { $_.IPAddress -notlike "127.*" -and $_.PrefixOrigin -ne "WellKnown" } |
  Select-Object IPAddress,InterfaceAlias |
  Format-Table -AutoSize

