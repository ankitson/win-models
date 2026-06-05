# Local Gemma Lab

Small Windows/PowerShell setup for trying Gemma 4 model variants on this machine.

Large files live outside the repo under `E:\root\models`.

## Quick Start

```powershell
just status
just serve-google-qat12
just bench gemma-4-12b-qat
```

## Useful Endpoints

- llama.cpp server: `http://localhost:8080/v1`
- LiteRT-LM server: `http://localhost:9379/v1`

For LAN clients, replace `localhost` with this machine's LAN IP.

## Variants

- `google-qat12`: official Google Gemma 4 12B IT QAT Q4_0 GGUF with multimodal projector.
- `ggml-12b-q4km`: ggml-org Gemma 4 12B IT Q4_K_M GGUF with Q8_0 projector.
- `litert-e4b`: LiteRT-LM Gemma 4 E4B IT `.litertlm`.
- `unsloth-26b-q3km`: Unsloth Gemma 4 26B-A4B IT UD-Q3_K_M GGUF.

## Notes

Reasoning is enabled for llama.cpp recipes. Use a larger `max_tokens` budget for clients,
because reasoning tokens count against the output budget.

