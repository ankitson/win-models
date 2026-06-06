# Unsloth Studio

This repo keeps the Unsloth path separate from the direct llama.cpp path. The
commands live in `src/unsloth/Justfile` and call the Python package through `uv`.

## Layout

- Studio install root: `E:\root\projects\unsloth`
  (`UNSLOTH_STUDIO_HOME` can override it).
- Model root: `E:\root\projects\models`
  (`WIN_MODELS_MODEL_ROOT` can override it).
- Backend output streams in the terminal that launched Studio.
- Studio's internal `llama-server` still writes per-model logs under
  `E:\root\projects\unsloth\logs\llama-server`.

Unsloth Studio itself must already be installed via Unsloth's official installer.
These helpers do not bootstrap the Unsloth venv; they make the local setup
repeatable after that venv exists.

## Commands

```powershell
just unsloth setup            # patch installer, install llama.cpp, register models, write wrapper
just unsloth serve            # foreground server on 127.0.0.1:8888, opens browser
just unsloth serve-lan        # bind 0.0.0.0 for LAN access
just unsloth serve-debug      # LOG_LEVEL=DEBUG, foreground output
just unsloth stop             # cleanup a stale/separately-launched Studio server
just unsloth register-models  # register E:\root\projects\models as a scan folder
```

`serve` uses `unsloth studio run --model ...`, which is the Unsloth command form
that supports preloading the default model from `WIN_MODELS_MODEL_ROOT`.

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
3. Install the pinned `b9536`, cuda-`13.3` build from those zips.

Expected files in `~\Downloads`, unless `just unsloth setup <zip-dir>` points
elsewhere:

```text
llama-b9536-bin-win-cuda-13.3-x64.zip
cudart-llama-bin-win-cuda-13.3-x64.zip
```

Sources:

```text
https://github.com/ggml-org/llama.cpp/releases/download/b9536/llama-b9536-bin-win-cuda-13.3-x64.zip
https://github.com/ggml-org/llama.cpp/releases/download/b9536/cudart-llama-bin-win-cuda-13.3-x64.zip
```

This machine is an RTX 5080. The cuda-12.4 prebuilt predates Blackwell SASS, so
Studio uses cuda-13.3.

## Models

Studio lists GGUF and HF safetensors/bin folders under registered scan folders.
From the default model root, that surfaces:

- `gemma-4-12b-it-qat-q4_0`
- `ggml-org-gemma-4-12b-it-q4km`
- `unsloth-gemma-4-26b-a4b-it-ud-q3km`

LiteRT `.litertlm` files are not GGUF models, so Studio correctly ignores them.

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
- The mic feature is browser speech-to-text; it does not send audio to the model.
- Studio runs its own `llama-server` internally. Do not run it alongside the
  direct llama.cpp server against the same GPU unless you mean to.
