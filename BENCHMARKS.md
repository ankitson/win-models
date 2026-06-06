# Benchmark Results

These are quick local smoke benchmarks from this Windows machine:

- GPU: NVIDIA GeForce RTX 5080, 16 GB VRAM
- Runtime for GGUF models: llama.cpp `b9536` CUDA build
- llama.cpp settings: `ctx-size=8192`, `parallel=1`, `flash-attn=on`, `reasoning=on`
- Benchmark prompt: one short reasoning-friendly sentence prompt
- Benchmark command shape: `just plain bench <model>`, usually `runs=2`, `max_tokens=512`

These numbers are useful for rough local comparison, not a rigorous eval. With reasoning enabled, some models spent the full output budget on reasoning and produced no final `content`; those runs need a larger `max_tokens` or a reasoning budget before comparing answer quality.

## Summary

| Variant | Runtime | Model id | Multimodal | Reasoning field | VRAM observed | Result |
| --- | --- | --- | --- | --- | ---: | --- |
| Google Gemma 4 12B IT QAT Q4_0 GGUF | llama.cpp | `gemma-4-12b-qat` | Yes | Yes | ~10.8-13.8 GB loaded | Best default: fast, finishes final answer |
| ggml-org Gemma 4 12B IT Q4_K_M GGUF | llama.cpp | `gemma-4-12b-ggml-q4km` | Yes | Yes | ~14.0 GB loaded | Similar speed, but used budget in reasoning |
| LiteRT Gemma 4 E4B IT | LiteRT-LM | `gemma-4-E4B-it,gpu` | Runtime-dependent | No OpenAI reasoning field | ~6 GB before load; not fully tracked | Fast warm tiny prompt, smaller model |
| Unsloth Gemma 4 26B-A4B IT UD-Q3_K_M GGUF | llama.cpp | `gemma-4-26b-a4b-unsloth-q3km` | Yes | Yes | ~15.3 GB loaded | Fits, but slow and very tight on VRAM |

## Tiny Benchmark

| Variant | Run | Elapsed | Prompt tok/s | Gen tok/s | Completion tokens | Finish | Final content | Reasoning chars |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |
| Google QAT 12B | 1 | 5722.9 ms | 107.92 | 74.30 | 396 | stop | 188 chars | 1659 |
| Google QAT 12B | 2 | 5385.8 ms | 505.93 | 74.57 | 396 | stop | 188 chars | 1659 |
| ggml-org 12B Q4_K_M | 1 | 7648.0 ms | 178.09 | 69.48 | 512 | length | 0 chars | 384 |
| ggml-org 12B Q4_K_M | 2 | 7560.6 ms | 466.89 | 68.63 | 512 | length | 0 chars | 269 |
| LiteRT E4B | 1 | 13476.4 ms | N/A | N/A | N/A | stop | 280 chars | 0 |
| LiteRT E4B | 2 | 749.3 ms | N/A | N/A | N/A | stop | 280 chars | 0 |
| Unsloth 26B-A4B Q3_K_M | 1 | 65861.4 ms | 1.22 | 15.96 | 512 | length | 0 chars | N/A |
| Unsloth 26B-A4B Q3_K_M | 2 | 29048.6 ms | 130.11 | 17.80 | 512 | length | 0 chars | N/A |

## Earlier Checks

- Google QAT 12B text test with reasoning enabled returned both `message.reasoning_content` and final `message.content`.
- Google QAT 12B image test worked through the OpenAI-compatible API and answered `A red square.`
- ggml-org 12B loaded its Q8_0 multimodal projector successfully.
- Unsloth 26B-A4B loaded its F16 multimodal projector successfully, but left little VRAM headroom.
- Docker's bundled llama.cpp build could not load the new Gemma 4 projector (`unknown projector type: gemma4uv`), so the repo uses llama.cpp `b9536`.

## Current Recommendation

Use `google-qat12` as the default local coding model:

```powershell
just plain serve-google-qat12
```

For lower VRAM pressure:

```powershell
just plain serve-google-qat12-lowvram
```

For future benchmark passes, use larger output budgets with reasoning enabled:

```powershell
just plain bench gemma-4-12b-qat http://127.0.0.1:8080/v1 2 2048
```
