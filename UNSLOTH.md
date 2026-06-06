# Unsloth Studio (web UI)

Reproducible install/run of the Unsloth Studio server on this machine, alongside
the raw `llama-server` recipes. Studio is a web chat UI that drives its own
bundled `llama.cpp`; these scripts make the otherwise-fiddly bits repeatable.

## Layout

- Studio install root (`studio_home`): `E:\root\projects\unsloth`
  (the venv lives at `…\unsloth_studio`). Override with `-StudioHome`.
- Models (`model_root`): `E:\root\projects\models` — same folder the raw
  `serve-*` recipes use; registered with Studio so the web UI lists them.
- Backend output streams in the terminal that launched Studio. Studio's internal
  `llama-server` still writes per-model logs under
  `E:\root\projects\unsloth\logs\llama-server`.

## Prerequisite

Unsloth Studio itself must already be installed (its venv at
`<studio_home>\unsloth_studio`) via Unsloth's official installer — see
<https://unsloth.ai>. These scripts do **not** bootstrap that; they finish and
make-reproducible everything after it.

## Commands

```powershell
just studio-setup            # patch + install llama.cpp + finish setup + register models + wrapper
just studio-serve            # foreground server on 127.0.0.1:8888, opens browser
just studio-serve-lan        # same but bind 0.0.0.0 (LAN) — see secure-context note below
just studio-stop             # clean up a stale/separately-launched server on the port
just studio-register-models  # (re)register E:\root\projects\models as a scan folder
```

### Stopping the server

`studio-serve` runs Studio in the foreground. Startup failures, backend logs, and
Ctrl+C all happen in the same terminal. `studio-stop` is only a cleanup escape
hatch for a stale process left from an older launch or a separate manual launch;
it finds the server by listening port + `studio.pid`, kills the process tree so
the spawned child `llama-server` doesn't orphan, and clears the stale pid file.

`studio-setup` is idempotent — safe to re-run. Re-run it after ever reinstalling
or updating Studio (the local-zip patch below lives inside the venv and is
re-applied on each run).

## Why the local-zip workaround exists

Studio installs a prebuilt `llama.cpp` by downloading two large CUDA zips from
GitHub releases. GitHub throttles those, so the install hangs/fails. The fix:

1. `scripts/unsloth-setup.ps1` patches the venv's `install_llama_prebuilt.py`
   with a **local-zip shim** (marker `UNSLOTH_LOCAL_ZIP_SHIM`): before any
   release download it reuses an already-downloaded asset of the same name from
   `UNSLOTH_LLAMA_LOCAL_DIR` (or `~\Downloads`). The official sha256 + post-install
   smoke test still run, so a bad local file is still caught.
2. It installs the build **pinned to `b9536`, cuda-`13.3`**, from those zips.

You must have these two files in `~\Downloads` (or pass `-ZipDir`):

```
llama-b9536-bin-win-cuda-13.3-x64.zip
cudart-llama-bin-win-cuda-13.3-x64.zip
```

Source (use a fast/resumable downloader to dodge the throttle):

```
https://github.com/ggml-org/llama.cpp/releases/download/b9536/llama-b9536-bin-win-cuda-13.3-x64.zip
https://github.com/ggml-org/llama.cpp/releases/download/b9536/cudart-llama-bin-win-cuda-13.3-x64.zip
```

### Why cuda-13.3 and not cuda-12

This box is an RTX 5080 (Blackwell, sm_120). The cuda-12.4 prebuilt predates
Blackwell SASS; the `…\models\llama-b9536-cuda12` folder is the **wrong** build
for Studio and is only kept for the raw `serve-*` recipes' historical reasons.
Studio's metadata records `runtime_line: cuda13` for the correct install.

## Launcher wrapper

`studio-setup` writes `~\.local\bin\unsloth.cmd`, which sets `UNSLOTH_STUDIO_HOME`
and forwards to the real CLI, so you can run `unsloth …` from anywhere
(ensure `~\.local\bin` is on PATH). Override the home per-call:
`set UNSLOTH_STUDIO_HOME=… & unsloth studio`.

## Models in the web UI

Only **GGUF** (and HF safetensors/bin) folders under a registered scan folder are
listed. From `model_root` that surfaces:

- `gemma-4-12b-it-qat-q4_0`, `ggml-org-gemma-4-12b-it-q4km`, `unsloth-gemma-4-26b-a4b-it-ud-q3km`

Not listed (by design): `litert-gemma-4-e4b-it` (`.litertlm` is LiteRT, not GGUF —
llama.cpp can't run it) and `llama-b9536-cuda12` (a binary build, not a model).

## Automatic inference settings

On load, Studio auto-applies recommended sampling params (temperature, top_p,
top_k, min_p) by **substring-matching a model-family token in the file path/name**
— it does NOT read the model name from inside the GGUF. Our gemma-4 files all
contain `gemma-4`, so they resolve to Gemma's recommended profile
(temp 1.0 / top_p 0.95 / top_k 64 / min_p 0.0). Resolution order: model-specific
YAML (HF-repo loads only) → family defaults (`inference_defaults.json`) → generic
`default.yaml` (temp 0.7 / top_k -1 / min_p 0.01).

Keep a recognized family token (`gemma-4`, `qwen3`, `llama-3.1`, ...) in the
folder/file name. Rename it to something unrecognized and it silently falls back
to the generic defaults. You can still override per-conversation in the UI.

## Detailed logging

Three layers, increasing detail:

1. **Backend logs** (what Studio is doing — model loads, routing, template
   application, tool calls). Controlled by `LOG_LEVEL` (default `INFO`). Run
   `just studio-serve-debug` (sets `LOG_LEVEL=DEBUG`) and watch the terminal
   output. The built-in request middleware logs method/path/status/timing only
   (`request_completed`); it does **not** log request/response bodies.

2. **llama-server logs** (the actual model traffic). Studio tees its internal
   `llama-server`'s stdout/stderr to
   `E:\root\projects\unsloth\logs\llama-server\llama-<ts>-port-<port>.log` (one
   per model load). At default verbosity this has slot/timing/token traces. To
   capture the **full templated prompt + sampling + generation**, load the model
   with the llama extra arg `--verbose`. Studio appends llama extra args
   last-wins; set them per model load in the UI's advanced load options, or use
   the single-model server:
   `unsloth studio run --model <path-or-repo> -H 127.0.0.1 -p 8888 --verbose`.
   (There is no global env to force `--verbose` on the multi-model UI.)

3. **Conversation content** (requests/responses as messages) is persisted in
   `studio.db` (`chat_messages` table) regardless of log level — query it
   directly for a durable record of prompts and replies.

`LOG_LEVEL=DEBUG` is chatty; use it while debugging, not for a long-running
server.

## Gotchas

- **Open via `http://localhost:8888`** (or `127.0.0.1`) on this machine. A plain
  `http://<LAN-IP>` is not a "secure context", so the browser disables the mic
  and other gated features. For LAN/remote use with mic you'd need HTTPS.
- **Mic = browser speech-to-text**, transcribed to *text* client-side (Chrome/Edge
  only; Firefox lacks the API). It does **not** send audio to the model.
- **No audio-to-model path** in the web UI at all: attachments accept images / PDF /
  text / docx / ODF, but there is no audio attachment adapter. Feeding raw audio to
  a multimodal model is an API-level workflow against an audio-projector model,
  not something this chat UI does.
- Studio runs its **own** `llama-server` internally (default UI port 8888). Don't
  expect it to share the raw `serve-*` recipe on 8080; don't run both against the
  GPU at once.
