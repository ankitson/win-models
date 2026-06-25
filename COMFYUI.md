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
just comfy serve          # foreground server on 127.0.0.1:8188
just comfy serve-tailscale # bind only the current Tailscale IPv4
just comfy status         # show checkout/venv/port state
just comfy stop           # stop the process listening on COMFYUI_PORT
just comfy logs           # tail running ComfyUI logs
just comfy prompt-logs    # tail compact formatted prompt JSONL
just comfy st-workflow <workflow> # generate SillyTavern API workflow JSON
```

The setup path follows ComfyUI's manual install flow: clone the upstream repo,
use an isolated virtual environment, install PyTorch, install
`requirements.txt`, and launch with `python main.py`.

Assets are enabled by default. ComfyUI scans `models`, `input`, and `output`
into its local asset index so generated files can appear in asset-backed UI
views. Set `COMFYUI_ASSETS=0` or pass `--no-assets` to the underlying
`win-models comfy serve` command to disable this.

## Workflow Templates

The repo ships generic ComfyUI workflow templates through a small custom-node
package:

```text
src\comfyui\custom_nodes\win_models_templates\workflows\
```

These templates are installed into ComfyUI by `setup`, `update`, and `serve`.
After ComfyUI restarts, they are available through ComfyUI's workflow template
browser under the `win_models_templates` node pack.

Included templates:

```text
win_models_text2img.json
win_models_img2img.json
win_models_inpaint.json
win_models_flux2_klein_9b_single_image_edit_lora.json
win_models_flux2_klein_9b_single_image_edit_lora_main.json
win_models_flux2_klein_switch_single_image_edit_lora_main.json
win_models_flux2_klein_switch_text2img_lora_main.json
```

They are generic UI workflows with:

- `CheckpointLoaderSimple`
- `MultiLoRAStack`
- `StringReplace` positive and negative prompt templates
- fixed text-to-image, img2img, or inpaint latent paths
- no model-specific checkpoint, embedding, trigger word, URL, or local path

These templates are for checkpoint-style SD/SDXL/Pony workflows. Split-loader
families such as Flux.2 Klein need their own workflow using separate diffusion
model, text encoder, and VAE loader nodes; selecting a Flux.2 Klein file in
`CheckpointLoaderSimple` can fail later with an invalid VAE.

The Flux.2 Klein 9B single-image edit template follows the official ComfyUI
tutorial workflow shape, trims it to one input image plus edit text, and adds a
`MultiLoRAStack` between the Flux model/text encoder loaders and the sampler
conditioning path.

The `_lora_main` variant keeps the Flux diffusion loader, text encoder loader,
and `MultiLoRAStack` on the main canvas so LoRA rows are easier to inspect and
edit. The subgraph only contains the image-edit core.

The `switch` variant uses a local `Flux.2 Klein Preset Loader` node. Its preset
selects the matching diffusion model, text encoder, and VAE for 9B base or 4B
distilled. The override fields remain editable for exact filename experiments.
The switch image-edit and text-to-image templates expose both prompt and
negative prompt fields.

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
- Prompt logging is enabled by default through a repo-managed custom node. Raw
  `/prompt` payloads are appended to `logs\comfyui.prompts.jsonl`; disable it
  with `COMFYUI_LOG_PROMPTS=0` or `win-models comfy serve --no-log-prompts`.
- `just comfy prompt-logs` parses the JSONL prompt log and prints compact JSON:
  one header line per request and one line per ComfyUI node, omitting bulky
  embedded UI workflow metadata.
- `just comfy stop` only kills the process that owns `COMFYUI_PORT`; it does not
  blindly kill every Python process.
- Use `COMFYUI_TORCH=skip` when the venv already has the desired PyTorch build.

## SillyTavern

Generated SillyTavern API workflows are written outside the tracked source tree:

```text
outputs\<workflow-name>.json
```

Generated workflows can hardcode model-specific fixed prompt nodes inside
ComfyUI. Keep the SillyTavern common prompt prefix/suffix minimal so those
tokens are not added twice.

SillyTavern only detects placeholders when they are exact JSON string values,
for example `"replace": "%prompt%"`. Do not place `%prompt%` inside a larger
prompt string such as `"fixed tags, %prompt%, trigger"`. Current workflows use a
`StringReplace` template: fixed prompt text contains an internal marker, and the
standalone `replace` value is what SillyTavern fills.

### Workflow Generator

Generate SillyTavern API-format JSON into `outputs\` from either a ComfyUI UI
workflow or an already-exported API workflow:

```powershell
just comfy st-workflow E:\root\projects\comfyui\user\default\workflows\example.json example_api
```

For simple placeholders that replace the entire node input:

```powershell
uv --project . run win-models comfy st-workflow workflow.json --placeholder 6.text=prompt --placeholder 7.text=negative_prompt
```

Use `--set NODE.INPUT=VALUE` for exact values. Use
`--placeholder NODE.INPUT=NAME` when the whole input should become `%NAME%`.

`--append-placeholder NODE.INPUT=NAME` and
`--insert-placeholder-before NODE.INPUT=NAME::MARKER` are generic helpers for
systems that allow placeholders inside larger text strings. SillyTavern's
ComfyUI workflow editor does not reliably detect that form; for SillyTavern,
use a standalone placeholder value in a `StringReplace` node.
