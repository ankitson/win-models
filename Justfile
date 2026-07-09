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

serve model_key="":
    @uv --project "{{ root }}" run win-models edge start --model-key {{ model_key }}

# Omit model_key to use models-config.json default
serve-default:
    @uv --project "{{ root }}" run win-models edge start

# Stop heavy services (llama-server, Unsloth, LM Studio, ASR, ComfyUI)
stop:
    @uv --project "{{ root }}" run win-models edge stop

# Stop everything including Caddy
stop-all:
    @uv --project "{{ root }}" run win-models edge stop --all

# Stop heavy services + also stop Caddy
stop-caddy:
    @uv --project "{{ root }}" run win-models edge stop --caddy

# OpenCode headless server (opencode serve) fronted by opencode.win.ankitson.com
opencode-serve:
    @powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{{ root }}\\scripts\\start-opencode.ps1"

opencode-stop:
    @powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{{ root }}\\scripts\\stop-opencode.ps1"

opencode-logs lines="120":
    @powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Get-Content -LiteralPath '{{ root }}\\logs\\opencode.out.log' -Tail {{ lines }}; Write-Output '--- err ---'; Get-Content -LiteralPath '{{ root }}\\logs\\opencode.err.log' -Tail {{ lines }}"

logs lines="120":
    @powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{{ root }}\\scripts\\tail-logs.ps1" -LogDir "{{ root }}\\logs" -Lines {{ lines }}
