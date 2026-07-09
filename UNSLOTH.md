# Unsloth Studio

This repo keeps the Unsloth path separate from the direct llama.cpp path. The
commands live in `src/unsloth/Justfile` and call the Python package through `uv`.

## Layout

- Studio install root: `E:\root\projects\unsloth`
  (`UNSLOTH_STUDIO_HOME` can override it).
- Model root: `E:\root\projects\models`
  (`WIN_MODELS_MODEL_ROOT` can override it).
- Default model: `unsloth/gemma-4-26B-A4B-it-GGUF:UD-Q8_K_XL`
  (`UNSLOTH_DEFAULT_MODEL` can override it).
- Context length: `262144` (`UNSLOTH_CONTEXT_LENGTH` can override it).
- KV cache type: `q8_0` (`UNSLOTH_CACHE_TYPE_KV` can override it).
- Chat template override: `src/unsloth/chat-templates/gemma-4-31b-it-pr118.jinja`
  (`UNSLOTH_CHAT_TEMPLATE_FILE` can override it; set it to empty to disable).
- Hugging Face cache for Studio downloads: `WIN_MODELS_MODEL_ROOT`
  (`UNSLOTH_HF_CACHE` can override it).
- Backend output streams in the terminal that launched Studio.
- Studio's internal `llama-server` still writes per-model logs under
  `E:\root\projects\unsloth\logs\llama-server`.

Unsloth Studio itself must already be installed via Unsloth's official installer.
These helpers do not bootstrap the Unsloth venv; they make the local setup
repeatable after that venv exists.

## Commands

```powershell
just unsloth setup            # patch installer, install llama.cpp, register models, write wrapper
just unsloth sync-mcp         # sync declarative MCP servers into Studio
just unsloth patch-web        # patch and rebuild Studio web UI integrations
just unsloth serve            # foreground server on 127.0.0.1:8888, opens browser
just unsloth serve-lan        # bind 0.0.0.0 for LAN access
just unsloth serve-debug      # LOG_LEVEL=DEBUG, foreground output
just unsloth stop             # cleanup a stale/separately-launched Studio server
just unsloth register-models <path>  # register a separate non-cache custom folder
```

`serve` uses `unsloth studio run --model ...`, which is the Unsloth command form
that supports preloading the model configured by `UNSLOTH_DEFAULT_MODEL`.
The serve recipes also pass llama-server overrides for context length and KV
cache dtype so Studio does not fall back to `4096` context or `f16` KV cache.
For Gemma 4 instruction-tuned GGUFs, they also pass a repo-pinned
`chat_template_override` through Studio's load API so Studio's reasoning/tool
capability detection sees the same template that llama-server renders.
When `UNSLOTH_DEFAULT_MODEL` points at an embedding model such as
`Qwen/Qwen3-Embedding-8B-GGUF:Q4_K_M`, the wrapper automatically switches the
internal llama-server into embedding mode with `--embedding --pooling last`.
If an OpenAI-compatible API request names a different model in `model`, the
wrapper now loads that model on demand before serving the request. Requests
that send `model: "default"` or `model: "auto"` fall back to
`UNSLOTH_DEFAULT_MODEL`.
The first request for an uncached model blocks until Studio finishes
downloading and loading it, so client-side timeouts may need to be higher on
the first hit.

The launcher patches Studio's CLI to reuse a single default `cli` API key from
`E:\root\projects\unsloth\auth\cli-api-key.txt`. Set
`UNSLOTH_REUSE_CLI_API_KEY=0` to restore Studio's upstream behavior of creating
a fresh `cli` key on every `unsloth studio run`.

`setup` and `patch-web` also add a microphone recorder to Studio's web UI. For
models with native audio input, the mic button records browser audio, attaches it
to the current chat as an audio message, and sends it through Studio's existing
`input_audio` path. For text-only models, Studio can use `UNSLOTH_ASR_FALLBACK_URL`
to transcribe the recording with a local ASR sidecar and inject the transcript
into the user turn. The chat settings sidebar includes a `Voice Recording Limit`
slider. It defaults to `120` seconds for ASR fallback, clamps to `5`-`600`
seconds, caps Gemma native-audio models at `30` seconds, plays a warning beep 5
seconds before the limit, and auto-stops at the configured limit. Run
`just unsloth patch-web` after a Studio update to reapply the integration and
rebuild the frontend bundle; use `just unsloth patch-web --no-build` only when
you intentionally want to patch source without rebuilding.

`E:\root\projects\models` is used as the Hugging Face cache root. It should not
also be registered as a custom scan folder, because Studio lists HF cache repos
as downloaded models already and intentionally keeps custom-folder entries even
when they duplicate cached models. `just unsloth setup` therefore does not
register the model root by default; use `just unsloth register-models <path>`
only for a separate folder that is not the HF cache.

## Embeddings

Use the existing serve flow by overriding `UNSLOTH_DEFAULT_MODEL`:

```powershell
$env:UNSLOTH_DEFAULT_MODEL = "Qwen/Qwen3-Embedding-8B-GGUF:Q4_K_M"
just unsloth serve
```

Studio prints the API key on startup and also stores it in:

```text
E:\root\projects\unsloth\auth\cli-api-key.txt
```

Minimal embedding request:

```powershell
$token = (Get-Content E:\root\projects\unsloth\auth\cli-api-key.txt).Trim()
curl.exe http://127.0.0.1:8888/v1/embeddings `
  -H "Authorization: Bearer $token" `
  -H "Content-Type: application/json" `
  -d "{\"model\":\"Qwen/Qwen3-Embedding-8B-GGUF\",\"input\":[\"hello world\"]}"
```

## Gemma 4 Chat Template

The default template override is pinned from:

```text
https://huggingface.co/google/gemma-4-31B-it/raw/refs%2Fpr%2F118/chat_template.jinja
```

That PR adds `preserve_thinking`, OpenAI-compatible `image_url` and
`input_audio` content type handling, and fixes tool-call formatting edge cases.
This is a runtime override for Studio/llama-server; it does not rewrite GGUF
metadata. When updated GGUFs are republished with the fixed embedded template,
set `UNSLOTH_CHAT_TEMPLATE_FILE=` to disable the override.

## MCP Tools

MCP servers are declared in `src/unsloth/mcp-servers.json` and synced into
Studio's `studio.db` with:

```powershell
just unsloth sync-mcp
```

The default serve recipes run that sync before launching Studio. The checked-in
manifest registers:

- `https://mcp.dev.ankitson.com/mcp`
- `https://mcp.dev.ankitson.com/mcp/code`

Both use `op://clankers/mcpproxy-agents/password` as a bearer token. The secret
reference is stored in the manifest; the resolved token is written only to
Studio's local MCP server table. If needed, `MCPPROXY_AGENTS_TOKEN` overrides the
1Password lookup. Repo-local `.env.secret` is loaded automatically by the
`win-models unsloth ...` wrapper; `MCPPROXY_AGENT_TOKEN` is accepted as a
singular alias.

In Studio chat, enable the MCP composer control for local tool-capable models.
For API calls, pass `mcp_enabled: true` with tool-enabled requests.

## Stopping

Studio runs in the foreground. Startup failures, backend logs, and Ctrl+C all
happen in the same terminal. `just unsloth stop` is only a cleanup escape hatch
for a stale process left from an older launch or a separate manual launch.

## Local Zip Workaround

Studio installs a prebuilt `llama.cpp` by downloading two large CUDA zips from
GitHub releases. GitHub often throttles those downloads, so setup patches the
venv's `install_llama_prebuilt.py` with a local-zip shim:

1. Before a release download, reuse an already-downloaded asset of the same name
   from `UNSLOTH_LLAMA_LOCAL_DIR` or `~\Downloads`.
2. Keep the official sha256 and post-install smoke test.
3. Install the pinned `b9878`, cuda-`13.3` build from those zips.

Expected files in `~\Downloads`, unless `just unsloth setup <zip-dir>` points
elsewhere:

```text
llama-b9878-bin-win-cuda-13.3-x64.zip
cudart-llama-bin-win-cuda-13.3-x64.zip
```

Sources:

```text
https://github.com/ggml-org/llama.cpp/releases/download/b9878/llama-b9878-bin-win-cuda-13.3-x64.zip
https://github.com/ggml-org/llama.cpp/releases/download/b9878/cudart-llama-bin-win-cuda-13.3-x64.zip
```

This machine is an RTX 5080. The cuda-12.4 prebuilt predates Blackwell SASS, so
Studio uses cuda-13.3.

## Models

Studio lists Hugging Face cache repos under Downloaded Models. From the default
cache root, that surfaces:

- `gemma-4-12b-it-qat-q4_0`
- `google-gemma-4-26b-a4b-q4km`
- `google-gemma-4-26b-a4b-q8_0`
- `ggml-org-gemma-4-12b-it-q4km`
- `unsloth-gemma-4-26b-a4b-it-ud-q6kxl`
- `unsloth-gemma-4-26b-a4b-it-ud-q8kxl`
- `unsloth-gemma-4-26b-a4b-it-q8_0`

Legacy plain folders under `E:\root\projects\models` are for direct llama.cpp or
LiteRT recipes only. They are not registered with Studio, so they should not
appear under Custom Folders unless you explicitly register that root again.
LiteRT `.litertlm` files are not GGUF models, so Studio correctly ignores them.

## Download Location

Studio uses Hugging Face cache layout for repository downloads. With the repo
recipes, `HUGGINGFACE_HUB_CACHE` is set to `UNSLOTH_HF_CACHE`, which defaults to
`WIN_MODELS_MODEL_ROOT`. A download like:

```text
unsloth/gemma-4-26B-A4B-it-GGUF:UD-Q8_K_XL
```

lands under:

```text
E:\root\projects\models\models--unsloth--gemma-4-26B-A4B-it-GGUF
```

Previously, without that env var, it used the normal user HF cache:

```text
C:\Users\ankit\.cache\huggingface\hub
```

## Logging

`LOG_LEVEL` controls backend verbosity. `just unsloth serve-debug` sets
`LOG_LEVEL=DEBUG` and streams logs in the terminal. Request middleware logs
method/path/status/timing, not full request or response bodies.

Studio tees internal `llama-server` output to:

```text
E:\root\projects\unsloth\logs\llama-server\llama-<ts>-port-<port>.log
```

Conversation content is stored in `studio.db` (`chat_messages` table).

## Gotchas

- Open `http://localhost:8888` or `http://127.0.0.1:8888` on this machine for
  browser secure-context features such as mic input.
- Plain `http://<LAN-IP>` disables browser-gated mic features.
- The mic button appears when the selected Studio model advertises native audio
  input or when an ASR fallback URL is configured.
- Studio runs its own `llama-server` internally. Do not run it alongside the
  direct llama.cpp server against the same GPU unless you mean to.
