# Unsloth Studio (web UI)

Reproducible install/run of the Unsloth Studio server on this machine, alongside
the raw `llama-server` recipes. Studio is a web chat UI that drives its own
bundled `llama.cpp`; these scripts make the otherwise-fiddly bits repeatable.

## Layout

- Studio install root (`studio_home`): `E:\root\projects\unsloth`
  (the venv lives at `…\unsloth_studio`). Override with `-StudioHome`.
- Models (`model_root`): `E:\root\projects\models` — same folder the raw
  `serve-*` recipes use; registered with Studio so the web UI lists them.
- Server logs: `logs/studio-<port>.out.log` / `.err.log` (gitignored).

## Prerequisite

Unsloth Studio itself must already be installed (its venv at
`<studio_home>\unsloth_studio`) via Unsloth's official installer — see
<https://unsloth.ai>. These scripts do **not** bootstrap that; they finish and
make-reproducible everything after it.

## Commands

```powershell
just studio-setup            # patch + install llama.cpp + finish setup + register models + wrapper
just studio-serve            # background server on 127.0.0.1:8888, logs to logs/, opens browser
just studio-serve-lan        # same but bind 0.0.0.0 (LAN) — see secure-context note below
just studio-stop             # stop the background server cleanly
just studio-register-models  # (re)register E:\root\projects\models as a scan folder
```

### Stopping the server

`studio-serve` runs the server **detached**, so Ctrl+C on the launcher can't reach
it — use `just studio-stop`. (Studio's own `unsloth studio stop` is broken on
Windows: it crashes in `os.kill(pid, 0)` with WinError 87 before terminating
anything.) `studio-stop` finds the server by listening port + `studio.pid`, kills
the whole process tree so the spawned child `llama-server` doesn't orphan, and
clears the stale pid file. Windows can't deliver a graceful signal to a
hidden-console process, so it's a force-stop — safe here, since Studio state lives
in sqlite.

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
