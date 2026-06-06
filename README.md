# Win Models

Small Windows setup for running local model servers through either direct
`llama.cpp`/LiteRT commands or Unsloth Studio.

## Layout

- `src/plain-llama`: direct local model servers and downloads, backed by Python.
- `src/unsloth`: Unsloth Studio setup, model registration, serve, and cleanup.
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

Utilities:

```powershell
just utils status
just utils monitor
just utils firewall-allow 8080
```

See [UNSLOTH.md](UNSLOTH.md) for Studio details and [BENCHMARKS.md](BENCHMARKS.md)
for local benchmark notes.

## Useful Endpoints

- llama.cpp server: `http://localhost:8080/v1`
- LiteRT-LM server: `http://localhost:9379/v1`
- Unsloth Studio server: `http://localhost:8888/v1`

## Variants

- `google-qat12`: official Google Gemma 4 12B IT QAT Q4_0 GGUF with multimodal projector.
- `ggml-12b-q4km`: ggml-org Gemma 4 12B IT Q4_K_M GGUF with Q8_0 projector.
- `litert-e4b`: LiteRT-LM Gemma 4 E4B IT `.litertlm`.
- `unsloth-26b-q3km`: Unsloth Gemma 4 26B-A4B IT UD-Q3_K_M GGUF.

## Configuration

Defaults can be overridden with environment variables:

- `WIN_MODELS_MODEL_ROOT`, default `E:\root\projects\models`
- `UNSLOTH_STUDIO_HOME`, default `E:\root\projects\unsloth`
- `WIN_MODELS_HOST`, default `0.0.0.0`
- `WIN_MODELS_LLAMA_PORT`, default `8080`
- `WIN_MODELS_LITERT_PORT`, default `9379`
- `WIN_MODELS_STUDIO_PORT`, default `8888`

Server recipes run in the foreground so logs and failures are visible in the
terminal. Use Ctrl+C to stop normal foreground servers.
