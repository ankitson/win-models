# Win Models

Small Windows/PowerShell setup for trying local model variants on this machine. With a few different execution engines


## Quick Start

Uses the **Unsloth Studio** API Server + Web UI (chat front-end over the same models):

```powershell
just studio-setup          # reproducible install (one-time / after a Studio update)
just studio-serve-debug    # foreground server on 0.0.0.0:8888, tools enabled, defaults to gemma4-12b

# can also serve via plain llama.cpp
just status
just serve-google-qat12    # via llama
just bench gemma-4-12b-qat
```

See [UNSLOTH.md](UNSLOTH.md) for details, the local-zip workaround, and gotchas.
See [BENCHMARKS.md](BENCHMARKS.md) for the local benchmark notes and current recommendation.

## Useful Endpoints

- llama.cpp server: `http://localhost:8080/v1`
- LiteRT-LM server: `http://localhost:9379/v1`
- unsloth server: `http://localhost:8888/v1`

## Variants

- `google-qat12`: official Google Gemma 4 12B IT QAT Q4_0 GGUF with multimodal projector.
- `ggml-12b-q4km`: ggml-org Gemma 4 12B IT Q4_K_M GGUF with Q8_0 projector.
- `litert-e4b`: LiteRT-LM Gemma 4 E4B IT `.litertlm`.
- `unsloth-26b-q3km`: Unsloth Gemma 4 26B-A4B IT UD-Q3_K_M GGUF.

## Notes

Reasoning is enabled for llama.cpp recipes. Use a larger `max_tokens` budget for clients,
because reasoning tokens count against the output budget.
