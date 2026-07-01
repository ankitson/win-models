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
    @powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{{ root }}\\scripts\\tail-logs.ps1" -LogDir "{{ root }}\\logs" -Lines {{ lines }}
