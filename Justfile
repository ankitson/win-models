set shell := ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command"]

mod plain 'src/plain-llama'
mod unsloth 'src/unsloth'
mod lmstudio 'src/lmstudio'
mod parakeet 'src/parakeet'
mod comfy 'src/comfyui'
mod utils 'src/utils'
root := justfile_directory()

default:
    @just --list --list-submodules

serve:
    @powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{{ root }}\\scripts\\start-edge.ps1"

stop:
    @powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{{ root }}\\scripts\\stop-edge.ps1"

logs lines="120":
    @powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{{ root }}\\scripts\\tail-logs.ps1" -LogDir "{{ root }}\\logs" -Lines {{ lines }}
