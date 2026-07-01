set shell := ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command"]

mod plain 'src/plain-llama'
mod unsloth 'src/unsloth'
mod comfy 'src/comfyui'
mod utils 'src/utils'
root := justfile_directory()

default:
    @just --list --list-submodules

serve-edge:
    @powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{{ root }}\\scripts\\start-edge.ps1"

logs lines="120":
    @[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); $OutputEncoding = [Console]::OutputEncoding; $logDir = "{{ root }}\\logs"; $files = @("$logDir\\caddy.access.log", "$logDir\\caddy.err.log", "$logDir\\caddy.out.log", "$logDir\\edge-unsloth.err.log", "$logDir\\edge-unsloth.out.log", "$logDir\\comfyui.err.log", "$logDir\\comfyui.out.log"); foreach ($file in $files) { if (-not (Test-Path $file)) { New-Item -ItemType File -Path $file | Out-Null } }; Write-Host "Tailing logs from $logDir"; Get-Content -Encoding UTF8 -Tail {{ lines }} -Wait $files
