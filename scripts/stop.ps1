Get-Process llama-server,litert-lm -ErrorAction SilentlyContinue | Stop-Process -Force
Write-Host "Stopped llama-server and litert-lm processes."

