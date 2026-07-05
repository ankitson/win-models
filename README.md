# Win Models

Small Windows setup for running local model servers through direct
`llama.cpp`/LiteRT commands, Unsloth Studio, or ComfyUI.

## Layout

- `src/plain-llama`: direct local model servers and downloads, backed by Python.
- `src/unsloth`: Unsloth Studio setup, model registration, serve, and cleanup.
- `src/comfyui`: ComfyUI setup, update, serve, and cleanup.
- `src/utils`: Windows-specific utilities such as firewall and VRAM monitoring.
- `src/win_models`: the Python package used by the just recipes.

The root `Justfile` is only a module index. List everything from the repo root:

```powershell
just
```

## Quick Start

Unsloth Studio API server + web UI:

```powershell
just unsloth setup
just unsloth serve-debug
```

Direct llama.cpp server:

```powershell
just plain serve-google-qat12
just plain bench gemma-4-12b-qat
```

ComfyUI:

```powershell
just comfy setup
just comfy serve
just comfy serve-tailscale
just comfy logs
```

Download the new Gemma 26B variants into the Hugging Face cache layout used by
Unsloth Studio:

```powershell
just plain download-new26-cache
```

Utilities:

```powershell
just utils status
just utils monitor
just utils firewall-allow 8080
```

Declarative Unsloth MCP sync:

```powershell
just unsloth sync-mcp
```

Patch/rebuild Unsloth Studio web integrations, including browser microphone
input for native-audio models and ASR-backed voice input for text-only models:

```powershell
just unsloth patch-web
```

See [UNSLOTH.md](UNSLOTH.md) for Studio details, [COMFYUI.md](COMFYUI.md) for
ComfyUI details, and [BENCHMARKS.md](BENCHMARKS.md) for local benchmark notes.

## Useful Endpoints

- llama.cpp server: `http://localhost:8080/v1`
- LiteRT-LM server: `http://localhost:9379/v1`
- Unsloth Studio server: `http://localhost:8888/v1`
- ComfyUI server: `http://localhost:8188`

## Variants

- `google-qat12`: official Google Gemma 4 12B IT QAT Q4_0 GGUF with multimodal projector.
- `ggml-12b-q4km`: ggml-org Gemma 4 12B IT Q4_K_M GGUF with Q8_0 projector.
- `google-26b-a4b-q4km`: Google Gemma 4 26B-A4B base Q4_K_M GGUF.
- `google-26b-a4b-q8`: Google Gemma 4 26B-A4B base Q8_0 GGUF.
- `litert-e4b`: LiteRT-LM Gemma 4 E4B IT `.litertlm`.
- `unsloth-26b-q3km`: Unsloth Gemma 4 26B-A4B IT UD-Q3_K_M GGUF.
- `unsloth-26b-q6kxl`: Unsloth Gemma 4 26B-A4B IT UD-Q6_K_XL GGUF.
- `unsloth-26b-q8kxl`: Unsloth Gemma 4 26B-A4B IT UD-Q8_K_XL GGUF.
- `unsloth-26b-q8`: Unsloth Gemma 4 26B-A4B IT Q8_0 GGUF.

Model groups:

- `new-gemma-26b`: `unsloth-26b-q6kxl`, `unsloth-26b-q8kxl`, `unsloth-26b-q8`, `google-26b-a4b-q4km`, and `google-26b-a4b-q8`.

## Configuration

Defaults can be overridden with environment variables:

- Repo-local `.env.secret` is loaded by `just serve`, `just stop`, and the
  `win-models unsloth ...` wrapper. It is ignored by git and uses simple
  `NAME=value` lines.
- `WIN_MODELS_MODEL_ROOT`, default `E:\root\projects\models`
- `UNSLOTH_STUDIO_HOME`, default `E:\root\projects\unsloth`
- `COMFYUI_HOME`, default `E:\root\projects\comfyui`
- `COMFYUI_MODEL_ROOT`, default `WIN_MODELS_MODEL_ROOT\comfyui`
- `COMFYUI_PYTHON`, default `3.13`
- `COMFYUI_TORCH`, default `nvidia`; can be `nvidia`, `cpu`, or `skip`.
- `COMFYUI_TORCH_INDEX_URL`, default `https://download.pytorch.org/whl/cu130`
- `UNSLOTH_DEFAULT_MODEL`, default `unsloth/gemma-4-26B-A4B-it-GGUF:UD-Q8_K_XL`
- `UNSLOTH_CONTEXT_LENGTH`, default `262144`; passed as `unsloth studio run --max-seq-length` and llama-server `-c`.
- `UNSLOTH_CACHE_TYPE_KV`, default `q8_0`; passed as llama-server `--cache-type-k` and `--cache-type-v`.
- `UNSLOTH_CHAT_TEMPLATE_FILE`, default `src\unsloth\chat-templates\gemma-4-31b-it-pr118.jinja`; passed as Studio `chat_template_override`.
- `UNSLOTH_HF_CACHE`, default `WIN_MODELS_MODEL_ROOT`; controls where Studio/HF downloads are cached.
- `UNSLOTH_REUSE_CLI_API_KEY`, default `1`; reuses `UNSLOTH_STUDIO_HOME\auth\cli-api-key.txt` instead of creating a new `cli` API key on each Studio startup.
- `WIN_MODELS_HF_DOWNLOAD_PYTHON`, default `3.13`; Python runtime used for Hugging Face cache downloads.
- `WIN_MODELS_HF_TOKEN_FILE`, default `logs\hf-token.tmp`; temporary local token file used by `download-cache` when `HF_TOKEN` is unset.
- `WIN_MODELS_HF_TOKEN_OP_REF`, default `op://clankers/huggingface-read/password`; used by `download-cache` when `HF_TOKEN` is unset.
- `MCPPROXY_AGENTS_TOKEN`, optional override for the 1Password MCP bearer token.
- `CLOUDFLARE_API_TOKEN`, optional override for the Caddy DNS challenge used by
  root `just serve`; if unset, `scripts\start-edge.ps1` falls back to 1Password.
- `WIN_MODELS_HOST`, default `0.0.0.0`
- `WIN_MODELS_LLAMA_PORT`, default `8080`
- `WIN_MODELS_LITERT_PORT`, default `9379`
- `WIN_MODELS_STUDIO_PORT`, default `8888`
- `COMFYUI_HOST`, default `127.0.0.1`
- `COMFYUI_PORT`, default `8188`

ComfyUI serve recipes start in the background and write process output to
`logs\comfyui.out.log` and `logs\comfyui.err.log`; use `just comfy logs` to
follow them and `just comfy stop` to stop the server. Other server recipes run
in the foreground so logs and failures are visible in the terminal.
