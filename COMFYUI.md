# ComfyUI

This repo manages a manual ComfyUI install beside the local LLM tooling. The
commands live in `src/comfyui/Justfile` and call the Python package through `uv`.

Defaults:

- ComfyUI checkout: `E:\root\projects\comfyui` (`COMFYUI_HOME` can override it).
- Shared model root: `E:\root\projects\models\comfyui`
  (`COMFYUI_MODEL_ROOT` can override it).
- Port: `8188` (`COMFYUI_PORT` can override it).
- Python runtime: `3.13` (`COMFYUI_PYTHON` can override it).
- PyTorch mode: `nvidia` (`COMFYUI_TORCH` can be `nvidia`, `cpu`, or `skip`).
- PyTorch CUDA wheel index: `https://download.pytorch.org/whl/cu130`
  (`COMFYUI_TORCH_INDEX_URL` can override it).

## Commands

```powershell
just comfy setup          # clone ComfyUI, create .venv, install torch + requirements
just comfy update         # git pull and refresh dependencies
just comfy serve          # foreground server on 127.0.0.1:8188, opens browser
just comfy serve-no-open  # foreground server without browser auto-open
just comfy serve-lan      # bind 0.0.0.0 for LAN access
just comfy status         # show checkout/venv/port state
just comfy stop           # stop the process listening on COMFYUI_PORT
```

The setup path follows ComfyUI's manual install flow: clone the upstream repo,
use an isolated virtual environment, install PyTorch, install
`requirements.txt`, and launch with `python main.py`.

## Model Paths

`just comfy setup` and `just comfy serve` write:

```text
E:\root\projects\comfyui\extra_model_paths.yaml
```

The generated config points ComfyUI at `COMFYUI_MODEL_ROOT` and creates common
model directories there:

```text
checkpoints
clip
clip_vision
configs
controlnet
diffusion_models
embeddings
loras
style_models
text_encoders
unet
upscale_models
vae
```

Drop diffusion assets into those folders instead of `ComfyUI\models` so updates
or reinstall work does not mix application code with large model files.

## Runtime Directories

By default, ComfyUI uses directories inside `COMFYUI_HOME`:

- `input`
- `output`
- `temp`
- `user`

The CLI also accepts `--input-dir`, `--output-dir`, `--temp-dir`, and
`--user-dir` if a run needs separate locations.

## Notes

- `just comfy serve` runs in the foreground. Use Ctrl+C for normal shutdown.
- `just comfy stop` only kills the process that owns `COMFYUI_PORT`; it does not
  blindly kill every Python process.
- Use `COMFYUI_TORCH=skip` when the venv already has the desired PyTorch build.
