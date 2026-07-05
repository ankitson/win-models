from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import zipfile
from pathlib import Path
from typing import Any

from .common import echo, ensure_dir, open_url, powershell, run
from .config import DEFAULT_LLAMA_PORT, DEFAULT_MODEL_ROOT, DEFAULT_STUDIO_HOME, DEFAULT_STUDIO_PORT


REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_ZIP_MARKER = "UNSLOTH_LOCAL_ZIP_SHIM"
CHAT_TEMPLATE_OVERRIDE_SHIM_MARKER = "UNSLOTH_CHAT_TEMPLATE_OVERRIDE_SHIM"
SPECULATIVE_TYPE_SHIM_MARKER = "UNSLOTH_SPECULATIVE_TYPE_SHIM"
CLI_API_KEY_REUSE_SHIM_MARKER = "UNSLOTH_CLI_API_KEY_REUSE_SHIM"
OPENAI_REASONING_PASSTHROUGH_SHIM_MARKER = "UNSLOTH_OPENAI_REASONING_PASSTHROUGH_SHIM"
EMBEDDING_EXTRA_ARGS_SHIM_MARKER = "UNSLOTH_EMBEDDING_EXTRA_ARGS_SHIM"
FIXED_LLAMA_PORT_SHIM_MARKER = "UNSLOTH_FIXED_LLAMA_PORT_SHIM"
OPENAI_REQUEST_AUTOLOAD_SHIM_MARKER = "UNSLOTH_OPENAI_REQUEST_AUTOLOAD_SHIM"
OPENAI_AUTOLOAD_SPECULATIVE_SHIM_MARKER = "UNSLOTH_OPENAI_AUTOLOAD_SPECULATIVE_SHIM"
WEB_UI_TOOL_POLICY_SHIM_MARKER = "UNSLOTH_WEB_UI_TOOL_POLICY_SHIM"
NATIVE_AUDIO_MIC_SHIM_MARKER = "UNSLOTH_NATIVE_AUDIO_MIC_SHIM"
ASSISTANT_PREFIX_CONTINUATION_SHIM_MARKER = "UNSLOTH_ASSISTANT_PREFIX_CONTINUATION_SHIM"
ASR_FALLBACK_SHIM_MARKER = "UNSLOTH_ASR_FALLBACK_SHIM"
TAILWIND_SAFE_SOURCE_SHIM_MARKER = "UNSLOTH_TAILWIND_SAFE_SOURCE_SHIM"
DEFAULT_MCP_CONFIG = (
    Path(__file__).resolve().parents[1] / "unsloth" / "mcp-servers.json"
)
PATCH_BASE_COMPLETE_MARKER = ".win-models-patch-base-complete"
PATCH_BASE_PACKAGE_PREFIXES = ("studio/", "unsloth_cli/")
PATCHED_SITE_PACKAGE_FILES = (
    "unsloth_cli/commands/studio.py",
    "studio/install_llama_prebuilt.py",
    "studio/backend/core/inference/llama_server_args.py",
    "studio/backend/core/inference/llama_cpp.py",
    "studio/backend/models/inference.py",
    "studio/backend/routes/inference.py",
    "studio/backend/routes/chat_history.py",
    "studio/frontend/src/index.css",
    "studio/frontend/src/features/auth/api.ts",
    "studio/frontend/src/features/chat/types/runtime.ts",
    "studio/frontend/src/features/chat/types/api.ts",
    "studio/frontend/src/features/chat/api/chat-api.ts",
    "studio/frontend/src/features/chat/api/chat-settings-api.ts",
    "studio/frontend/src/features/chat/api/chat-adapter.ts",
    "studio/frontend/src/features/chat/hooks/use-chat-model-runtime.ts",
    "studio/frontend/src/features/chat/lib/apply-inference-status-to-store.ts",
    "studio/frontend/src/features/chat/audio-attachment-adapter.ts",
    "studio/frontend/src/features/chat/utils/chat-settings-storage.ts",
    "studio/frontend/src/features/chat/stores/chat-runtime-store.ts",
    "studio/frontend/src/features/chat/presets/preset-policy.ts",
    "studio/frontend/src/features/chat/chat-settings-sheet.tsx",
    "studio/frontend/src/components/assistant-ui/thread.tsx",
    "studio/frontend/src/features/chat/shared-composer.tsx",
)
GENERATED_SITE_PACKAGE_FILES = (
    "studio/frontend/src/features/chat/native-audio-recorder.ts",
)


def load_dotenv_secret() -> None:
    paths = [Path.cwd() / ".env.secret", REPO_ROOT / ".env.secret"]
    seen: set[Path] = set()
    for path in paths:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in seen or not path.is_file():
            continue
        seen.add(resolved)
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            name, value = stripped.split("=", 1)
            name = name.strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                continue
            if os.environ.get(name):
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            os.environ[name] = value

    if os.environ.get("MCPPROXY_AGENT_TOKEN") and not os.environ.get(
        "MCPPROXY_AGENTS_TOKEN"
    ):
        os.environ["MCPPROXY_AGENTS_TOKEN"] = os.environ["MCPPROXY_AGENT_TOKEN"]


NATIVE_AUDIO_RECORDER_TS = """// SPDX-License-Identifier: AGPL-3.0-only
// UNSLOTH_NATIVE_AUDIO_MIC_SHIM: browser microphone capture for native audio-input models.

import { fileToBase64, MAX_AUDIO_SIZE } from "@/lib/audio-utils";
import { toast } from "@/lib/toast";
import { useCallback, useEffect, useRef, useState } from "react";

export type NativeAudioRecorderStatus = "idle" | "recording" | "processing";

const MIME_CANDIDATES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/ogg;codecs=opus",
  "audio/ogg",
  "audio/mp4",
];
const MIN_RECORDING_SECONDS = 5;
const MAX_RECORDING_SECONDS = 600;
const DEFAULT_RECORDING_SECONDS = 120;
const GEMMA_NATIVE_AUDIO_RECORDING_SECONDS = 30;
const WARNING_SECONDS = 5;

export const DEFAULT_VOICE_MESSAGE_PROMPT_TEXT = "";
export const AUDIO_ONLY_MESSAGE_PLACEHOLDER = "\u200b";

export function stripAudioOnlyMessagePlaceholder(text: string): string {
  return text.replaceAll(AUDIO_ONLY_MESSAGE_PLACEHOLDER, "");
}

export function isAudioOnlyMessagePlaceholderText(text?: string | null): boolean {
  const value = text ?? "";
  return (
    value.includes(AUDIO_ONLY_MESSAGE_PLACEHOLDER) &&
    stripAudioOnlyMessagePlaceholder(value).trim().length === 0
  );
}

function clampRecordingSeconds(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_RECORDING_SECONDS;
  return Math.min(
    MAX_RECORDING_SECONDS,
    Math.max(MIN_RECORDING_SECONDS, Math.round(value)),
  );
}

export function getEffectiveVoiceRecordingMaxSeconds(
  configuredSeconds: number,
  model?: { id?: string; name?: string; hasAudioInput?: boolean } | null,
): number {
  const configured = clampRecordingSeconds(configuredSeconds);
  const label = `${model?.id ?? ""} ${model?.name ?? ""}`.toLowerCase();
  if (model?.hasAudioInput && label.includes("gemma")) {
    return Math.min(configured, GEMMA_NATIVE_AUDIO_RECORDING_SECONDS);
  }
  return configured;
}

export function buildVoiceMessageText({
  existingText,
  transcript,
  promptText,
}: {
  existingText?: string | null;
  transcript?: string | null;
  promptText?: string | null;
}): string {
  const parts: string[] = [];
  const existing = (existingText ?? "").trim();
  const prompt = (promptText ?? "").trim();
  const voiceTranscript = (transcript ?? "").trim();
  if (existing) parts.push(existing);
  if (prompt && (!existing || !existing.includes(prompt))) parts.push(prompt);
  if (voiceTranscript) {
    parts.push(`Voice message transcript:\\n${voiceTranscript}`);
  }
  return parts.join("\\n\\n");
}

function pickMimeType(): string | undefined {
  if (typeof MediaRecorder === "undefined") {
    return undefined;
  }
  return MIME_CANDIDATES.find((type) => MediaRecorder.isTypeSupported(type));
}

function extensionForMimeType(mimeType: string): string {
  if (mimeType.includes("ogg")) return "ogg";
  if (mimeType.includes("mp4")) return "m4a";
  if (mimeType.includes("wav")) return "wav";
  return "webm";
}

function stopStream(stream: MediaStream | null): void {
  for (const track of stream?.getTracks() ?? []) {
    track.stop();
  }
}

function playWarningBeep(): void {
  try {
    const AudioContextCtor =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext?: typeof AudioContext })
        .webkitAudioContext;
    if (!AudioContextCtor) return;
    const ctx = new AudioContextCtor();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = 880;
    gain.gain.setValueAtTime(0.0001, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.18, ctx.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.28);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.3);
    window.setTimeout(() => void ctx.close(), 500);
  } catch {
    // Best-effort cue only.
  }
}

export function useNativeAudioRecorder(
  onAudioReady: (base64: string, name: string) => void | Promise<void>,
  maxSeconds: number,
): {
  status: NativeAudioRecorderStatus;
  supported: boolean;
  secondsElapsed: number;
  secondsLimit: number;
  start: () => Promise<void>;
  stop: () => void;
} {
  const [status, setStatus] = useState<NativeAudioRecorderStatus>("idle");
  const [secondsElapsed, setSecondsElapsed] = useState(0);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const onAudioReadyRef = useRef(onAudioReady);
  const timerRef = useRef<number | null>(null);
  const startedAtRef = useRef(0);
  const warningPlayedRef = useRef(false);
  const secondsLimit = clampRecordingSeconds(maxSeconds);

  useEffect(() => {
    onAudioReadyRef.current = onAudioReady;
  }, [onAudioReady]);

  const supported =
    typeof navigator !== "undefined" &&
    Boolean(navigator.mediaDevices?.getUserMedia) &&
    typeof MediaRecorder !== "undefined";

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const stop = useCallback(() => {
    const recorder = recorderRef.current;
    if (!recorder || recorder.state === "inactive") {
      return;
    }
    setStatus("processing");
    clearTimer();
    recorder.stop();
  }, [clearTimer]);

  const startTimer = useCallback(
    (limit: number) => {
      clearTimer();
      startedAtRef.current = Date.now();
      warningPlayedRef.current = false;
      setSecondsElapsed(0);
      timerRef.current = window.setInterval(() => {
        const elapsed = Math.floor((Date.now() - startedAtRef.current) / 1000);
        setSecondsElapsed(Math.min(elapsed, limit));
        const remaining = limit - elapsed;
        if (
          remaining <= WARNING_SECONDS &&
          remaining > 0 &&
          !warningPlayedRef.current
        ) {
          warningPlayedRef.current = true;
          playWarningBeep();
          toast.warning("Voice recording ending soon", {
            description: `${remaining} seconds remaining before auto-stop.`,
          });
        }
        if (elapsed >= limit) {
          stop();
        }
      }, 250);
    },
    [clearTimer, stop],
  );

  const start = useCallback(async () => {
    if (!supported) {
      toast.error("Microphone recording is not supported in this browser.");
      return;
    }
    if (status !== "idle") {
      return;
    }

    try {
      const limit = clampRecordingSeconds(maxSeconds);
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      const mimeType = pickMimeType();
      const recorder = new MediaRecorder(
        stream,
        mimeType ? { mimeType } : undefined,
      );
      chunksRef.current = [];
      streamRef.current = stream;
      recorderRef.current = recorder;

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          chunksRef.current.push(event.data);
        }
      };
      recorder.onerror = () => {
        clearTimer();
        stopStream(streamRef.current);
        streamRef.current = null;
        recorderRef.current = null;
        chunksRef.current = [];
        setSecondsElapsed(0);
        setStatus("idle");
        toast.error("Microphone recording failed.");
      };
      recorder.onstop = async () => {
        clearTimer();
        setStatus("processing");
        stopStream(streamRef.current);
        streamRef.current = null;
        recorderRef.current = null;

        const blob = new Blob(chunksRef.current, {
          type: recorder.mimeType || "audio/webm",
        });
        chunksRef.current = [];
        setSecondsElapsed(0);
        if (blob.size <= 0) {
          setStatus("idle");
          toast.error("No audio was recorded.");
          return;
        }
        if (blob.size > MAX_AUDIO_SIZE) {
          setStatus("idle");
          toast.error("Audio size exceeds 50MB limit.");
          return;
        }

        try {
          const ext = extensionForMimeType(blob.type);
          const name = `voice-${new Date()
            .toISOString()
            .replace(/[:.]/g, "-")}.${ext}`;
          const file = new File([blob], name, { type: blob.type });
          const base64 = await fileToBase64(file);
          await onAudioReadyRef.current(base64, name);
          toast.success("Voice message recorded", {
            description: "Press Send to use the voice message in this chat.",
          });
        } catch {
          toast.error("Could not prepare the voice message.");
        } finally {
          setStatus("idle");
        }
      };

      recorder.start();
      setStatus("recording");
      startTimer(limit);
    } catch (error) {
      clearTimer();
      stopStream(streamRef.current);
      streamRef.current = null;
      recorderRef.current = null;
      setSecondsElapsed(0);
      setStatus("idle");
      const message =
        error instanceof DOMException && error.name === "NotAllowedError"
          ? "Microphone permission was denied."
          : "Could not start microphone recording.";
      toast.error(message);
    }
  }, [clearTimer, maxSeconds, startTimer, status, supported]);

  useEffect(() => {
    return () => {
      clearTimer();
      const recorder = recorderRef.current;
      if (recorder && recorder.state !== "inactive") {
        recorder.stop();
      }
      stopStream(streamRef.current);
    };
  }, [clearTimer]);

  return { status, supported, secondsElapsed, secondsLimit, start, stop };
}
"""


def embedding_llama_args(model: str) -> list[str]:
    model_id = model.replace("\\", "/").lower()
    if "embed" not in model_id and "embedding" not in model_id:
        return []
    return ["--embedding", "--pooling", "last"]


def resolve_unsloth_exe(studio_home: Path) -> Path:
    exe = studio_home / "unsloth_studio" / "Scripts" / "unsloth.exe"
    if not exe.exists():
        raise FileNotFoundError(
            f"Unsloth Studio is not installed at {studio_home}.\n"
            "Install it first from https://unsloth.ai so the venv exists at "
            "<StudioHome>\\unsloth_studio."
        )
    return exe


def resolve_studio_python(studio_home: Path) -> Path:
    python = studio_home / "unsloth_studio" / "Scripts" / "python.exe"
    if not python.exists():
        raise FileNotFoundError(f"Missing venv python at {python}")
    return python


def studio_package_dir(studio_home: Path) -> Path:
    return studio_home / "unsloth_studio" / "Lib" / "site-packages" / "studio"


def studio_site_packages_dir(studio_home: Path) -> Path:
    return studio_package_dir(studio_home).parent


def resolve_unsloth_source_repo(value: str | None) -> Path | None:
    raw = (value or "").strip()
    if not raw:
        return None
    repo = Path(raw).expanduser()
    try:
        repo = repo.resolve()
    except OSError:
        pass
    required = (
        "pyproject.toml",
        "unsloth_cli/commands/studio.py",
        "studio/backend/run.py",
        "studio/backend/core/inference/llama_cpp.py",
    )
    missing = [name for name in required if not (repo / name).is_file()]
    if missing:
        raise FileNotFoundError(
            f"Unsloth source repo does not look complete: {repo}\n"
            f"Missing: {', '.join(missing)}"
        )
    return repo


def _prepend_pythonpath(env: dict[str, str], path: Path) -> None:
    path_text = str(path)
    parts = [part for part in env.get("PYTHONPATH", "").split(os.pathsep) if part]
    normalized = {part.lower() for part in parts}
    if path_text.lower() not in normalized:
        parts.insert(0, path_text)
    env["PYTHONPATH"] = os.pathsep.join(parts)


def _git_revision(repo: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        return "unknown"
    return completed.stdout.strip() or "unknown"


def prepare_unsloth_source_repo(
    source_repo: Path,
    *,
    build_frontend: bool = False,
) -> None:
    frontend = source_repo / "studio" / "frontend"
    dist_index = frontend / "dist" / "index.html"
    if build_frontend:
        if not (frontend / "package.json").is_file():
            raise FileNotFoundError(f"Missing Studio frontend package.json at {frontend}")
        if not (frontend / "node_modules").is_dir():
            echo("  installing Unsloth source frontend dependencies...")
            run(["npm.cmd", "install"], cwd=frontend)
        echo("  building Unsloth source frontend...")
        run(["npm.cmd", "run", "build"], cwd=frontend)
    elif not dist_index.is_file():
        echo(
            "  warning: source frontend dist is not built; Studio may fall back "
            "to the installed web dist. Use --source-build-frontend for a fully "
            "source-backed UI."
        )

    echo(f"  using Unsloth source repo: {source_repo} ({_git_revision(source_repo)})")


def configure_unsloth_source_env(env: dict[str, str], source_repo: Path) -> None:
    _prepend_pythonpath(env, source_repo)
    env["UNSLOTH_SOURCE_REPO"] = str(source_repo)
    env["STUDIO_LOCAL_INSTALL"] = "1"
    env["STUDIO_LOCAL_REPO"] = str(source_repo)


def unsloth_cli_studio_command(studio_home: Path) -> Path:
    return (
        studio_home
        / "unsloth_studio"
        / "Lib"
        / "site-packages"
        / "unsloth_cli"
        / "commands"
        / "studio.py"
    )


def installed_unsloth_version(studio_home: Path) -> str:
    site_packages = studio_site_packages_dir(studio_home)
    metadata_files = sorted(site_packages.glob("unsloth-*.dist-info/METADATA"))
    for metadata in metadata_files:
        for line in metadata.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("Version:"):
                version = line.split(":", 1)[1].strip()
                if version:
                    return version
    raise FileNotFoundError(f"Could not find installed Unsloth package metadata in {site_packages}")


def _patch_workspace(studio_home: Path) -> Path:
    return studio_home / ".win-models" / "studio-patches"


def _patch_base_dir(studio_home: Path, version: str) -> Path:
    return _patch_workspace(studio_home) / "bases" / f"unsloth-{version}" / "site-packages"


def _find_cached_unsloth_wheel(wheels_dir: Path, version: str) -> Path | None:
    wheels = sorted(wheels_dir.glob(f"unsloth-{version}-*.whl"))
    return wheels[0] if wheels else None


def _download_unsloth_wheel(studio_home: Path, version: str) -> Path:
    python = resolve_studio_python(studio_home)
    wheels_dir = _patch_workspace(studio_home) / "wheels"
    ensure_dir(wheels_dir)
    cached = _find_cached_unsloth_wheel(wheels_dir, version)
    if cached is not None:
        return cached

    echo(f"  downloading clean Unsloth wheel for patch base: unsloth=={version}")
    completed = subprocess.run(
        [
            python,
            "-m",
            "pip",
            "download",
            "--disable-pip-version-check",
            "--no-deps",
            "--only-binary=:all:",
            "--dest",
            str(wheels_dir),
            f"unsloth=={version}",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "Could not download the clean Unsloth wheel used as the patch base.\n"
            + completed.stdout.strip()
        )
    cached = _find_cached_unsloth_wheel(wheels_dir, version)
    if cached is None:
        raise FileNotFoundError(f"pip download succeeded but no unsloth=={version} wheel was found in {wheels_dir}")
    return cached


def _safe_extract_wheel_member(zf: zipfile.ZipFile, member: zipfile.ZipInfo, destination_root: Path) -> None:
    member_name = member.filename.replace("\\", "/")
    parts = [part for part in member_name.split("/") if part]
    if member.is_dir() or not parts:
        return
    if any(part in {"", ".", ".."} for part in parts):
        raise RuntimeError(f"Refusing unsafe wheel path: {member.filename}")
    destination = destination_root.joinpath(*parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zf.open(member) as source, destination.open("wb") as target:
        shutil.copyfileobj(source, target)


def _ensure_studio_patch_base(studio_home: Path) -> Path:
    version = installed_unsloth_version(studio_home)
    base_dir = _patch_base_dir(studio_home, version)
    if (base_dir / PATCH_BASE_COMPLETE_MARKER).is_file():
        return base_dir

    wheel = _download_unsloth_wheel(studio_home, version)
    tmp_dir = base_dir.with_name(base_dir.name + ".tmp")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    if base_dir.exists():
        shutil.rmtree(base_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(wheel) as zf:
        for member in zf.infolist():
            member_name = member.filename.replace("\\", "/")
            if member_name.startswith(PATCH_BASE_PACKAGE_PREFIXES):
                _safe_extract_wheel_member(zf, member, tmp_dir)

    if not (tmp_dir / "studio" / "__init__.py").is_file():
        raise RuntimeError(f"Wheel {wheel} did not contain the expected Studio package files")
    (tmp_dir / PATCH_BASE_COMPLETE_MARKER).write_text(version, encoding="utf-8")
    tmp_dir.rename(base_dir)
    echo(f"  cached clean Unsloth patch base: {base_dir}")
    return base_dir


def restore_studio_patch_base(studio_home: Path) -> None:
    base_dir = _ensure_studio_patch_base(studio_home)
    site_packages = studio_site_packages_dir(studio_home)
    restored = 0
    for rel in PATCHED_SITE_PACKAGE_FILES:
        source = base_dir / rel
        destination = site_packages / rel
        if not source.is_file():
            raise FileNotFoundError(f"Patch base is missing {rel}; refresh the base cache for {base_dir}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        restored += 1
    for rel in GENERATED_SITE_PACKAGE_FILES:
        generated = site_packages / rel
        if generated.exists():
            generated.unlink()
    echo(f"  restored {restored} Studio patch target(s) from clean Unsloth base")


def apply_embedding_extra_args_shim(studio_home: Path) -> None:
    target = studio_package_dir(studio_home) / "backend" / "core" / "inference" / "llama_server_args.py"
    if not target.exists():
        raise FileNotFoundError(f"Missing {target}")
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    if EMBEDDING_EXTRA_ARGS_SHIM_MARKER in text:
        echo("  embedding extra-args shim already present")
        return

    old = (
        '    # Server-mode flips: --embedding / --rerank restrict llama-server to\n'
        "    # those endpoints, breaking Studio's /v1/chat/completions hop.\n"
        '    frozenset({"--embedding", "--embeddings"}),\n'
    )
    new = (
        "    # UNSLOTH_EMBEDDING_EXTRA_ARGS_SHIM: win-models may intentionally run\n"
        "    # Studio with a dedicated embedding model, in which case /v1/chat/\n"
        "    # completions is irrelevant and the backend must allow --embedding.\n"
    )
    if old not in text:
        raise RuntimeError(
            f"Could not find the embedding denylist anchor in {target}; the Studio backend changed."
        )
    target.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
    echo("  applied embedding extra-args shim to backend.core.inference.llama_server_args")


def apply_fixed_llama_port_shim(studio_home: Path) -> None:
    target = studio_package_dir(studio_home) / "backend" / "core" / "inference" / "llama_cpp.py"
    if not target.exists():
        raise FileNotFoundError(f"Missing {target}")
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    if FIXED_LLAMA_PORT_SHIM_MARKER in text:
        echo("  fixed llama-server port shim already present")
        return

    old = '''    @staticmethod
    def _find_free_port() -> int:
        """Find an available TCP port."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]
'''
    new = f'''    @staticmethod
    def _find_free_port() -> int:
        """Find an available TCP port."""
        # {FIXED_LLAMA_PORT_SHIM_MARKER}: expose Studio's embedded llama-server
        # on a stable loopback port so Caddy and direct probes can target it.
        fixed_port = (
            os.environ.get("UNSLOTH_LLAMA_PORT")
            or os.environ.get("WIN_MODELS_LLAMA_PORT")
            or ""
        ).strip()
        if fixed_port:
            try:
                port = int(fixed_port)
            except ValueError as exc:
                raise RuntimeError(
                    f"UNSLOTH_LLAMA_PORT must be an integer, got {{fixed_port!r}}"
                ) from exc
            if port <= 0 or port > 65535:
                raise RuntimeError(
                    f"UNSLOTH_LLAMA_PORT must be between 1 and 65535, got {{port}}"
                )
            return port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]
'''
    if old not in text:
        raise RuntimeError(
            f"Could not find _find_free_port anchor in {target}; the Studio backend changed."
        )
    target.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
    echo("  applied fixed llama-server port shim to backend.core.inference.llama_cpp")


def apply_llama_local_zip_shim(studio_home: Path) -> None:
    target = studio_package_dir(studio_home) / "install_llama_prebuilt.py"
    if not target.exists():
        raise FileNotFoundError(f"Missing {target}")
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    if LOCAL_ZIP_MARKER in text or "using local asset" in text:
        echo("  llama local-zip shim already present")
        return

    anchor = "def download_file(url: str, destination: Path) -> None:\n    destination.parent.mkdir(parents = True, exist_ok = True)\n"
    if anchor not in text:
        raise RuntimeError(f"Could not find the download_file() anchor in {target}; the installer changed.")

    shim = """def download_file(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents = True, exist_ok = True)
    # UNSLOTH_LOCAL_ZIP_SHIM: reuse a manually-downloaded asset of the same name
    # (GitHub throttles the large release zips). Searches UNSLOTH_LLAMA_LOCAL_DIR
    # (os.pathsep-separated) then ~/Downloads. sha256/validation still run.
    _shim_dirs = []
    _shim_env = os.environ.get("UNSLOTH_LLAMA_LOCAL_DIR")
    if _shim_env:
        _shim_dirs.extend(p for p in _shim_env.split(os.pathsep) if p.strip())
    _shim_home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    if _shim_home:
        _shim_dirs.append(os.path.join(_shim_home, "Downloads"))
    for _shim_dir in _shim_dirs:
        try:
            _shim_cand = Path(_shim_dir) / destination.name
            if _shim_cand.is_file() and _shim_cand.stat().st_size > 0:
                log(f"using local asset {_shim_cand} instead of downloading {url}")
                shutil.copyfile(_shim_cand, destination)
                return
        except OSError:
            continue
"""
    target.write_text(text.replace(anchor, shim + "\n", 1), encoding="utf-8", newline="\n")
    echo("  applied llama local-zip shim to install_llama_prebuilt.py")


def apply_chat_template_override_shim(studio_home: Path) -> None:
    target = unsloth_cli_studio_command(studio_home)
    if not target.exists():
        raise FileNotFoundError(f"Missing {target}")
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    if CHAT_TEMPLATE_OVERRIDE_SHIM_MARKER in text:
        echo("  chat-template override shim already present")
        return

    signature_anchor = (
        "    llama_extra_args: Optional[List[str]] = None,\n"
        "    timeout: int = 600,\n"
    )
    if signature_anchor not in text:
        raise RuntimeError(
            f"Could not find _load_model_via_http signature anchor in {target}; "
            "the Studio CLI changed."
        )
    text = text.replace(
        signature_anchor,
        (
            "    llama_extra_args: Optional[List[str]] = None,\n"
            "    chat_template_override: Optional[str] = None,\n"
            "    timeout: int = 600,\n"
        ),
        1,
    )

    payload_anchor = (
        "    if llama_extra_args:\n"
        "        payload[\"llama_extra_args\"] = list(llama_extra_args)\n\n"
    )
    if payload_anchor not in text:
        raise RuntimeError(
            f"Could not find _load_model_via_http payload anchor in {target}; "
            "the Studio CLI changed."
        )
    text = text.replace(
        payload_anchor,
        (
            payload_anchor
            + "    if chat_template_override:\n"
            + "        payload[\"chat_template_override\"] = chat_template_override\n\n"
        ),
        1,
    )

    load_anchor = None
    load_indent = ""
    for candidate_indent in ("    ", "        "):
        candidate = (
            f"{candidate_indent}# 5. Load model via HTTP.\n"
            f"{candidate_indent}if not silent:\n"
            f"{candidate_indent}    typer.echo(f\"Loading model: {{model}}...\")\n"
            f"{candidate_indent}try:\n"
        )
        if candidate in text:
            load_anchor = candidate
            load_indent = candidate_indent
            break
    if load_anchor is None:
        raise RuntimeError(
            f"Could not find Studio run load anchor in {target}; the Studio CLI changed."
        )
    template_read = (
        f"{load_indent}# UNSLOTH_CHAT_TEMPLATE_OVERRIDE_SHIM: allow win-models to feed a\n"
        f"{load_indent}# repo-pinned Jinja template through Studio's first-class load field.\n"
        f"{load_indent}chat_template_override = None\n"
        f"{load_indent}chat_template_file = os.environ.get(\"UNSLOTH_CHAT_TEMPLATE_FILE\")\n"
        f"{load_indent}if chat_template_file:\n"
        f"{load_indent}    try:\n"
        f"{load_indent}        chat_template_override = Path(chat_template_file).read_text(encoding=\"utf-8\")\n"
        f"{load_indent}    except OSError as exc:\n"
        f"{load_indent}        typer.echo(f\"Error: could not read UNSLOTH_CHAT_TEMPLATE_FILE={{chat_template_file}}: {{exc}}\", err=True)\n"
        f"{load_indent}        raise typer.Exit(1)\n"
        f"{load_indent}    if not silent:\n"
        f"{load_indent}        typer.echo(f\"Using chat template override: {{chat_template_file}}\")\n\n"
    )
    text = text.replace(load_anchor, template_read + load_anchor, 1)

    call_anchor = "                llama_extra_args = extra_llama_args,\n"
    if call_anchor not in text:
        call_anchor = "            llama_extra_args = extra_llama_args,\n"
    if call_anchor not in text:
        raise RuntimeError(
            f"Could not find _load_model_via_http call anchor in {target}; "
            "the Studio CLI changed."
        )
    text = text.replace(
        call_anchor,
        call_anchor + call_anchor.replace(
            "llama_extra_args = extra_llama_args",
            "chat_template_override = chat_template_override",
        ),
        1,
    )

    target.write_text(text, encoding="utf-8", newline="\n")
    echo("  applied chat-template override shim to unsloth_cli.commands.studio")


def apply_speculative_type_shim(studio_home: Path) -> None:
    target = unsloth_cli_studio_command(studio_home)
    if not target.exists():
        raise FileNotFoundError(f"Missing {target}")
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    if SPECULATIVE_TYPE_SHIM_MARKER in text:
        echo("  speculative-type shim already present")
        return

    signature_anchor = (
        "    chat_template_override: Optional[str] = None,\n"
        "    timeout: int = 600,\n"
    )
    if signature_anchor not in text:
        raise RuntimeError(
            f"Could not find _load_model_via_http speculative signature anchor in {target}; "
            "the Studio CLI changed."
        )
    text = text.replace(
        signature_anchor,
        (
            "    chat_template_override: Optional[str] = None,\n"
            "    speculative_type: Optional[str] = None,\n"
            "    timeout: int = 600,\n"
        ),
        1,
    )

    payload_anchor = (
        "    if chat_template_override:\n"
        "        payload[\"chat_template_override\"] = chat_template_override\n\n"
    )
    if payload_anchor not in text:
        raise RuntimeError(
            f"Could not find _load_model_via_http speculative payload anchor in {target}; "
            "the Studio CLI changed."
        )
    text = text.replace(
        payload_anchor,
        (
            payload_anchor
            + "    if speculative_type:\n"
            + "        payload[\"speculative_type\"] = speculative_type\n\n"
        ),
        1,
    )

    load_anchor = None
    load_indent = ""
    for candidate_indent in ("        ", "    "):
        candidate = (
            f"{candidate_indent}# 5. Load model via HTTP.\n"
            f"{candidate_indent}if not silent:\n"
            f"{candidate_indent}    typer.echo(f\"Loading model: {{model}}...\")\n"
            f"{candidate_indent}try:\n"
        )
        if candidate in text:
            load_anchor = candidate
            load_indent = candidate_indent
            break
    if load_anchor is None:
        raise RuntimeError(
            f"Could not find Studio run speculative load anchor in {target}; "
            "the Studio CLI changed."
        )
    speculative_read = (
        f"{load_indent}# {SPECULATIVE_TYPE_SHIM_MARKER}: let win-models control\n"
        f"{load_indent}# Studio's first-class speculative decoding mode.\n"
        f"{load_indent}speculative_type = (os.environ.get(\"UNSLOTH_SPECULATIVE_TYPE\") or \"\").strip() or None\n"
        f"{load_indent}if speculative_type and not silent:\n"
        f"{load_indent}    typer.echo(f\"Using speculative decoding mode: {{speculative_type}}\")\n\n"
    )
    text = text.replace(load_anchor, speculative_read + load_anchor, 1)

    call_anchor = "                chat_template_override = chat_template_override,\n"
    if call_anchor not in text:
        call_anchor = "            chat_template_override = chat_template_override,\n"
    if call_anchor not in text:
        raise RuntimeError(
            f"Could not find _load_model_via_http speculative call anchor in {target}; "
            "the Studio CLI changed."
        )
    text = text.replace(
        call_anchor,
        call_anchor
        + call_anchor.replace(
            "chat_template_override = chat_template_override",
            "speculative_type = speculative_type",
        ),
        1,
    )

    target.write_text(text, encoding="utf-8", newline="\n")
    echo("  applied speculative-type shim to unsloth_cli.commands.studio")


def apply_cli_api_key_reuse_shim(studio_home: Path) -> None:
    target = unsloth_cli_studio_command(studio_home)
    if not target.exists():
        raise FileNotFoundError(f"Missing {target}")
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    if CLI_API_KEY_REUSE_SHIM_MARKER in text:
        echo("  CLI API-key reuse shim already present")
        return

    anchor = '''def _create_api_key_inprocess(name: str) -> str:
    """Create an API key via direct storage call (no HTTP needed).

    Bypasses the ``must_change_password`` gate that blocks HTTP
    ``POST /api/auth/api-keys`` on fresh installs.  Safe because the
    CLI already has filesystem access to ``~/.unsloth/studio``.
    """
    storage = _load_backend_auth_storage()

    raw_key, _row = storage.create_api_key(
        username = storage.DEFAULT_ADMIN_USERNAME,
        name = name,
    )
    return raw_key
'''
    if anchor not in text:
        raise RuntimeError(
            f"Could not find _create_api_key_inprocess anchor in {target}; "
            "the Studio CLI changed."
        )

    replacement = '''def _create_api_key_inprocess(name: str) -> str:
    """Create or reuse an API key via direct storage call (no HTTP needed).

    Bypasses the ``must_change_password`` gate that blocks HTTP
    ``POST /api/auth/api-keys`` on fresh installs.  Safe because the
    CLI already has filesystem access to ``~/.unsloth/studio``.
    """
    storage = _load_backend_auth_storage()

    # UNSLOTH_CLI_API_KEY_REUSE_SHIM: upstream creates a fresh visible
    # key named "cli" on every `unsloth studio run`. Keep custom names
    # upstream-compatible, but reuse one local raw key for the default.
    reuse_enabled = os.environ.get("UNSLOTH_REUSE_CLI_API_KEY", "1").lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    if name != "cli" or not reuse_enabled:
        raw_key, _row = storage.create_api_key(
            username = storage.DEFAULT_ADMIN_USERNAME,
            name = name,
        )
        return raw_key

    key_path = STUDIO_HOME / "auth" / "cli-api-key.txt"
    try:
        raw_key = key_path.read_text(encoding = "utf-8").strip()
    except OSError:
        raw_key = ""
    if raw_key and storage.validate_api_key(raw_key) == storage.DEFAULT_ADMIN_USERNAME:
        return raw_key

    raw_key = storage.API_KEY_PREFIX + secrets.token_hex(16)
    key_hash = storage._pbkdf2_api_key(raw_key)
    key_prefix = raw_key[len(storage.API_KEY_PREFIX) : len(storage.API_KEY_PREFIX) + 8]
    now = datetime.now(timezone.utc).isoformat()

    conn = storage.get_connection()
    try:
        existing = conn.execute(
            "SELECT id FROM api_keys WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO api_keys (
                    username, key_prefix, key_hash, name,
                    created_at, expires_at, is_internal
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    storage.DEFAULT_ADMIN_USERNAME,
                    key_prefix,
                    key_hash,
                    name,
                    now,
                    None,
                    0,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE api_keys
                   SET username = ?, name = ?, is_active = 1, expires_at = NULL
                 WHERE id = ?
                """,
                (storage.DEFAULT_ADMIN_USERNAME, name, existing["id"]),
            )
        conn.commit()
    finally:
        conn.close()

    _write_auth_secret(key_path, raw_key)
    return raw_key
'''
    target.write_text(text.replace(anchor, replacement, 1), encoding="utf-8", newline="\n")
    echo("  applied CLI API-key reuse shim to unsloth_cli.commands.studio")


def apply_openai_reasoning_passthrough_shim(studio_home: Path) -> None:
    target = studio_package_dir(studio_home) / "backend" / "routes" / "inference.py"
    if not target.exists():
        raise FileNotFoundError(f"Missing {target}")
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    if OPENAI_REASONING_PASSTHROUGH_SHIM_MARKER in text:
        echo("  OpenAI reasoning passthrough shim already present")
        return

    helper_anchor = '''def _chat_content_chunk(completion_id, created, model_name, text) -> str:
    """A content-delta chunk carrying ``text``."""
    return _chat_chunk_sse(
        completion_id,
        created,
        model_name,
        delta = ChoiceDelta(content = text),
        finish_reason = None,
    )


def _chat_final_chunk(completion_id, created, model_name, finish_reason) -> str:
'''
    helper_replacement = '''def _chat_content_chunk(completion_id, created, model_name, text) -> str:
    """A content-delta chunk carrying ``text``."""
    return _chat_chunk_sse(
        completion_id,
        created,
        model_name,
        delta = ChoiceDelta(content = text),
        finish_reason = None,
    )


def _split_reasoning_tags_for_openai(text: str) -> tuple[str, str]:
    # UNSLOTH_OPENAI_REASONING_PASSTHROUGH_SHIM: Studio wraps llama.cpp
    # reasoning_content as <think>...</think> for its UI. Split it back into
    # OpenAI-compatible content/reasoning streams at the API boundary.
    start_tag = "<think>"
    end_tag = "</think>"
    start = text.find(start_tag)
    if start < 0:
        return text, ""
    before = text[:start]
    body_start = start + len(start_tag)
    end = text.find(end_tag, body_start)
    if end < 0:
        return before, text[body_start:]
    return before + text[end + len(end_tag) :], text[body_start:end]


def _chat_reasoning_chunk(completion_id, created, model_name, text) -> str:
    obj = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model_name,
        "choices": [
            {
                "index": 0,
                "delta": {"reasoning_content": text},
                "finish_reason": None,
            }
        ],
    }
    return "data: " + json.dumps(obj, separators = (",", ":")) + "\\n\\n"


def _chat_final_chunk(completion_id, created, model_name, finish_reason) -> str:
'''
    if helper_anchor not in text:
        raise RuntimeError(f"Could not find _chat_content_chunk anchor in {target}; Studio changed.")
    text = text.replace(helper_anchor, helper_replacement, 1)

    replacements = [
        (
            '                    prev_text = ""\n'
            '                    _stream_usage = None\n',
            '                    prev_text = ""\n'
            '                    prev_reasoning_text = ""\n'
            '                    _stream_usage = None\n',
            "GGUF tool stream state",
        ),
        (
            '                            if not event["text"]:\n'
            '                                prev_text = ""\n',
            '                            if not event["text"]:\n'
            '                                prev_text = ""\n'
            '                                prev_reasoning_text = ""\n',
            "GGUF tool status reset",
        ),
        (
            '                            if event["type"] == "tool_start":\n'
            '                                prev_text = ""\n',
            '                            if event["type"] == "tool_start":\n'
            '                                prev_text = ""\n'
            '                                prev_reasoning_text = ""\n',
            "GGUF tool_start reset",
        ),
        (
            '                        new_text = clean_cumulative[len(prev_text) :]\n'
            '                        prev_text = clean_cumulative\n'
            '                        if not new_text:\n'
            '                            continue\n'
            '                        api_monitor.append_reply(monitor_id, new_text)\n'
            '                        yield _chat_content_chunk(completion_id, created, model_name, new_text)\n',
            '                        content_cumulative, reasoning_cumulative = _split_reasoning_tags_for_openai(\n'
            '                            clean_cumulative\n'
            '                        )\n'
            '                        new_reasoning = reasoning_cumulative[len(prev_reasoning_text) :]\n'
            '                        prev_reasoning_text = reasoning_cumulative\n'
            '                        if new_reasoning:\n'
            '                            yield _chat_reasoning_chunk(\n'
            '                                completion_id, created, model_name, new_reasoning\n'
            '                            )\n'
            '                        new_text = content_cumulative[len(prev_text) :]\n'
            '                        prev_text = content_cumulative\n'
            '                        if not new_text:\n'
            '                            continue\n'
            '                        api_monitor.append_reply(monitor_id, new_text)\n'
            '                        yield _chat_content_chunk(completion_id, created, model_name, new_text)\n',
            "GGUF tool content split",
        ),
        (
            '                    prev_text = ""\n'
            '                    _stream_usage = None\n',
            '                    prev_text = ""\n'
            '                    prev_reasoning_text = ""\n'
            '                    _stream_usage = None\n',
            "GGUF stream state",
        ),
        (
            '                        new_text = cumulative[len(prev_text) :]\n'
            '                        prev_text = cumulative\n'
            '                        if not new_text:\n'
            '                            continue\n'
            '                        api_monitor.append_reply(monitor_id, new_text)\n'
            '                        yield _chat_content_chunk(completion_id, created, model_name, new_text)\n',
            '                        content_cumulative, reasoning_cumulative = _split_reasoning_tags_for_openai(\n'
            '                            cumulative\n'
            '                        )\n'
            '                        new_reasoning = reasoning_cumulative[len(prev_reasoning_text) :]\n'
            '                        prev_reasoning_text = reasoning_cumulative\n'
            '                        if new_reasoning:\n'
            '                            yield _chat_reasoning_chunk(\n'
            '                                completion_id, created, model_name, new_reasoning\n'
            '                            )\n'
            '                        new_text = content_cumulative[len(prev_text) :]\n'
            '                        prev_text = content_cumulative\n'
            '                        if not new_text:\n'
            '                            continue\n'
            '                        api_monitor.append_reply(monitor_id, new_text)\n'
            '                        yield _chat_content_chunk(completion_id, created, model_name, new_text)\n',
            "GGUF stream content split",
        ),
        (
            '                        full_text = token\n'
            '\n'
            '                    _choices.append(\n',
            '                        full_text = token\n'
            '\n'
            '                    full_text, _reasoning_text = _split_reasoning_tags_for_openai(full_text)\n'
            '                    _choices.append(\n',
            "GGUF non-stream content split",
        ),
        (
            '                        full_text = _strip_tool_xml_for_display(\n'
            '                            event.get("text", ""),\n'
            '                            auto_heal_tool_calls = _sf_auto_heal_tool_calls,\n'
            '                        )\n',
            '                        full_text = _strip_tool_xml_for_display(\n'
            '                            event.get("text", ""),\n'
            '                            auto_heal_tool_calls = _sf_auto_heal_tool_calls,\n'
            '                        )\n'
            '                        full_text, _reasoning_text = _split_reasoning_tags_for_openai(full_text)\n',
            "tool non-stream content split",
        ),
    ]
    for old, new, label in replacements:
        if old not in text:
            raise RuntimeError(f"Could not find {label} anchor in {target}; Studio changed.")
        text = text.replace(old, new, 1)

    target.write_text(text, encoding="utf-8", newline="\n")
    echo("  applied OpenAI reasoning passthrough shim to backend.routes.inference")


def apply_openai_request_autoload_shim(studio_home: Path) -> None:
    target = studio_package_dir(studio_home) / "backend" / "routes" / "inference.py"
    if not target.exists():
        raise FileNotFoundError(f"Missing {target}")
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    if OPENAI_REQUEST_AUTOLOAD_SHIM_MARKER in text:
        echo("  OpenAI request autoload shim already present")
        return

    helper_anchor = '''def _flatten_monitor_prompt(value) -> str:
    """Flatten an OpenAI prompt/input field (str or list) into the single
    string the api_monitor prompt preview expects."""
    if isinstance(value, list):
        return "\\n".join(str(part) for part in value)
    return str(value)


@router.post("/completions")
'''
    helper_replacement = '''def _flatten_monitor_prompt(value) -> str:
    """Flatten an OpenAI prompt/input field (str or list) into the single
    string the api_monitor prompt preview expects."""
    if isinstance(value, list):
        return "\\n".join(str(part) for part in value)
    return str(value)


def _openai_request_bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


def _openai_request_int_env(name: str, default: int = 0) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("%s has invalid integer value %r", name, raw)
        return default


def _split_openai_requested_model(model_name: str) -> tuple[str, Optional[str]]:
    # UNSLOTH_OPENAI_REQUEST_AUTOLOAD_SHIM: accept llama.cpp-style repo:variant
    # ids in API payload.model so request-driven auto-load matches CLI usage.
    s = model_name.strip()
    if not s:
        return s, None
    if s.startswith(("/", "./", "../", "~")) or s == ".":
        return s, None
    if len(s) >= 2 and s[1] == ":" and s[0].isalpha():
        return s, None
    if ":" not in s:
        return s, None
    repo, _, variant = s.rpartition(":")
    if not repo or not variant or "/" in variant:
        return s, None
    return repo, variant


def _openai_requested_model_name(requested_model: object) -> Optional[str]:
    if requested_model is None:
        requested = ""
    else:
        requested = str(requested_model).strip()
    if requested and requested.lower() not in {"default", "auto"}:
        return requested
    fallback = (os.environ.get("UNSLOTH_DEFAULT_MODEL") or "").strip()
    return fallback or None


def _openai_request_llama_extra_args(model_name: str) -> Optional[list[str]]:
    args: list[str] = []
    reasoning_format = (os.environ.get("UNSLOTH_REASONING_FORMAT") or "").strip()
    if reasoning_format:
        args.extend(["--reasoning-format", reasoning_format])
    model_id = model_name.replace("\\\\", "/").lower()
    if "embed" in model_id or "embedding" in model_id:
        args.extend(["--embedding", "--pooling", "last"])
    return args or None


def _openai_request_chat_template_override() -> Optional[str]:
    template_path = (os.environ.get("UNSLOTH_CHAT_TEMPLATE_FILE") or "").strip()
    if not template_path:
        return None
    try:
        return Path(template_path).read_text(encoding = "utf-8")
    except OSError as exc:
        logger.warning("Could not read UNSLOTH_CHAT_TEMPLATE_FILE=%s: %s", template_path, exc)
        return None


async def _autoload_openai_requested_model(
    request: Request, current_subject: str, requested_model: object
) -> Optional[str]:
    requested = _openai_requested_model_name(requested_model)
    if not requested:
        return None

    requested_path, requested_variant = _split_openai_requested_model(requested)
    llama_backend = get_llama_cpp_backend()
    if llama_backend.is_loaded and llama_backend.model_identifier:
        active_variant = (getattr(llama_backend, "hf_variant", None) or "").lower()
        if (
            llama_backend.model_identifier.lower() == requested_path.lower()
            and (
                requested_variant is None
                or requested_path.lower().endswith(".gguf")
                or requested_variant.lower() == active_variant
            )
        ):
            return llama_backend.model_identifier

    backend = get_inference_backend()
    if backend.active_model_name and backend.active_model_name.lower() == requested_path.lower():
        return backend.active_model_name

    load_request = LoadRequest(
        model_path = requested_path,
        gguf_variant = requested_variant,
        max_seq_length = _openai_request_int_env("UNSLOTH_CONTEXT_LENGTH", 0),
        load_in_4bit = _openai_request_bool_env("UNSLOTH_AUTOLOAD_LOAD_IN_4BIT", True),
        chat_template_override = _openai_request_chat_template_override(),
        cache_type_kv = (os.environ.get("UNSLOTH_CACHE_TYPE_KV") or "").strip() or None,
        llama_extra_args = _openai_request_llama_extra_args(requested),
    )
    logger.info("OpenAI request auto-loading model %s", requested)
    result = await load_model(
        load_request,
        fastapi_request = request,
        current_subject = current_subject,
    )
    return result.model or requested_path


@router.post("/completions")
'''
    if helper_anchor not in text:
        raise RuntimeError(f"Could not find OpenAI completions helper anchor in {target}; Studio changed.")
    text = text.replace(helper_anchor, helper_replacement, 1)

    replacements = [
        (
            '''    llama_backend = get_llama_cpp_backend()
    if not llama_backend.is_loaded:
        raise HTTPException(
            status_code = 503,
            detail = "No GGUF model loaded. Load a GGUF model first.",
        )

    body = await request.json()
''',
            '''    body = await request.json()
    await _autoload_openai_requested_model(request, current_subject, body.get("model"))
    llama_backend = get_llama_cpp_backend()
    if not llama_backend.is_loaded:
        raise HTTPException(
            status_code = 503,
            detail = "No GGUF model loaded. Load a GGUF model first.",
        )
''',
            "OpenAI completions autoload",
        ),
        (
            '''    # ── Determine which backend is active ──────────────────────
    # Single-model server: any model name serves the loaded model (drop-in
    # OpenAI compat), so payload.model is only a fallback label here.
    monitor_id = None
''',
            '''    # ── Determine which backend is active ──────────────────────
    # Single-model server: any model name serves the loaded model (drop-in
    # OpenAI compat), so payload.model is only a fallback label here.
    requested_model_name = await _autoload_openai_requested_model(
        request, current_subject, payload.model
    )
    monitor_id = None
''',
            "OpenAI chat autoload trigger",
        ),
        (
            '        model_name = llama_backend.model_identifier or payload.model\n',
            '        model_name = llama_backend.model_identifier or requested_model_name or payload.model\n',
            "OpenAI chat GGUF model label",
        ),
        (
            '        model_name = backend.active_model_name or payload.model\n',
            '        model_name = backend.active_model_name or requested_model_name or payload.model\n',
            "OpenAI chat Unsloth model label",
        ),
        (
            '''    llama_backend = get_llama_cpp_backend()
    if not llama_backend.is_loaded:
        raise HTTPException(
            status_code = 503,
            detail = "No GGUF model loaded. Load a GGUF model first.",
        )

    body = await request.json()
''',
            '''    body = await request.json()
    await _autoload_openai_requested_model(request, current_subject, body.get("model"))
    llama_backend = get_llama_cpp_backend()
    if not llama_backend.is_loaded:
        raise HTTPException(
            status_code = 503,
            detail = "No GGUF model loaded. Load a GGUF model first.",
        )
''',
            "OpenAI embeddings autoload",
        ),
        (
            '''    if payload.stream:
''',
            '''    await _autoload_openai_requested_model(request, current_subject, payload.model)

    if payload.stream:
''',
            "OpenAI responses autoload trigger",
        ),
    ]
    for old, new, label in replacements:
        if old not in text:
            raise RuntimeError(f"Could not find {label} anchor in {target}; Studio changed.")
        text = text.replace(old, new, 1)

    target.write_text(text, encoding="utf-8", newline="\n")
    echo("  applied OpenAI request autoload shim to backend.routes.inference")


def apply_openai_request_autoload_shim_v2(studio_home: Path) -> None:
    target = studio_package_dir(studio_home) / "backend" / "routes" / "inference.py"
    if not target.exists():
        raise FileNotFoundError(f"Missing {target}")
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    if OPENAI_REQUEST_AUTOLOAD_SHIM_MARKER in text:
        echo("  OpenAI request autoload shim already present")
        return

    helper_anchor = '''def _flatten_monitor_prompt(value) -> str:
    """Flatten an OpenAI prompt/input field (str or list) into the single
    string the api_monitor prompt preview expects."""
    if isinstance(value, list):
        return "\\n".join(str(part) for part in value)
    return str(value)


@router.post("/completions")
'''
    helper_replacement = '''def _flatten_monitor_prompt(value) -> str:
    """Flatten an OpenAI prompt/input field (str or list) into the single
    string the api_monitor prompt preview expects."""
    if isinstance(value, list):
        return "\\n".join(str(part) for part in value)
    return str(value)


def _openai_request_bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


def _openai_request_int_env(name: str, default: int = 0) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("%s has invalid integer value %r", name, raw)
        return default


def _split_openai_requested_model(model_name: str) -> tuple[str, Optional[str]]:
    # UNSLOTH_OPENAI_REQUEST_AUTOLOAD_SHIM: accept llama.cpp-style repo:variant
    # ids in API payload.model so request-driven auto-load matches CLI usage.
    s = model_name.strip()
    if not s:
        return s, None
    if s.startswith(("/", "./", "../", "~")) or s == ".":
        return s, None
    if len(s) >= 2 and s[1] == ":" and s[0].isalpha():
        return s, None
    if ":" not in s:
        return s, None
    repo, _, variant = s.rpartition(":")
    if not repo or not variant or "/" in variant:
        return s, None
    return repo, variant


def _openai_requested_model_name(requested_model: object) -> Optional[str]:
    if requested_model is None:
        requested = ""
    else:
        requested = str(requested_model).strip()
    if requested and requested.lower() not in {"default", "auto"}:
        return requested
    fallback = (os.environ.get("UNSLOTH_DEFAULT_MODEL") or "").strip()
    return fallback or None


def _openai_request_llama_extra_args(model_name: str) -> Optional[list[str]]:
    args: list[str] = []
    reasoning_format = (os.environ.get("UNSLOTH_REASONING_FORMAT") or "").strip()
    if reasoning_format:
        args.extend(["--reasoning-format", reasoning_format])
    model_id = model_name.replace("\\\\", "/").lower()
    if "embed" in model_id or "embedding" in model_id:
        args.extend(["--embedding", "--pooling", "last"])
    return args or None


def _openai_request_chat_template_override() -> Optional[str]:
    template_path = (os.environ.get("UNSLOTH_CHAT_TEMPLATE_FILE") or "").strip()
    if not template_path:
        return None
    try:
        return Path(template_path).read_text(encoding = "utf-8")
    except OSError as exc:
        logger.warning("Could not read UNSLOTH_CHAT_TEMPLATE_FILE=%s: %s", template_path, exc)
        return None


async def _autoload_openai_requested_model(
    request: Request, current_subject: str, requested_model: object
) -> Optional[str]:
    requested = _openai_requested_model_name(requested_model)
    if not requested:
        return None

    requested_path, requested_variant = _split_openai_requested_model(requested)
    llama_backend = get_llama_cpp_backend()
    if llama_backend.is_loaded and llama_backend.model_identifier:
        active_variant = (getattr(llama_backend, "hf_variant", None) or "").lower()
        if (
            llama_backend.model_identifier.lower() == requested_path.lower()
            and (
                requested_variant is None
                or requested_path.lower().endswith(".gguf")
                or requested_variant.lower() == active_variant
            )
        ):
            return llama_backend.model_identifier

    backend = get_inference_backend()
    if backend.active_model_name and backend.active_model_name.lower() == requested_path.lower():
        return backend.active_model_name

    load_request = LoadRequest(
        model_path = requested_path,
        gguf_variant = requested_variant,
        max_seq_length = _openai_request_int_env("UNSLOTH_CONTEXT_LENGTH", 0),
        load_in_4bit = _openai_request_bool_env("UNSLOTH_AUTOLOAD_LOAD_IN_4BIT", True),
        chat_template_override = _openai_request_chat_template_override(),
        cache_type_kv = (os.environ.get("UNSLOTH_CACHE_TYPE_KV") or "").strip() or None,
        llama_extra_args = _openai_request_llama_extra_args(requested),
    )
    logger.info("OpenAI request auto-loading model %s", requested)
    result = await load_model(
        load_request,
        fastapi_request = request,
        current_subject = current_subject,
    )
    return result.model or requested_path


@router.post("/completions")
'''
    if helper_anchor not in text:
        raise RuntimeError(f"Could not find OpenAI completions helper anchor in {target}; Studio changed.")
    text = text.replace(helper_anchor, helper_replacement, 1)

    replacements = [
        (
            '''    llama_backend = get_llama_cpp_backend()
    if not llama_backend.is_loaded:
        raise HTTPException(
            status_code = 503,
            detail = "No GGUF model loaded. Load a GGUF model first.",
        )

    body = await request.json()
''',
            '''    body = await request.json()
    await _autoload_openai_requested_model(request, current_subject, body.get("model"))
    llama_backend = get_llama_cpp_backend()
    if not llama_backend.is_loaded:
        raise HTTPException(
            status_code = 503,
            detail = "No GGUF model loaded. Load a GGUF model first.",
        )
''',
            "OpenAI completions autoload",
        ),
        (
            '''    monitor_id = None

    async def _monitored_generate_audio(model_label: str, context_length: Optional[int] = None):
''',
            '''    requested_model_name = await _autoload_openai_requested_model(
        request, current_subject, payload.model
    )
    monitor_id = None

    async def _monitored_generate_audio(model_label: str, context_length: Optional[int] = None):
''',
            "OpenAI chat autoload trigger",
        ),
        (
            '        model_name = llama_backend.model_identifier or payload.model\n',
            '        model_name = llama_backend.model_identifier or requested_model_name or payload.model\n',
            "OpenAI chat GGUF model label",
        ),
        (
            '        model_name = backend.active_model_name or payload.model\n',
            '        model_name = backend.active_model_name or requested_model_name or payload.model\n',
            "OpenAI chat Unsloth model label",
        ),
        (
            '''    llama_backend = get_llama_cpp_backend()
    if not llama_backend.is_loaded:
        raise HTTPException(
            status_code = 503,
            detail = "No GGUF model loaded. Load a GGUF model first.",
        )

    body = await request.json()
''',
            '''    body = await request.json()
    await _autoload_openai_requested_model(request, current_subject, body.get("model"))
    llama_backend = get_llama_cpp_backend()
    if not llama_backend.is_loaded:
        raise HTTPException(
            status_code = 503,
            detail = "No GGUF model loaded. Load a GGUF model first.",
        )
''',
            "OpenAI embeddings autoload",
        ),
        (
            '''    if not messages:
        raise HTTPException(status_code = 400, detail = "No input provided.")

    if payload.stream:
''',
            '''    if not messages:
        raise HTTPException(status_code = 400, detail = "No input provided.")

    await _autoload_openai_requested_model(request, current_subject, payload.model)

    if payload.stream:
''',
            "OpenAI responses autoload trigger",
        ),
    ]
    for old, new, label in replacements:
        if old not in text:
            raise RuntimeError(f"Could not find {label} anchor in {target}; Studio changed.")
        text = text.replace(old, new, 1)

    target.write_text(text, encoding="utf-8", newline="\n")
    echo("  applied OpenAI request autoload shim to backend.routes.inference")


def apply_openai_autoload_speculative_shim(studio_home: Path) -> None:
    target = studio_package_dir(studio_home) / "backend" / "routes" / "inference.py"
    if not target.exists():
        raise FileNotFoundError(f"Missing {target}")
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    if OPENAI_AUTOLOAD_SPECULATIVE_SHIM_MARKER in text:
        echo("  OpenAI autoload speculative shim already present")
        return

    anchor = (
        '        cache_type_kv = (os.environ.get("UNSLOTH_CACHE_TYPE_KV") or "").strip() or None,\n'
        "        llama_extra_args = _openai_request_llama_extra_args(requested),\n"
    )
    if anchor not in text:
        raise RuntimeError(
            f"Could not find OpenAI autoload speculative anchor in {target}; "
            "the Studio backend changed or the autoload shim has not been applied."
        )
    replacement = (
        '        cache_type_kv = (os.environ.get("UNSLOTH_CACHE_TYPE_KV") or "").strip() or None,\n'
        f"        # {OPENAI_AUTOLOAD_SPECULATIVE_SHIM_MARKER}: keep request autoload\n"
        "        # aligned with win-models startup defaults.\n"
        '        speculative_type = (os.environ.get("UNSLOTH_SPECULATIVE_TYPE") or "").strip() or None,\n'
        "        llama_extra_args = _openai_request_llama_extra_args(requested),\n"
    )
    target.write_text(text.replace(anchor, replacement, 1), encoding="utf-8", newline="\n")
    echo("  applied OpenAI autoload speculative shim to backend.routes.inference")


def apply_web_ui_tool_policy_shim(studio_home: Path) -> None:
    frontend_auth = (
        studio_package_dir(studio_home)
        / "frontend"
        / "src"
        / "features"
        / "auth"
        / "api.ts"
    )
    if not frontend_auth.exists():
        raise FileNotFoundError(f"Missing {frontend_auth}")
    text = frontend_auth.read_text(encoding="utf-8").replace("\r\n", "\n")
    changed = False
    if 'headers.set("X-Unsloth-Studio-UI", "1");' not in text:
        text = _replace_once(
            text,
            "  const retryHeaders = new Headers(init?.headers);\n",
            (
                "  const retryHeaders = new Headers(init?.headers);\n"
                "  retryHeaders.set(\"X-Unsloth-Studio-UI\", \"1\");\n"
            ),
            frontend_auth,
            "Studio UI auth retry header",
        )
        text = _replace_once(
            text,
            "  const headers = new Headers(init?.headers);\n",
            (
                "  const headers = new Headers(init?.headers);\n"
                "  headers.set(\"X-Unsloth-Studio-UI\", \"1\");\n"
            ),
            frontend_auth,
            "Studio UI auth header",
        )
        frontend_auth.write_text(text, encoding="utf-8", newline="\n")
        changed = True

    backend = studio_package_dir(studio_home) / "backend" / "routes" / "inference.py"
    if not backend.exists():
        raise FileNotFoundError(f"Missing {backend}")
    text = backend.read_text(encoding="utf-8").replace("\r\n", "\n")
    if WEB_UI_TOOL_POLICY_SHIM_MARKER not in text:
        text = _replace_once(
            text,
            "from auth.authentication import get_current_subject\n",
            (
                "from auth.authentication import get_current_subject\n"
                "from auth.storage import API_KEY_PREFIX\n"
            ),
            backend,
            "API key prefix import",
        )
        old = '''def _effective_enable_tools(payload) -> Optional[bool]:
    """Resolve `payload.enable_tools` against the process-level tool policy.

    Returns the policy value when set (CLI hard-override from `unsloth run`),
    else the per-request value.
    """
    from state.tool_policy import get_tool_policy

    policy = get_tool_policy()
    return policy if policy is not None else payload.enable_tools
'''
        new = f'''def _is_api_key_request(request: Optional[Request]) -> bool:
    auth_header = request.headers.get("authorization", "") if request is not None else ""
    if not auth_header.lower().startswith("bearer "):
        return False
    return auth_header[7:].startswith(API_KEY_PREFIX)


def _is_studio_web_ui_request(request: Optional[Request]) -> bool:
    return (
        request is not None
        and request.headers.get("x-unsloth-studio-ui") == "1"
        and not _is_api_key_request(request)
    )


def _effective_enable_tools(payload, request: Optional[Request] = None) -> Optional[bool]:
    """Resolve `payload.enable_tools` against process and caller policy.

    # {WEB_UI_TOOL_POLICY_SHIM_MARKER}: default to UI-only server-side tools.
    Generated API keys cannot enable in-process tools even if they send
    enable_tools=true. The Studio web UI can opt in per turn via authFetch's
    first-party header. Explicit CLI policy only applies to tagged UI traffic.
    """
    from state.tool_policy import get_tool_policy

    if not _is_studio_web_ui_request(request):
        return False
    policy = get_tool_policy()
    if policy is not None:
        return policy
    return payload.enable_tools
'''
        text = _replace_once(text, old, new, backend, "web UI tool policy helper")
        text = text.replace(
            "_effective_enable_tools(payload)",
            "_effective_enable_tools(payload, request)",
        )
        backend.write_text(text, encoding="utf-8", newline="\n")
        changed = True

    if changed:
        echo("  applied web UI-only tool policy shim")
    else:
        echo("  web UI-only tool policy shim already present")


def find_llama_zips(zip_dir: Path, release_tag: str, runtime: str) -> tuple[Path, Path]:
    bin_name = f"llama-{release_tag}-bin-win-cuda-{runtime}-x64.zip"
    cudart_name = f"cudart-llama-bin-win-cuda-{runtime}-x64.zip"
    bin_zip = zip_dir / bin_name
    cudart_zip = zip_dir / cudart_name
    missing = [name for name, path in ((bin_name, bin_zip), (cudart_name, cudart_zip)) if not path.exists()]
    if missing:
        base = f"https://github.com/ggml-org/llama.cpp/releases/download/{release_tag}"
        examples = "\n".join(f"  {base}/{name}" for name in missing)
        raise FileNotFoundError(
            f"Missing prebuilt zip(s) in {zip_dir}:\n"
            f"{chr(10).join(missing)}\n"
            "Download them with a resumable downloader, e.g.:\n"
            f"{examples}\n"
            "Then re-run, or pass --zip-dir to point at the folder that holds them."
        )
    return bin_zip, cudart_zip


def register_scan_folder(studio_home: Path, scan_path: Path) -> None:
    if not scan_path.exists():
        raise FileNotFoundError(f"Scan folder does not exist: {scan_path}")
    python = resolve_studio_python(studio_home)
    backend = studio_package_dir(studio_home) / "backend"
    env = os.environ.copy()
    env["UNSLOTH_STUDIO_HOME"] = str(studio_home)
    code = (
        "import sys\n"
        f"sys.path.insert(0, r'{backend}')\n"
        "from storage.studio_db import add_scan_folder, list_scan_folders\n"
        f"add_scan_folder(r'{scan_path}')\n"
        "print('scan folders:', [f['path'] for f in list_scan_folders()])\n"
    )
    completed = subprocess.run([str(python), "-c", code], env=env)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def install_unsloth_wrapper(studio_home: Path) -> None:
    user_profile = Path(os.environ.get("USERPROFILE") or Path.home())
    bin_dir = user_profile / ".local" / "bin"
    ensure_dir(bin_dir)
    cmd = (
        "@echo off\r\n"
        "rem unsloth launcher wrapper generated by win-models\r\n"
        "rem Sets UNSLOTH_STUDIO_HOME and forwards all args to the real unsloth CLI.\r\n"
        f'if not defined UNSLOTH_STUDIO_HOME set "UNSLOTH_STUDIO_HOME={studio_home}"\r\n'
        '"%UNSLOTH_STUDIO_HOME%\\unsloth_studio\\Scripts\\unsloth.exe" %*\r\n'
        "exit /b %ERRORLEVEL%\r\n"
    )
    (bin_dir / "unsloth.cmd").write_text(cmd, encoding="ascii", newline="")
    echo(f"  wrote {bin_dir}\\unsloth.cmd (ensure {bin_dir} is on PATH)")


def _replace_once(text: str, old: str, new: str, target: Path, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"Could not find {label} anchor in {target}; Studio changed.")
    return text.replace(old, new, 1)


def _write_text_if_changed(path: Path, text: str) -> bool:
    current = path.read_text(encoding="utf-8") if path.exists() else None
    if current == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return True


TAILWIND_SAFE_SOURCE_START = f"/* {TAILWIND_SAFE_SOURCE_SHIM_MARKER}_START */"
TAILWIND_SAFE_SOURCE_END = f"/* {TAILWIND_SAFE_SOURCE_SHIM_MARKER}_END */"
TAILWIND_SOURCE_IMPORT = '@import "tailwindcss" source("./");'
TAILWIND_STREAMDOWN_SOURCE = '@source "../node_modules/streamdown/dist/*.js";'
TAILWIND_STRING_RE = re.compile(
    r'"([^"\\]*(?:\\.[^"\\]*)*)"'
    r"|'([^'\\]*(?:\\.[^'\\]*)*)'"
    r"|`([^`\\]*(?:\\.[^`\\]*)*)`",
    re.DOTALL,
)
TAILWIND_COMMON_TOKENS = {
    "absolute",
    "antialiased",
    "block",
    "border",
    "capitalize",
    "contents",
    "container",
    "flex",
    "grid",
    "group",
    "hidden",
    "inline",
    "inline-block",
    "inline-flex",
    "invisible",
    "isolate",
    "italic",
    "not-sr-only",
    "peer",
    "relative",
    "resize",
    "resize-none",
    "rounded",
    "shadow",
    "sr-only",
    "static",
    "sticky",
    "table",
    "truncate",
    "visible",
}
TAILWIND_TOKEN_PREFIXES = (
    "*:",
    "-bottom-",
    "-inset-",
    "-left-",
    "-m-",
    "-mb-",
    "-ml-",
    "-mr-",
    "-mt-",
    "-mx-",
    "-my-",
    "-right-",
    "-rotate-",
    "-space-",
    "-top-",
    "-translate-",
    "-z-",
    "after:",
    "aria-",
    "aspect-",
    "backdrop-",
    "basis-",
    "before:",
    "bg-",
    "blur",
    "border-",
    "bottom-",
    "col-",
    "content-",
    "cursor-",
    "data-",
    "decoration-",
    "delay-",
    "divide-",
    "duration-",
    "ease-",
    "fill-",
    "flex-",
    "font-",
    "from-",
    "gap-",
    "grid-",
    "grow",
    "h-",
    "has-",
    "hover:",
    "inset-",
    "items-",
    "justify-",
    "leading-",
    "left-",
    "line-",
    "m-",
    "max-",
    "mb-",
    "me-",
    "min-",
    "ml-",
    "mr-",
    "ms-",
    "mt-",
    "mx-",
    "my-",
    "object-",
    "opacity-",
    "origin-",
    "outline-",
    "overflow-",
    "overscroll-",
    "p-",
    "pb-",
    "pe-",
    "place-",
    "pl-",
    "pr-",
    "ps-",
    "pt-",
    "px-",
    "py-",
    "ring-",
    "right-",
    "rotate-",
    "rounded-",
    "scale-",
    "shadow-",
    "shrink",
    "size-",
    "space-",
    "stroke-",
    "text-",
    "to-",
    "top-",
    "tracking-",
    "transition",
    "translate-",
    "underline",
    "via-",
    "w-",
    "z-",
)


def _balanced_tailwind_token(token: str) -> bool:
    pairs = (("[", "]"), ("(", ")"))
    for open_, close in pairs:
        if token.count(open_) != token.count(close):
            return False
    return True


def _looks_like_tailwind_token(token: str) -> bool:
    if len(token) < 2 or len(token) > 260:
        return False
    if any(ch.isspace() for ch in token):
        return False
    if any(ch in token for ch in ("$", "{", "}", "`", ";", "\\", '"')):
        return False
    if token.startswith(("'", "(", ")", "http:", "https:", "data:", "/", "#", ".", "--", "<")):
        return False
    if token.startswith("@") and not token.startswith(("@container", "@sm", "@md", "@lg", "@xl", "@2xl")):
        return False
    if token.endswith((":", ".", "?")):
        return False
    if "<" in token:
        return False
    if "=" in token and "[" not in token:
        return False
    if "?" in token or ")." in token or "=>" in token:
        return False
    if token[0].isdigit() and not re.match(r"^\d+xl:", token):
        return False
    if token.endswith("[]"):
        return False
    if re.search(r"[A-Z]", token) and not token.startswith("["):
        return False
    if not _balanced_tailwind_token(token):
        return False

    prefix, sep, base = token.rpartition(":")
    if base.startswith("!"):
        base = base[1:]
    if base.endswith("!"):
        base = base[:-1]
    check_token = f"{prefix}{sep}{base}" if sep else base

    if check_token.startswith("[") or ":[" in check_token:
        return True
    if check_token in TAILWIND_COMMON_TOKENS:
        return True
    if check_token.startswith(TAILWIND_TOKEN_PREFIXES):
        return True
    return base in TAILWIND_COMMON_TOKENS or base.startswith(TAILWIND_TOKEN_PREFIXES)


def _iter_source_string_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for match in TAILWIND_STRING_RE.finditer(text):
        value = next(group for group in match.groups() if group is not None)
        value = value.replace("\\n", " ").replace("\\t", " ")
        for raw in re.split(r"\s+", value):
            token = raw.strip().strip(",")
            for _ in range(2):
                token = token.strip("{}")
                token = token.strip("\"'`()")
                token = token.strip(",")
            if _looks_like_tailwind_token(token):
                tokens.add(token)
    return tokens


def _tailwind_inline_escape(token: str) -> str:
    return token.replace("\\", "\\\\").replace('"', '\\"')


def _chunk_tailwind_tokens(tokens: list[str], *, max_chars: int = 3500) -> list[list[str]]:
    chunks: list[list[str]] = []
    current: list[str] = []
    current_len = 0
    for token in tokens:
        next_len = len(token) + (1 if current else 0)
        if current and current_len + next_len > max_chars:
            chunks.append(current)
            current = [token]
            current_len = len(token)
            continue
        current.append(token)
        current_len += next_len
    if current:
        chunks.append(current)
    return chunks


def _build_tailwind_safe_source_block(frontend: Path) -> str:
    src = frontend / "src"
    tokens: set[str] = set()
    for pattern in ("*.ts", "*.tsx", "*.js", "*.jsx"):
        for path in src.rglob(pattern):
            tokens.update(_iter_source_string_tokens(path.read_text(encoding="utf-8", errors="ignore")))
    # Keep the auth shell usable even if the extractor misses JSX literals that
    # are critical for login and first-run recovery.
    tokens.update({"h-20", "w-20", "max-w-sm", "object-contain", "mx-auto", "mb-2"})
    ordered = sorted(tokens)
    lines = [TAILWIND_SAFE_SOURCE_START + "\n"]
    for chunk in _chunk_tailwind_tokens([_tailwind_inline_escape(token) for token in ordered]):
        lines.append(f'@source inline("{" ".join(chunk)}");\n')
    lines.append(TAILWIND_SAFE_SOURCE_END + "\n")
    return "".join(lines)


def _apply_tailwind_safe_source_shim(frontend: Path) -> bool:
    target = frontend / "src" / "index.css"
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    text = text.replace('@import "tailwindcss";\n', TAILWIND_SOURCE_IMPORT + "\n")
    text = text.replace(TAILWIND_STREAMDOWN_SOURCE + "\n", "")
    anchor = '@plugin "@toolwind/corner-shape";\n'
    text = _replace_once(
        text,
        anchor,
        anchor + TAILWIND_STREAMDOWN_SOURCE + "\n",
        target,
        "Tailwind streamdown source anchor",
    )
    text = text.replace('@source inline("h-20 w-20 max-w-sm object-contain mx-auto mb-2");\n', "")
    block = _build_tailwind_safe_source_block(frontend)
    if TAILWIND_SAFE_SOURCE_START in text and TAILWIND_SAFE_SOURCE_END in text:
        start = text.index(TAILWIND_SAFE_SOURCE_START)
        end = text.index(TAILWIND_SAFE_SOURCE_END, start) + len(TAILWIND_SAFE_SOURCE_END)
        if end < len(text) and text[end] == "\n":
            end += 1
        new_text = text[:start] + block + text[end:]
    else:
        anchor = '@plugin "@toolwind/corner-shape";\n'
        new_text = _replace_once(text, anchor, anchor + block, target, "Tailwind plugin anchor")
    if new_text == target.read_text(encoding="utf-8").replace("\r\n", "\n"):
        return False
    target.write_text(new_text, encoding="utf-8", newline="\n")
    return True


def _apply_settings_dialog_layout_shim(frontend: Path) -> bool:
    target = frontend / "src" / "index.css"
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    marker = "UNSLOTH_SETTINGS_DIALOG_LAYOUT_SHIM"
    if marker in text:
        return False
    anchor = "\t/* Settings dialog: no shadow in dark mode. */\n"
    css = (
        f"\t/* {marker}: do not depend on Tailwind arbitrary max-width generation. */\n"
        "\t.settings-surface {\n"
        "\t\twidth: min(820px, calc(100vw - 2rem));\n"
        "\t\tmax-width: min(820px, calc(100vw - 2rem)) !important;\n"
        "\t}\n\n"
        "\t@media (max-width: 639px) {\n"
        "\t\t.settings-surface {\n"
        "\t\t\twidth: 100dvw !important;\n"
        "\t\t\tmax-width: none !important;\n"
        "\t\t\theight: 100dvh;\n"
        "\t\t\tborder-radius: 0;\n"
        "\t\t}\n"
        "\t}\n\n"
    )
    new_text = _replace_once(text, anchor, css + anchor, target, "settings dialog layout CSS")
    target.write_text(new_text, encoding="utf-8", newline="\n")
    return True


def _apply_sidebar_layout_fallback_shim(frontend: Path) -> bool:
    target = frontend / "src" / "index.css"
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    marker = "UNSLOTH_SIDEBAR_LAYOUT_FALLBACK_SHIM_V2"
    if marker in text:
        return False
    anchor = "\t/* Settings dialog: no shadow in dark mode. */\n"
    css = (
        f"\t/* {marker}: fallback for Tailwind custom-property width shorthand. */\n"
        "\t[data-slot=\"sidebar-wrapper\"] > [data-slot=\"sidebar\"][data-side=\"left\"] {\n"
        "\t\twidth: var(--sidebar-width);\n"
        "\t\tmin-width: var(--sidebar-width);\n"
        "\t\tflex-basis: var(--sidebar-width);\n"
        "\t}\n"
        "\t[data-slot=\"sidebar-wrapper\"] > [data-slot=\"sidebar\"][data-side=\"left\"][data-collapsible=\"icon\"] {\n"
        "\t\twidth: var(--sidebar-width-icon);\n"
        "\t\tmin-width: var(--sidebar-width-icon);\n"
        "\t\tflex-basis: var(--sidebar-width-icon);\n"
        "\t}\n"
        "\t[data-slot=\"sidebar-wrapper\"] > [data-slot=\"sidebar\"][data-side=\"left\"][data-collapsible=\"offcanvas\"] {\n"
        "\t\twidth: 0;\n"
        "\t\tmin-width: 0;\n"
        "\t\tflex-basis: 0;\n"
        "\t}\n"
        "\t[data-slot=\"sidebar\"][data-side=\"left\"] > [data-slot=\"sidebar-gap\"] {\n"
        "\t\twidth: 100%;\n"
        "\t\tmin-height: 100%;\n"
        "\t}\n"
        "\t[data-slot=\"sidebar\"][data-side=\"left\"] > [data-slot=\"sidebar-container\"] {\n"
        "\t\twidth: 100%;\n"
        "\t\tmax-width: 100%;\n"
        "\t\tz-index: 30;\n"
        "\t}\n\n"
    )
    new_text = _replace_once(text, anchor, css + anchor, target, "sidebar layout fallback CSS")
    target.write_text(new_text, encoding="utf-8", newline="\n")
    return True


def _apply_chat_layout_fallback_shim(frontend: Path) -> bool:
    target = frontend / "src" / "index.css"
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    marker = "UNSLOTH_CHAT_LAYOUT_FALLBACK_SHIM"
    if marker in text:
        return False
    anchor = "\t/* Settings dialog: no shadow in dark mode. */\n"
    css = (
        f"\t/* {marker}: fallback for Tailwind custom-property max-width shorthand. */\n"
        "\t.aui-thread-root {\n"
        "\t\t--thread-max-width: 48rem;\n"
        "\t\t--thread-content-max-width: calc(var(--thread-max-width) - 1.5rem);\n"
        "\t}\n"
        "\t.max-w-\\(--thread-max-width\\) {\n"
        "\t\tmax-width: var(--thread-max-width);\n"
        "\t}\n"
        "\t.max-w-\\(--thread-content-max-width\\) {\n"
        "\t\tmax-width: var(--thread-content-max-width);\n"
        "\t}\n"
        "\t.aui-thread-root .composer-footer-note {\n"
        "\t\tmax-width: var(--thread-max-width);\n"
        "\t\tmargin-left: auto;\n"
        "\t\tmargin-right: auto;\n"
        "\t}\n\n"
    )
    new_text = _replace_once(text, anchor, css + anchor, target, "chat layout fallback CSS")
    target.write_text(new_text, encoding="utf-8", newline="\n")
    return True


def _apply_asr_fallback_backend_schema_shim(studio_home: Path) -> bool:
    target = studio_package_dir(studio_home) / "backend" / "models" / "inference.py"
    if not target.exists():
        raise FileNotFoundError(f"Missing {target}")
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    if "audio_transcription_fallback_available" in text:
        return False
    audio_field = '    has_audio_input: bool = Field(False, description = "Whether model accepts audio input (ASR)")\n'
    fallback_field = (
        audio_field
        + f"    # {ASR_FALLBACK_SHIM_MARKER}: external ASR sidecar available for text-only models.\n"
        + '    audio_transcription_fallback_available: bool = Field(False, description = "Whether audio can be transcribed through an external ASR sidecar")\n'
    )
    text = text.replace(audio_field, fallback_field)
    target.write_text(text, encoding="utf-8", newline="\n")
    return True


def _apply_asr_fallback_backend_route_shim(studio_home: Path) -> bool:
    target = studio_package_dir(studio_home) / "backend" / "routes" / "inference.py"
    if not target.exists():
        raise FileNotFoundError(f"Missing {target}")
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    changed = False
    if "from pydantic import BaseModel" not in text:
        text = _replace_once(
            text,
            "from fastapi.responses import StreamingResponse, JSONResponse, Response\n",
            "from fastapi.responses import StreamingResponse, JSONResponse, Response\nfrom pydantic import BaseModel\n",
            target,
            "ASR frontend transcription endpoint Pydantic import",
        )
        changed = True
    if ASR_FALLBACK_SHIM_MARKER not in text:
        helper = f'''

def _audio_transcription_fallback_url() -> Optional[str]:
    # {ASR_FALLBACK_SHIM_MARKER}: Parakeet or another local ASR sidecar.
    url = (os.environ.get("UNSLOTH_ASR_FALLBACK_URL") or "").strip()
    return url.rstrip("/") if url else None


def _audio_transcription_fallback_available() -> bool:
    return _audio_transcription_fallback_url() is not None


def _gguf_native_audio_input_available(llama_backend) -> bool:
    return bool(getattr(llama_backend, "_has_audio_input", False)) and bool(
        getattr(llama_backend, "_mmproj_has_audio", False)
    )


def _prepare_audio_for_asr_wav(b64: str) -> str:
    """Return 16kHz mono WAV base64 for external ASR sidecars."""
    if b64.startswith("data:"):
        b64 = b64.split(",", 1)[1] if "," in b64 else ""
    raw = base64.b64decode(b64)
    arr, sr = _decode_audio_mono(raw)
    if sr != 16000:
        arr = _resample_mono_linear(arr, sr, 16000)
        sr = 16000
    return base64.b64encode(_mono_f32_to_wav_bytes(arr, sr)).decode("ascii")


async def _transcribe_audio_fallback(b64: str) -> str:
    url = _audio_transcription_fallback_url()
    if not url:
        raise _reject(
            400,
            "Audio provided but current model does not support native audio input, and no ASR fallback is configured.",
        )
    try:
        wav_b64 = await asyncio.to_thread(_prepare_audio_for_asr_wav, b64)
    except Exception as e:
        logger.warning("Audio decode for ASR fallback failed: %s", e, exc_info = True)
        raise _reject(400, "Could not decode the provided audio file for transcription.")
    try:
        async with httpx.AsyncClient(timeout = httpx.Timeout(180.0, connect = 5.0)) as client:
            resp = await client.post(
                f"{{url}}/transcribe",
                json = {{"audio_base64": wav_b64, "format": "wav"}},
            )
    except Exception as e:
        logger.warning("ASR fallback request failed: %s", e, exc_info = True)
        raise _reject(502, "Audio transcription fallback is not reachable.")
    if resp.status_code >= 400:
        detail = resp.text[:500]
        logger.warning("ASR fallback returned %s: %s", resp.status_code, detail)
        raise _reject(502, "Audio transcription fallback failed.")
    try:
        data = resp.json()
    except Exception:
        logger.warning("ASR fallback returned non-JSON response: %s", resp.text[:500])
        raise _reject(502, "Audio transcription fallback returned an invalid response.")
    transcript = str(data.get("text") or "").strip()
    if not transcript:
        raise _reject(422, "Audio transcription fallback returned an empty transcript.")
    return transcript


def _voice_transcript_prompt(existing: str, transcript: str) -> str:
    bridge = "Please respond to the attached voice message."
    existing = (existing or "").strip()
    transcript = transcript.strip()
    if existing and existing != bridge:
        return (
            f"{{existing}}\\n\\n"
            "The user also sent a voice message. Transcript:\\n"
            f"{{transcript}}"
        )
    return "The user sent a voice message. Transcript:\\n" + transcript


def _inject_audio_transcript(messages: list[dict], transcript: str) -> None:
    """Replace the newest user turn's bridge text with an ASR transcript."""
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, list):
            text_parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text_parts.append(str(part.get("text") or ""))
            msg["content"] = _voice_transcript_prompt("\\n".join(text_parts), transcript)
        else:
            msg["content"] = _voice_transcript_prompt(str(content or ""), transcript)
        return
'''
        text = _replace_once(
            text,
            "\ndef _inject_audio_part(messages: list[dict], audio_b64: str, audio_format: str) -> None:\n",
            helper + "\ndef _inject_audio_part(messages: list[dict], audio_b64: str, audio_format: str) -> None:\n",
            target,
            "ASR fallback helper anchor",
        )
        changed = True

    if "_gguf_native_audio_input_available" not in text:
        native_helper = '''

def _gguf_native_audio_input_available(llama_backend) -> bool:
    return bool(getattr(llama_backend, "_has_audio_input", False)) and bool(
        getattr(llama_backend, "_mmproj_has_audio", False)
    )

'''
        text = _replace_once(
            text,
            "\ndef _prepare_audio_for_asr_wav(b64: str) -> str:\n",
            native_helper + "def _prepare_audio_for_asr_wav(b64: str) -> str:\n",
            target,
            "ASR fallback native GGUF audio helper anchor",
        )
        changed = True

    if "class AudioTranscribeRequest(BaseModel)" not in text:
        transcribe_route = f'''

class AudioTranscribeRequest(BaseModel):
    audio_base64: str


class AudioTranscribeResponse(BaseModel):
    text: str


@router.post("/audio/transcribe", response_model = AudioTranscribeResponse)
async def transcribe_audio(payload: AudioTranscribeRequest):
    # {ASR_FALLBACK_SHIM_MARKER}: frontend pre-transcription for durable chat history.
    if not payload.audio_base64:
        raise _reject(400, "audio_base64 is required.")
    if len(payload.audio_base64) > _MAX_AUDIO_B64_CHARS:
        raise _reject(413, "Audio file is too large (max ~25 MB).")
    return AudioTranscribeResponse(text = await _transcribe_audio_fallback(payload.audio_base64))
'''
        text = _replace_once(
            text,
            '\n@router.post("/audio/generate")\n',
            transcribe_route + '\n@router.post("/audio/generate")\n',
            target,
            "ASR frontend transcription endpoint",
        )
        changed = True

    legacy_response_upgrades = [
        (
            '                    has_audio_input = getattr(llama_backend, "_has_audio_input", False),\n                    audio_transcription_fallback_available = _audio_transcription_fallback_available(),\n                    inference = inference_config,\n',
            '                    has_audio_input = _gguf_native_audio_input_available(llama_backend),\n                    audio_transcription_fallback_available = _audio_transcription_fallback_available(),\n                    inference = inference_config,\n',
        ),
        (
            "                has_audio_input = llama_backend._has_audio_input,\n                audio_transcription_fallback_available = _audio_transcription_fallback_available(),\n                inference = inference_config,\n",
            "                has_audio_input = _gguf_native_audio_input_available(llama_backend),\n                audio_transcription_fallback_available = _audio_transcription_fallback_available(),\n                inference = inference_config,\n",
        ),
        (
            '                has_audio_input = getattr(llama_backend, "_has_audio_input", False),\n                audio_transcription_fallback_available = _audio_transcription_fallback_available(),\n                loading = [],\n',
            '                has_audio_input = _gguf_native_audio_input_available(llama_backend),\n                audio_transcription_fallback_available = _audio_transcription_fallback_available(),\n                loading = [],\n',
        ),
    ]
    for old, new in legacy_response_upgrades:
        if old in text and new not in text:
            text = text.replace(old, new, 1)
            changed = True

    response_replacements = [
        (
            '                    has_audio_input = getattr(llama_backend, "_has_audio_input", False),\n                    inference = inference_config,\n',
            '                    has_audio_input = _gguf_native_audio_input_available(llama_backend),\n                    audio_transcription_fallback_available = _audio_transcription_fallback_available(),\n                    inference = inference_config,\n',
        ),
        (
            '                    has_audio_input = _model_info.get("has_audio_input", False),\n                    inference = inference_config,\n',
            '                    has_audio_input = _model_info.get("has_audio_input", False),\n                    audio_transcription_fallback_available = _audio_transcription_fallback_available(),\n                    inference = inference_config,\n',
        ),
        (
            "                has_audio_input = llama_backend._has_audio_input,\n                inference = inference_config,\n",
            "                has_audio_input = _gguf_native_audio_input_available(llama_backend),\n                audio_transcription_fallback_available = _audio_transcription_fallback_available(),\n                inference = inference_config,\n",
        ),
        (
            "            has_audio_input = config.has_audio_input,\n            inference = inference_config,\n",
            "            has_audio_input = config.has_audio_input,\n            audio_transcription_fallback_available = _audio_transcription_fallback_available(),\n            inference = inference_config,\n",
        ),
        (
            '                has_audio_input = getattr(llama_backend, "_has_audio_input", False),\n                loading = [],\n',
            '                has_audio_input = _gguf_native_audio_input_available(llama_backend),\n                audio_transcription_fallback_available = _audio_transcription_fallback_available(),\n                loading = [],\n',
        ),
        (
            "            has_audio_input = has_audio_input,\n            loading = list(getattr(backend, \"loading_models\", set())),\n",
            "            has_audio_input = has_audio_input,\n            audio_transcription_fallback_available = _audio_transcription_fallback_available(),\n            loading = list(getattr(backend, \"loading_models\", set())),\n",
        ),
    ]
    for old, new in response_replacements:
        if new in text:
            continue
        text = _replace_once(text, old, new, target, "ASR fallback response field")
        changed = True

    old_audio_block = """        audio_b64 = None
        audio_format = "wav"
        if payload.audio_base64:
            if not getattr(llama_backend, "_has_audio_input", False):
                raise _reject(
                    400,
                    "Audio provided but current GGUF model does not support audio input.",
                )
            if len(payload.audio_base64) > _MAX_AUDIO_B64_CHARS:
                raise _reject(413, "Audio file is too large (max ~25 MB).")
            try:
                audio_b64, audio_format = await asyncio.to_thread(
                    _prepare_audio_for_llama, payload.audio_base64
                )
            except Exception as e:
                logger.warning("Audio decode failed: %s", e, exc_info = True)
                raise _reject(400, "Could not decode the provided audio file.")
"""
    previous_audio_block = """        audio_b64 = None
        audio_format = "wav"
        audio_transcript = None
        if payload.audio_base64:
            if len(payload.audio_base64) > _MAX_AUDIO_B64_CHARS:
                raise _reject(413, "Audio file is too large (max ~25 MB).")
            if not getattr(llama_backend, "_has_audio_input", False):
                audio_transcript = await _transcribe_audio_fallback(payload.audio_base64)
            else:
                try:
                    audio_b64, audio_format = await asyncio.to_thread(
                        _prepare_audio_for_llama, payload.audio_base64
                    )
                except Exception as e:
                    logger.warning("Audio decode failed: %s", e, exc_info = True)
                    raise _reject(400, "Could not decode the provided audio file.")
"""
    new_audio_block = """        audio_b64 = None
        audio_format = "wav"
        audio_transcript = None
        if payload.audio_base64:
            if len(payload.audio_base64) > _MAX_AUDIO_B64_CHARS:
                raise _reject(413, "Audio file is too large (max ~25 MB).")
            native_gguf_audio = _gguf_native_audio_input_available(llama_backend)
            if not native_gguf_audio:
                audio_transcript = await _transcribe_audio_fallback(payload.audio_base64)
            else:
                try:
                    audio_b64, audio_format = await asyncio.to_thread(
                        _prepare_audio_for_llama, payload.audio_base64
                    )
                except Exception as e:
                    logger.warning("Audio decode failed: %s", e, exc_info = True)
                    raise _reject(400, "Could not decode the provided audio file.")
"""
    if new_audio_block not in text:
        if previous_audio_block in text:
            text = text.replace(previous_audio_block, new_audio_block, 1)
        else:
            text = _replace_once(text, old_audio_block, new_audio_block, target, "GGUF audio fallback block")
        changed = True

    old_inject = """        image_b64 = None
        if audio_b64:
            _inject_audio_part(gguf_messages, audio_b64, audio_format)
"""
    new_inject = """        image_b64 = None
        if audio_transcript:
            _inject_audio_transcript(gguf_messages, audio_transcript)
        elif audio_b64:
            _inject_audio_part(gguf_messages, audio_b64, audio_format)
"""
    if new_inject not in text:
        text = _replace_once(text, old_inject, new_inject, target, "GGUF audio transcript injection")
        changed = True

    if changed:
        target.write_text(text, encoding="utf-8", newline="\n")
    return changed


def _apply_asr_fallback_frontend_shim(frontend: Path) -> bool:
    changed = False

    runtime_types = frontend / "src" / "features" / "chat" / "types" / "runtime.ts"
    text = runtime_types.read_text(encoding="utf-8").replace("\r\n", "\n")
    if "hasVoiceInput?: boolean;" not in text:
        text = _replace_once(
            text,
            "  hasAudioInput?: boolean;\n",
            "  hasAudioInput?: boolean;\n  hasVoiceInput?: boolean;\n",
            runtime_types,
            "runtime voice input field",
        )
        runtime_types.write_text(text, encoding="utf-8", newline="\n")
        changed = True

    api_types = frontend / "src" / "features" / "chat" / "types" / "api.ts"
    text = api_types.read_text(encoding="utf-8").replace("\r\n", "\n")
    if "audio_transcription_fallback_available?: boolean;" not in text:
        text = text.replace(
            "  has_audio_input?: boolean;\n",
            "  has_audio_input?: boolean;\n  audio_transcription_fallback_available?: boolean;\n",
        )
        api_types.write_text(text, encoding="utf-8", newline="\n")
        changed = True

    chat_api = frontend / "src" / "features" / "chat" / "api" / "chat-api.ts"
    text = chat_api.read_text(encoding="utf-8").replace("\r\n", "\n")
    if "transcribeVoiceAudio" not in text:
        helper = """

export async function transcribeVoiceAudio(audioBase64: string): Promise<string> {
  const response = await authFetch("/api/inference/audio/transcribe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ audio_base64: audioBase64 }),
  });
  const data = await parseJsonOrThrow<{ text?: string }>(response);
  return (data.text ?? "").trim();
}
"""
        text = _replace_once(
            text,
            "\nexport async function getInferenceStatus(): Promise<InferenceStatusResponse> {\n",
            helper + "\nexport async function getInferenceStatus(): Promise<InferenceStatusResponse> {\n",
            chat_api,
            "voice transcription frontend API helper",
        )
        chat_api.write_text(text, encoding="utf-8", newline="\n")
        changed = True

    model_runtime = frontend / "src" / "features" / "chat" / "hooks" / "use-chat-model-runtime.ts"
    text = model_runtime.read_text(encoding="utf-8").replace("\r\n", "\n")
    replacements = [
        (
            "  has_audio_input?: boolean;\n}): string | undefined {\n",
            "  has_audio_input?: boolean;\n  audio_transcription_fallback_available?: boolean;\n}): string | undefined {\n",
        ),
        (
            "  if (model.has_audio_input) tags.push(\"Audio Input\");\n",
            "  if (model.has_audio_input) tags.push(\"Audio Input\");\n  else if (model.audio_transcription_fallback_available) tags.push(\"Voice via ASR\");\n",
        ),
        (
            "    !model.has_audio_input\n  )\n",
            "    !model.has_audio_input &&\n    !model.audio_transcription_fallback_available\n  )\n",
        ),
        (
            "  has_audio_input?: boolean;\n}): ChatModelSummary {\n",
            "  has_audio_input?: boolean;\n  audio_transcription_fallback_available?: boolean;\n}): ChatModelSummary {\n",
        ),
        (
            "    hasAudioInput: Boolean(model.has_audio_input),\n",
            "    hasAudioInput: Boolean(model.has_audio_input),\n    hasVoiceInput: Boolean(model.has_audio_input || model.audio_transcription_fallback_available),\n",
        ),
        (
            "    has_audio_input?: boolean;\n  },\n): void {\n",
            "    has_audio_input?: boolean;\n    audio_transcription_fallback_available?: boolean;\n  },\n): void {\n",
        ),
        (
            "    hasAudioInput: Boolean(resp.has_audio_input),\n",
            "    hasAudioInput: Boolean(resp.has_audio_input),\n    hasVoiceInput: Boolean(resp.has_audio_input || resp.audio_transcription_fallback_available),\n",
        ),
    ]
    for old, new in replacements:
        if new in text:
            continue
        text = _replace_once(text, old, new, model_runtime, "model runtime voice input")
        changed = True
    if changed:
        model_runtime.write_text(text, encoding="utf-8", newline="\n")

    status_sync = frontend / "src" / "features" / "chat" / "lib" / "apply-inference-status-to-store.ts"
    text = status_sync.read_text(encoding="utf-8").replace("\r\n", "\n")
    old = """  const store = useChatRuntimeStore.getState();
  if (store.models.some((model) => model.id === checkpointId)) {
    return;
  }
  const summary: ChatModelSummary = {
    id: checkpointId,
    name: status.active_model ?? checkpointId,
    isVision: status.is_vision ?? false,
    isLora: false,
    isGguf: status.is_gguf ?? false,
    isAudio: status.is_audio ?? false,
    audioType: status.audio_type ?? null,
    hasAudioInput: status.has_audio_input ?? false,
  };
  store.setModels([...store.models, summary]);
"""
    new = """  const store = useChatRuntimeStore.getState();
  const voiceFields = {
    isVision: status.is_vision ?? false,
    isGguf: status.is_gguf ?? false,
    isAudio: status.is_audio ?? false,
    audioType: status.audio_type ?? null,
    hasAudioInput: status.has_audio_input ?? false,
    hasVoiceInput: Boolean(
      status.has_audio_input || status.audio_transcription_fallback_available,
    ),
  };
  const idx = store.models.findIndex((model) => model.id === checkpointId);
  if (idx !== -1) {
    const next = [...store.models];
    next[idx] = { ...next[idx], ...voiceFields };
    store.setModels(next);
    return;
  }
  const summary: ChatModelSummary = {
    id: checkpointId,
    name: status.active_model ?? checkpointId,
    isLora: false,
    ...voiceFields,
  };
  store.setModels([...store.models, summary]);
"""
    if new not in text:
        text = _replace_once(text, old, new, status_sync, "status voice capability sync")
        status_sync.write_text(text, encoding="utf-8", newline="\n")
        changed = True

    audio_adapter = frontend / "src" / "features" / "chat" / "audio-attachment-adapter.ts"
    text = audio_adapter.read_text(encoding="utf-8").replace("\r\n", "\n")
    if 'import { transcribeVoiceAudio } from "./api/chat-api";\n' not in text:
        text = _replace_once(
            text,
            'import { toast } from "sonner";\n',
            'import { toast } from "sonner";\nimport { transcribeVoiceAudio } from "./api/chat-api";\nimport { buildVoiceMessageText } from "./native-audio-recorder";\n',
            audio_adapter,
            "audio adapter transcription imports",
        )
        changed = True
    replacements = [
        (
            "    } else if (!activeModel?.hasAudioInput) {\n",
            "    } else if (!activeModel?.hasVoiceInput) {\n",
        ),
        (
            "      unavailableReason = `${label} cannot accept audio. Load an audio-input model before attaching audio files.`;\n",
            "      unavailableReason = `${label} cannot accept voice input. Load an audio-input model or enable the ASR fallback before attaching audio files.`;\n",
        ),
    ]
    for old, new in replacements:
        if new in text:
            continue
        text = _replace_once(text, old, new, audio_adapter, "audio adapter voice gate")
        changed = True
    old_send = """  async send(attachment: PendingAttachment): Promise<CompleteAttachment> {
    try {
      const base64 = await fileToBase64(attachment.file);
      // Backend takes raw base64; format only satisfies the part type.
      const format = attachment.contentType === "audio/mpeg" ? "mp3" : "wav";
      return {
        id: attachment.id,
        type: "file",
        name: attachment.name,
        contentType: attachment.contentType,
        content: [{ type: "audio", audio: { data: base64, format } }],
        status: { type: "complete" },
      };
    } finally {
      this.attachmentIds.delete(attachment.id);
    }
  }
"""
    new_send = """  async send(attachment: PendingAttachment): Promise<CompleteAttachment> {
    try {
      const base64 = await fileToBase64(attachment.file);
      const state = useChatRuntimeStore.getState();
      const checkpoint = state.params.checkpoint;
      const activeModel = state.models.find((m) => m.id === checkpoint);
      if (activeModel?.hasAudioInput) {
        // Backend takes raw base64; format only satisfies the part type.
        const format = attachment.contentType === "audio/mpeg" ? "mp3" : "wav";
        return {
          id: attachment.id,
          type: "file",
          name: attachment.name,
          contentType: attachment.contentType,
          content: [{ type: "audio", audio: { data: base64, format } }],
          status: { type: "complete" },
        };
      }
      const transcript = await transcribeVoiceAudio(base64);
      const promptText = useChatRuntimeStore.getState().voiceMessagePromptText;
      const text = buildVoiceMessageText({ transcript, promptText });
      return {
        id: attachment.id,
        type: "file",
        name: attachment.name,
        contentType: "text/plain",
        content: [{ type: "text", text }],
        status: { type: "complete" },
      };
    } finally {
      this.attachmentIds.delete(attachment.id);
    }
  }
"""
    if old_send in text:
        text = text.replace(old_send, new_send, 1)
        changed = True
    current_asr_send = """  async send(attachment: PendingAttachment): Promise<CompleteAttachment> {
    try {
      const base64 = await fileToBase64(attachment.file);
      const transcript = await transcribeVoiceAudio(base64);
      const promptText = useChatRuntimeStore.getState().voiceMessagePromptText;
      const text = buildVoiceMessageText({ transcript, promptText });
      return {
        id: attachment.id,
        type: "file",
        name: attachment.name,
        contentType: "text/plain",
        content: [{ type: "text", text }],
        status: { type: "complete" },
      };
    } finally {
      this.attachmentIds.delete(attachment.id);
    }
  }
"""
    if current_asr_send in text:
        text = text.replace(current_asr_send, new_send, 1)
        changed = True
    if changed:
        audio_adapter.write_text(text, encoding="utf-8", newline="\n")

    thread = frontend / "src" / "components" / "assistant-ui" / "thread.tsx"
    text = thread.read_text(encoding="utf-8").replace("\r\n", "\n")
    replacements = [
        (
            "    if (!checkpoint) return \"Load an audio-input model before recording audio.\";\n",
            "    if (!checkpoint) return \"Load a model before recording audio.\";\n",
        ),
        (
            "    if (model?.hasAudioInput) return null;\n",
            "    if (model?.hasVoiceInput) return null;\n",
        ),
        (
            "    return `${label} does not expose audio input. Load a model with an audio-capable mmproj.`;\n",
            "    return `${label} cannot accept voice input. Load an audio-input model or enable the ASR fallback.`;\n",
        ),
        (
            "            tooltip={recorder.status === \"processing\" ? \"Preparing voice message...\" : audioInputUnavailableReason ?? `Record native audio (max ${recorder.secondsLimit}s)`}\n",
            "            tooltip={recorder.status === \"processing\" ? \"Preparing voice message...\" : audioInputUnavailableReason ?? `Record voice message (max ${recorder.secondsLimit}s)`}\n",
        ),
        (
            "            aria-label=\"Record native audio message\"\n",
            "            aria-label=\"Record voice message\"\n",
        ),
        (
            "            aria-label=\"Stop native audio recording\"\n",
            "            aria-label=\"Stop voice recording\"\n",
        ),
    ]
    for old, new in replacements:
        if new in text:
            continue
        text = text.replace(old, new)
        changed = True
    thread.write_text(text, encoding="utf-8", newline="\n")

    shared = frontend / "src" / "features" / "chat" / "shared-composer.tsx"
    text = shared.read_text(encoding="utf-8").replace("\r\n", "\n")
    replacements = [
        (
            "    ? \"Load an audio-input model before recording audio.\"\n",
            "    ? \"Load a model before recording audio.\"\n",
        ),
        (
            "    : activeModel?.hasAudioInput\n",
            "    : activeModel?.hasVoiceInput\n",
        ),
        (
            "      : `${activeModel?.name || \"Current model\"} does not expose audio input. Load a model with an audio-capable mmproj.`;\n",
            "      : `${activeModel?.name || \"Current model\"} cannot accept voice input. Load an audio-input model or enable the ASR fallback.`;\n",
        ),
        (
            "                tooltip={nativeRecorder.status === \"processing\" ? \"Preparing voice message...\" : nativeAudioUnavailableReason ?? `Record native audio (max ${nativeRecorder.secondsLimit}s)`}\n",
            "                tooltip={nativeRecorder.status === \"processing\" ? \"Preparing voice message...\" : nativeAudioUnavailableReason ?? `Record voice message (max ${nativeRecorder.secondsLimit}s)`}\n",
        ),
        (
            "                aria-label=\"Record native audio message\"\n",
            "                aria-label=\"Record voice message\"\n",
        ),
        (
            "                aria-label=\"Stop native audio recording\"\n",
            "                aria-label=\"Stop voice recording\"\n",
        ),
        (
            "          hasAudioInput: Boolean(resp.has_audio_input),\n",
            "          hasAudioInput: Boolean(resp.has_audio_input),\n          hasVoiceInput: Boolean(resp.has_audio_input || resp.audio_transcription_fallback_available),\n",
        ),
        (
            "              {activeModel?.hasAudioInput && (\n",
            "              {activeModel?.hasVoiceInput && (\n",
        ),
    ]
    for old, new in replacements:
        if new in text:
            continue
        text = text.replace(old, new)
        changed = True
    shared.write_text(text, encoding="utf-8", newline="\n")

    return changed


def _apply_native_audio_backend_settings_shim(studio_home: Path) -> bool:
    target = studio_package_dir(studio_home) / "backend" / "routes" / "chat_history.py"
    if not target.exists():
        raise FileNotFoundError(f"Missing {target}")
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    changed = False
    if "voiceRecordingMaxSeconds" not in text:
        old = "    toolCallTimeout: Optional[int] = Field(default = None, ge = 1)\n"
        new = (
            old
            + "    # UNSLOTH_NATIVE_AUDIO_MIC_SHIM: browser mic recorder duration cap.\n"
            + "    voiceRecordingMaxSeconds: Optional[int] = Field(default = None, ge = 5, le = 600)\n"
        )
        text = _replace_once(text, old, new, target, "chat settings schema")
        changed = True
    if "voiceMessagePromptText" not in text:
        old = "    voiceRecordingMaxSeconds: Optional[int] = Field(default = None, ge = 5, le = 600)\n"
        new = (
            old
            + "    # UNSLOTH_NATIVE_AUDIO_MIC_SHIM: optional text sent with voice turns.\n"
            + "    voiceMessagePromptText: Optional[str] = Field(default = None, max_length = 2000)\n"
        )
        text = _replace_once(text, old, new, target, "chat settings voice prompt schema")
        changed = True
    if changed:
        target.write_text(text, encoding="utf-8", newline="\n")
    return changed


def _apply_native_audio_frontend_api_shim(frontend: Path) -> bool:
    target = frontend / "src" / "features" / "chat" / "api" / "chat-settings-api.ts"
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    changed = False
    if "voiceRecordingMaxSeconds?: number;" not in text:
        old = "  toolCallTimeout?: number;\n"
        new = old + "  voiceRecordingMaxSeconds?: number;\n"
        text = _replace_once(text, old, new, target, "chat settings API type")
        changed = True
    if "voiceMessagePromptText?: string;" not in text:
        old = "  voiceRecordingMaxSeconds?: number;\n"
        new = old + "  voiceMessagePromptText?: string;\n"
        text = _replace_once(text, old, new, target, "chat settings voice prompt API type")
        changed = True
    if changed:
        target.write_text(text, encoding="utf-8", newline="\n")
    return changed


def _apply_audio_only_placeholder_chat_adapter_shim(frontend: Path) -> bool:
    target = frontend / "src" / "features" / "chat" / "api" / "chat-adapter.ts"
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    changed = False
    import_line = 'import { stripAudioOnlyMessagePlaceholder } from "../native-audio-recorder";\n'
    if import_line not in text:
        text = _replace_once(
            text,
            'import { useExternalProvidersStore } from "../stores/external-providers-store";\n',
            'import { useExternalProvidersStore } from "../stores/external-providers-store";\n'
            + import_line,
            target,
            "audio-only placeholder chat adapter import",
        )
        changed = True
    old_collect = """function collectTextParts(message: RunMessage): string[] {
  const textParts = message.content
    .filter((part) => part.type === "text")
    .map((part) => part.text);

  if ("attachments" in message && (message.attachments?.length ?? 0) > 0) {
    for (const attachment of message.attachments ?? []) {
      for (const part of attachment.content ?? []) {
        if (part.type === "text") {
          textParts.push(part.text);
        }
      }
    }
  }

  return textParts;
}
"""
    new_collect = """function collectTextParts(message: RunMessage): string[] {
  const textParts = message.content
    .filter((part) => part.type === "text")
    .map((part) => stripAudioOnlyMessagePlaceholder(part.text))
    .filter((text) => text.trim().length > 0);

  if ("attachments" in message && (message.attachments?.length ?? 0) > 0) {
    for (const attachment of message.attachments ?? []) {
      for (const part of attachment.content ?? []) {
        if (part.type === "text") {
          const text = stripAudioOnlyMessagePlaceholder(part.text);
          if (text.trim().length > 0) textParts.push(text);
        }
      }
    }
  }

  return textParts;
}
"""
    if old_collect in text:
        text = text.replace(old_collect, new_collect, 1)
        changed = True
    if changed:
        target.write_text(text, encoding="utf-8", newline="\n")
    return changed


def _apply_native_audio_settings_storage_shim(frontend: Path) -> bool:
    target = frontend / "src" / "features" / "chat" / "utils" / "chat-settings-storage.ts"
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    changed = False
    if "sanitizeIntRange" not in text:
        old = (
            "function sanitizeInt(value: unknown, min: number): number | undefined {\n"
            "  return typeof value === \"number\" && Number.isInteger(value) && value >= min\n"
            "    ? value\n"
            "    : undefined;\n"
            "}\n"
        )
        new = (
            old
            + "\n"
            + "function sanitizeIntRange(\n"
            + "  value: unknown,\n"
            + "  min: number,\n"
            + "  max: number,\n"
            + "): number | undefined {\n"
            + "  return typeof value === \"number\" &&\n"
            + "    Number.isInteger(value) &&\n"
            + "    value >= min &&\n"
            + "    value <= max\n"
            + "    ? value\n"
            + "    : undefined;\n"
            + "}\n"
        )
        text = _replace_once(text, old, new, target, "sanitizeInt helper")
        changed = True
    if "function sanitizeString" not in text:
        old = (
            "function sanitizeIntRange(\n"
            "  value: unknown,\n"
            "  min: number,\n"
            "  max: number,\n"
            "): number | undefined {\n"
            "  return typeof value === \"number\" &&\n"
            "    Number.isInteger(value) &&\n"
            "    value >= min &&\n"
            "    value <= max\n"
            "    ? value\n"
            "    : undefined;\n"
            "}\n"
        )
        new = (
            old
            + "\n"
            + "function sanitizeString(value: unknown, maxLength: number): string | undefined {\n"
            + "  return typeof value === \"string\" && value.length <= maxLength\n"
            + "    ? value\n"
            + "    : undefined;\n"
            + "}\n"
        )
        text = _replace_once(text, old, new, target, "sanitizeString helper")
        changed = True
    if "voiceRecordingMaxSeconds" not in text:
        text = _replace_once(
            text,
            "  const toolCallTimeout = sanitizeInt(value.toolCallTimeout, 1);\n",
            (
                "  const toolCallTimeout = sanitizeInt(value.toolCallTimeout, 1);\n"
                "  const voiceRecordingMaxSeconds = sanitizeIntRange(\n"
                "    value.voiceRecordingMaxSeconds,\n"
                "    5,\n"
                "    600,\n"
                "  );\n"
            ),
            target,
            "sanitize chat settings voice field",
        )
        text = _replace_once(
            text,
            "  if (toolCallTimeout !== undefined) settings.toolCallTimeout = toolCallTimeout;\n",
            (
                "  if (toolCallTimeout !== undefined) settings.toolCallTimeout = toolCallTimeout;\n"
                "  if (voiceRecordingMaxSeconds !== undefined) {\n"
                "    settings.voiceRecordingMaxSeconds = voiceRecordingMaxSeconds;\n"
                "  }\n"
            ),
            target,
            "persist voice setting",
        )
        text = _replace_once(
            text,
            "    settings.toolCallTimeout === undefined\n",
            (
                "    settings.toolCallTimeout === undefined &&\n"
                "    settings.voiceRecordingMaxSeconds === undefined\n"
            ),
            target,
            "empty settings voice field",
        )
        changed = True
    if "voiceMessagePromptText" not in text:
        text = _replace_once(
            text,
            "  const toolCallTimeout = sanitizeInt(value.toolCallTimeout, 1);\n",
            (
                "  const toolCallTimeout = sanitizeInt(value.toolCallTimeout, 1);\n"
                "  const voiceMessagePromptText = sanitizeString(\n"
                "    value.voiceMessagePromptText,\n"
                "    2000,\n"
                "  );\n"
            ),
            target,
            "sanitize chat settings voice prompt field",
        )
        text = _replace_once(
            text,
            "  if (toolCallTimeout !== undefined) settings.toolCallTimeout = toolCallTimeout;\n",
            (
                "  if (toolCallTimeout !== undefined) settings.toolCallTimeout = toolCallTimeout;\n"
                "  if (voiceMessagePromptText !== undefined) {\n"
                "    settings.voiceMessagePromptText = voiceMessagePromptText;\n"
                "  }\n"
            ),
            target,
            "persist voice prompt setting",
        )
        text = _replace_once(
            text,
            "    settings.toolCallTimeout === undefined &&\n",
            (
                "    settings.toolCallTimeout === undefined &&\n"
                "    settings.voiceMessagePromptText === undefined &&\n"
            ),
            target,
            "empty settings voice prompt field",
        )
        changed = True
    if changed:
        target.write_text(text, encoding="utf-8", newline="\n")
    return changed


def _apply_native_audio_runtime_store_shim(frontend: Path) -> bool:
    target = frontend / "src" / "features" / "chat" / "stores" / "chat-runtime-store.ts"
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    changed = False
    if "  voiceRecordingMaxSeconds: 30,\n" in text:
        text = text.replace(
            "  voiceRecordingMaxSeconds: 30,\n",
            "  voiceRecordingMaxSeconds: 120,\n",
            1,
        )
        changed = True
    replacements = [
        (
            "  toolCallTimeout: number;\n",
            "  toolCallTimeout: number;\n  voiceRecordingMaxSeconds: number;\n",
            "store field",
        ),
        (
            "  setToolCallTimeout: (value: number) => void;\n",
            "  setToolCallTimeout: (value: number) => void;\n  setVoiceRecordingMaxSeconds: (value: number) => void;\n",
            "store setter type",
        ),
        (
            '  | "toolCallTimeout";\n',
            '  | "toolCallTimeout"\n  | "voiceRecordingMaxSeconds";\n',
            "scalar setting key",
        ),
        (
            '  "toolCallTimeout",\n',
            '  "toolCallTimeout",\n  "voiceRecordingMaxSeconds",\n',
            "scalar setting list",
        ),
        (
            "  toolCallTimeout: 5,\n",
            "  toolCallTimeout: 5,\n  voiceRecordingMaxSeconds: 120,\n",
            "default voice setting",
        ),
        (
            "      return { toolCallTimeout };\n    }),\n",
            (
                "      return { toolCallTimeout };\n"
                "    }),\n"
                "  setVoiceRecordingMaxSeconds: (voiceRecordingMaxSeconds) =>\n"
                "    set((state) => {\n"
                "      const next = Math.min(600, Math.max(5, Math.round(voiceRecordingMaxSeconds)));\n"
                "      setScalarSettingVersion(\n"
                "        \"voiceRecordingMaxSeconds\",\n"
                "        next,\n"
                "        state.voiceRecordingMaxSeconds,\n"
                "      );\n"
                "      return { voiceRecordingMaxSeconds: next };\n"
                "    }),\n"
            ),
            "voice setter implementation",
        ),
    ]
    for old, new, label in replacements:
        if new in text:
            continue
        text = _replace_once(text, old, new, target, label)
        changed = True
    prompt_replacements = [
        (
            "  toolCallTimeout: number;\n  voiceRecordingMaxSeconds: number;\n",
            "  toolCallTimeout: number;\n  voiceRecordingMaxSeconds: number;\n  voiceMessagePromptText: string;\n",
            "store voice prompt field",
        ),
        (
            "  setToolCallTimeout: (value: number) => void;\n  setVoiceRecordingMaxSeconds: (value: number) => void;\n",
            "  setToolCallTimeout: (value: number) => void;\n  setVoiceRecordingMaxSeconds: (value: number) => void;\n  setVoiceMessagePromptText: (value: string) => void;\n",
            "store voice prompt setter type",
        ),
        (
            '  | "toolCallTimeout"\n  | "voiceRecordingMaxSeconds";\n',
            '  | "toolCallTimeout"\n  | "voiceRecordingMaxSeconds"\n  | "voiceMessagePromptText";\n',
            "scalar voice prompt setting key",
        ),
        (
            '  "toolCallTimeout",\n  "voiceRecordingMaxSeconds",\n',
            '  "toolCallTimeout",\n  "voiceRecordingMaxSeconds",\n  "voiceMessagePromptText",\n',
            "scalar voice prompt setting list",
        ),
        (
            "  toolCallTimeout: 5,\n  voiceRecordingMaxSeconds: 120,\n",
            "  toolCallTimeout: 5,\n  voiceRecordingMaxSeconds: 120,\n  voiceMessagePromptText: \"\",\n",
            "default voice prompt setting",
        ),
        (
            "      return { voiceRecordingMaxSeconds: next };\n    }),\n",
            (
                "      return { voiceRecordingMaxSeconds: next };\n"
                "    }),\n"
                "  setVoiceMessagePromptText: (voiceMessagePromptText) =>\n"
                "    set((state) => {\n"
                "      const next = voiceMessagePromptText.slice(0, 2000);\n"
                "      setScalarSettingVersion(\n"
                "        \"voiceMessagePromptText\",\n"
                "        next,\n"
                "        state.voiceMessagePromptText,\n"
                "      );\n"
                "      return { voiceMessagePromptText: next };\n"
                "    }),\n"
            ),
            "voice prompt setter implementation",
        ),
    ]
    for old, new, label in prompt_replacements:
        if new in text:
            continue
        text = _replace_once(text, old, new, target, label)
        changed = True
    if changed:
        target.write_text(text, encoding="utf-8", newline="\n")
    return changed


def _apply_native_audio_settings_panel_shim(frontend: Path) -> bool:
    target = frontend / "src" / "features" / "chat" / "chat-settings-sheet.tsx"
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    changed = False
    if "max={32768}" in text:
        text = text.replace("max={32768}", "max={131072}", 1)
        changed = True
    if "RUN_SETTINGS_PANEL_WIDTH_STORAGE_KEY" not in text:
        old = "  const isMobile = useIsMobile();\n"
        new = """  const isMobile = useIsMobile();
  const RUN_SETTINGS_PANEL_WIDTH_STORAGE_KEY = "unsloth:run-settings-panel-width";
  const DEFAULT_RUN_SETTINGS_PANEL_WIDTH = 360;
  const MIN_RUN_SETTINGS_PANEL_WIDTH = 320;
  const MAX_RUN_SETTINGS_PANEL_WIDTH = 720;
  const clampRunSettingsPanelWidth = useCallback((value: number) => {
    const viewportLimit =
      typeof window === "undefined"
        ? MAX_RUN_SETTINGS_PANEL_WIDTH
        : Math.max(
            MIN_RUN_SETTINGS_PANEL_WIDTH,
            Math.min(MAX_RUN_SETTINGS_PANEL_WIDTH, window.innerWidth - 480),
          );
    return Math.max(
      MIN_RUN_SETTINGS_PANEL_WIDTH,
      Math.min(viewportLimit, Math.round(value)),
    );
  }, []);
  const [runSettingsPanelWidth, setRunSettingsPanelWidth] = useState(() => {
    if (typeof window === "undefined") return DEFAULT_RUN_SETTINGS_PANEL_WIDTH;
    const saved = Number.parseInt(
      window.localStorage.getItem(RUN_SETTINGS_PANEL_WIDTH_STORAGE_KEY) ?? "",
      10,
    );
    return clampRunSettingsPanelWidth(
      Number.isFinite(saved) ? saved : DEFAULT_RUN_SETTINGS_PANEL_WIDTH,
    );
  });
  const runSettingsPanelWidthRef = useRef(runSettingsPanelWidth);
  useEffect(() => {
    runSettingsPanelWidthRef.current = runSettingsPanelWidth;
  }, [runSettingsPanelWidth]);
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onResize = () => {
      setRunSettingsPanelWidth((current) =>
        clampRunSettingsPanelWidth(current),
      );
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [clampRunSettingsPanelWidth]);
"""
        text = _replace_once(text, old, new, target, "run settings resizable panel state")
        changed = True
    old_run_settings_width = '''    <aside
      className={`relative z-50 shrink-0 h-full overflow-hidden bg-panel-surface text-panel-surface-fg font-heading ${open ? "w-[17rem] border-l border-sidebar-border" : "w-0"}`}
    >
'''
    new_run_settings_width = '''    <aside
      className={`relative z-50 shrink-0 min-w-0 h-full overflow-hidden bg-panel-surface text-panel-surface-fg font-heading ${open ? "border-l border-sidebar-border" : ""}`}
      style={{
        width: open ? `${runSettingsPanelWidth}px` : "0rem",
        flexBasis: open ? `${runSettingsPanelWidth}px` : "0rem",
        maxWidth: open ? `${runSettingsPanelWidth}px` : "0rem",
      }}
    >
      {open ? (
        <div
          role="separator"
          aria-label="Resize run settings panel"
          aria-orientation="vertical"
          aria-valuemin={MIN_RUN_SETTINGS_PANEL_WIDTH}
          aria-valuemax={MAX_RUN_SETTINGS_PANEL_WIDTH}
          aria-valuenow={runSettingsPanelWidth}
          className="absolute left-0 top-0 z-20 h-full w-2 cursor-col-resize touch-none border-l border-transparent hover:border-primary/50"
          onPointerDown={(event) => {
            event.preventDefault();
            const startX = event.clientX;
            const startWidth = runSettingsPanelWidthRef.current;
            const previousCursor = document.body.style.cursor;
            const previousUserSelect = document.body.style.userSelect;
            document.body.style.cursor = "col-resize";
            document.body.style.userSelect = "none";
            const onPointerMove = (moveEvent: PointerEvent) => {
              const nextWidth = clampRunSettingsPanelWidth(
                startWidth + startX - moveEvent.clientX,
              );
              runSettingsPanelWidthRef.current = nextWidth;
              setRunSettingsPanelWidth(nextWidth);
            };
            const stopResize = () => {
              document.removeEventListener("pointermove", onPointerMove);
              document.removeEventListener("pointerup", stopResize);
              document.removeEventListener("pointercancel", stopResize);
              document.body.style.cursor = previousCursor;
              document.body.style.userSelect = previousUserSelect;
              window.localStorage.setItem(
                RUN_SETTINGS_PANEL_WIDTH_STORAGE_KEY,
                String(runSettingsPanelWidthRef.current),
              );
            };
            document.addEventListener("pointermove", onPointerMove);
            document.addEventListener("pointerup", stopResize);
            document.addEventListener("pointercancel", stopResize);
          }}
        />
      ) : null}
'''
    if old_run_settings_width in text:
        text = text.replace(old_run_settings_width, new_run_settings_width, 1)
        changed = True
    old_fixed_run_settings_width = '''    <aside
      className={`relative z-50 shrink-0 min-w-0 h-full overflow-hidden bg-panel-surface text-panel-surface-fg font-heading ${open ? "border-l border-sidebar-border" : ""}`}
      style={{
        width: open ? "17rem" : "0rem",
        flexBasis: open ? "17rem" : "0rem",
        maxWidth: open ? "17rem" : "0rem",
      }}
    >
'''
    if old_fixed_run_settings_width in text:
        text = text.replace(old_fixed_run_settings_width, new_run_settings_width, 1)
        changed = True
    if "VoiceRecordingLimitSlider" not in text:
        old = "        {!isExternalModel ? (\n          <CollapsibleSection label=\"Tools\">\n"
        new = (
            "        {!isExternalModel ? (\n"
            "          <CollapsibleSection label=\"Voice\">\n"
            "            <div className=\"flex flex-col gap-5 pt-1\">\n"
            "              <VoiceRecordingLimitSlider />\n"
            "              <VoiceMessagePromptTextArea />\n"
            "            </div>\n"
            "          </CollapsibleSection>\n"
            "        ) : null}\n\n"
            + old
        )
        text = _replace_once(text, old, new, target, "voice settings section")
        fn = """

function formatVoiceLimit(seconds: number): string {
  if (seconds < 60) return `${seconds} sec`;
  const minutes = seconds / 60;
  return Number.isInteger(minutes) ? `${minutes} min` : `${seconds} sec`;
}

function VoiceRecordingLimitSlider() {
  const limit = useChatRuntimeStore((s) => s.voiceRecordingMaxSeconds);
  const setLimit = useChatRuntimeStore((s) => s.setVoiceRecordingMaxSeconds);

  return (
    <ParamSlider
      label="Voice Recording Limit"
      value={limit}
      min={5}
      max={600}
      step={5}
      onChange={setLimit}
      displayValue={formatVoiceLimit(limit)}
      valueSize={8}
      info="Maximum browser microphone recording length for voice messages. Defaults to 2 minutes for ASR fallback; Gemma native audio is capped at 30 seconds."
    />
  );
}
"""
        text = _replace_once(text, "\nfunction ToolCallTimeoutSlider() {\n", fn + "\nfunction ToolCallTimeoutSlider() {\n", target, "voice slider function")
        changed = True
    voice_info = '      info="Maximum browser microphone recording length for voice messages. Defaults to 2 minutes for ASR fallback; Gemma native audio is capped at 30 seconds."\n'
    for old_info in (
        '      info="Maximum browser microphone recording length for native audio-input messages. Gemma defaults safely to 30 seconds; other models can be raised up to 10 minutes."\n',
        '      info="Maximum browser microphone recording length for voice messages. Defaults to 2 minutes for ASR fallback; can be raised up to 10 minutes."\n',
    ):
        if old_info in text and voice_info not in text:
            text = text.replace(old_info, voice_info, 1)
            changed = True
    if "              <VoiceMessagePromptTextArea />" not in text:
        text = _replace_once(
            text,
            "              <VoiceRecordingLimitSlider />\n",
            "              <VoiceRecordingLimitSlider />\n              <VoiceMessagePromptTextArea />\n",
            target,
            "voice prompt settings control",
        )
        changed = True
    if "function VoiceMessagePromptTextArea()" not in text:
        fn = """

function VoiceMessagePromptTextArea() {
  const promptText = useChatRuntimeStore((s) => s.voiceMessagePromptText);
  const setPromptText = useChatRuntimeStore((s) => s.setVoiceMessagePromptText);

  return (
    <div className="space-y-2">
      <div className="space-y-1">
        <div className="text-xs font-medium">Voice Message Prompt</div>
        <p className="text-[11px] leading-4 text-muted-foreground">
          Optional text sent along with voice turns. Leave blank to send only the
          transcript or native audio.
        </p>
      </div>
      <Textarea
        value={promptText}
        onChange={(event) => setPromptText(event.target.value)}
        placeholder="Optional instruction for voice-only turns..."
        fieldSizing="fixed"
        className="min-h-20 resize-y text-xs leading-5"
        rows={3}
      />
    </div>
  );
}
"""
        text = _replace_once(text, "\nfunction ToolCallTimeoutSlider() {\n", fn + "\nfunction ToolCallTimeoutSlider() {\n", target, "voice prompt settings function")
        changed = True
    if changed:
        target.write_text(text, encoding="utf-8", newline="\n")
    return changed


def _apply_gemma_recommended_preset_shim(frontend: Path) -> bool:
    target = frontend / "src" / "features" / "chat" / "presets" / "preset-policy.ts"
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    changed = False
    if '| "maxSeqLength"' not in text:
        text = _replace_once(
            text,
            '  | "presencePenalty"\n  | "maxTokens"\n',
            '  | "presencePenalty"\n  | "maxSeqLength"\n  | "maxTokens"\n',
            target,
            "preset-owned max sequence length",
        )
        changed = True
    if 'name: "Gemma Recommended"' not in text:
        old = """export const BUILTIN_PRESETS: Preset[] = [
  { name: "Default", params: { ...defaultInferenceParams } },
];
"""
        new = """export const GEMMA_RECOMMENDED_MAX_SEQ_LENGTH = 131072;

export const BUILTIN_PRESETS: Preset[] = [
  { name: "Default", params: { ...defaultInferenceParams } },
  {
    name: "Gemma Recommended",
    params: {
      ...defaultInferenceParams,
      temperature: 1.0,
      topP: 0.95,
      topK: 64,
      minP: 0.0,
      repetitionPenalty: 1.0,
      presencePenalty: 0.0,
      maxSeqLength: GEMMA_RECOMMENDED_MAX_SEQ_LENGTH,
    },
  },
];
"""
        text = _replace_once(text, old, new, target, "Gemma recommended preset")
        changed = True
    if "    maxSeqLength: params.maxSeqLength,\n" not in text:
        text = _replace_once(
            text,
            "    presencePenalty: params.presencePenalty,\n    maxTokens: params.maxTokens,\n",
            "    presencePenalty: params.presencePenalty,\n    maxSeqLength: params.maxSeqLength,\n    maxTokens: params.maxTokens,\n",
            target,
            "preset-owned max sequence length value",
        )
        changed = True
    if "    left.maxSeqLength === right.maxSeqLength &&\n" not in text:
        text = _replace_once(
            text,
            "    left.presencePenalty === right.presencePenalty &&\n    left.maxTokens === right.maxTokens &&\n",
            "    left.presencePenalty === right.presencePenalty &&\n    left.maxSeqLength === right.maxSeqLength &&\n    left.maxTokens === right.maxTokens &&\n",
            target,
            "preset max sequence length comparison",
        )
        changed = True
    if changed:
        target.write_text(text, encoding="utf-8", newline="\n")
    return changed


def _apply_native_audio_thread_shim(frontend: Path) -> bool:
    target = frontend / "src" / "components" / "assistant-ui" / "thread.tsx"
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    changed = False
    if 'from "@/features/chat/native-audio-recorder";' not in text:
        text = _replace_once(
            text,
            'import { sentAudioNames } from "@/features/chat/api/chat-adapter";\n',
            (
                'import { sentAudioNames } from "@/features/chat/api/chat-adapter";\n'
                'import { transcribeVoiceAudio } from "@/features/chat/api/chat-api";\n'
                'import { AUDIO_ONLY_MESSAGE_PLACEHOLDER, buildVoiceMessageText, getEffectiveVoiceRecordingMaxSeconds, isAudioOnlyMessagePlaceholderText, stripAudioOnlyMessagePlaceholder, useNativeAudioRecorder } from "@/features/chat/native-audio-recorder";\n'
            ),
            target,
            "native recorder import",
        )
        changed = True
    elif (
        'import { getEffectiveVoiceRecordingMaxSeconds, useNativeAudioRecorder } from "@/features/chat/native-audio-recorder";\n'
        not in text
        and 'import { useNativeAudioRecorder } from "@/features/chat/native-audio-recorder";\n' in text
    ):
        text = text.replace(
            'import { useNativeAudioRecorder } from "@/features/chat/native-audio-recorder";\n',
            'import { AUDIO_ONLY_MESSAGE_PLACEHOLDER, buildVoiceMessageText, getEffectiveVoiceRecordingMaxSeconds, isAudioOnlyMessagePlaceholderText, stripAudioOnlyMessagePlaceholder, useNativeAudioRecorder } from "@/features/chat/native-audio-recorder";\n',
            1,
        )
        changed = True
    if 'import { transcribeVoiceAudio } from "@/features/chat/api/chat-api";\n' not in text:
        text = _replace_once(
            text,
            'import { sentAudioNames } from "@/features/chat/api/chat-adapter";\n',
            'import { sentAudioNames } from "@/features/chat/api/chat-adapter";\nimport { transcribeVoiceAudio } from "@/features/chat/api/chat-api";\n',
            target,
            "thread transcription API import",
        )
        changed = True
    if (
        'import { AUDIO_ONLY_MESSAGE_PLACEHOLDER, buildVoiceMessageText, getEffectiveVoiceRecordingMaxSeconds, isAudioOnlyMessagePlaceholderText, stripAudioOnlyMessagePlaceholder, useNativeAudioRecorder } from "@/features/chat/native-audio-recorder";\n'
        not in text
        and 'import { getEffectiveVoiceRecordingMaxSeconds, useNativeAudioRecorder } from "@/features/chat/native-audio-recorder";\n' in text
    ):
        text = text.replace(
            'import { getEffectiveVoiceRecordingMaxSeconds, useNativeAudioRecorder } from "@/features/chat/native-audio-recorder";\n',
            'import { AUDIO_ONLY_MESSAGE_PLACEHOLDER, buildVoiceMessageText, getEffectiveVoiceRecordingMaxSeconds, isAudioOnlyMessagePlaceholderText, stripAudioOnlyMessagePlaceholder, useNativeAudioRecorder } from "@/features/chat/native-audio-recorder";\n',
            1,
        )
        changed = True
    old_thread_recorder_import = 'import { buildVoiceMessageText, getEffectiveVoiceRecordingMaxSeconds, useNativeAudioRecorder } from "@/features/chat/native-audio-recorder";\n'
    new_thread_recorder_import = 'import { AUDIO_ONLY_MESSAGE_PLACEHOLDER, buildVoiceMessageText, getEffectiveVoiceRecordingMaxSeconds, isAudioOnlyMessagePlaceholderText, stripAudioOnlyMessagePlaceholder, useNativeAudioRecorder } from "@/features/chat/native-audio-recorder";\n'
    if old_thread_recorder_import in text:
        text = text.replace(old_thread_recorder_import, new_thread_recorder_import, 1)
        changed = True
    if "  recordingDisabled?: boolean;\n" not in text:
        text = _replace_once(
            text,
            "  disabled?: boolean;\n  queueDisabled?: boolean;\n",
            "  disabled?: boolean;\n  recordingDisabled?: boolean;\n  queueDisabled?: boolean;\n",
            target,
            "thread recorder disabled prop",
        )
        changed = True
    if "  recordingDisabled,\n  queueDisabled,\n" not in text:
        text = _replace_once(
            text,
            "  disabled,\n  queueDisabled,\n",
            "  disabled,\n  recordingDisabled,\n  queueDisabled,\n",
            target,
            "thread recorder disabled destructure",
        )
        changed = True
    if "const recorder = useNativeAudioRecorder" not in text:
        old = (
            "  const isQueueRunning = Boolean(queueEntry);\n"
            "  return (\n"
        )
        new = (
            "  const isQueueRunning = Boolean(queueEntry);\n"
            "  const aui = useAui();\n"
            "  const voiceRecordingMaxSeconds = useChatRuntimeStore(\n"
            "    (s) => s.voiceRecordingMaxSeconds,\n"
            "  );\n"
            "  const activeVoiceModel = useChatRuntimeStore((s) => {\n"
            "    const checkpoint = s.params.checkpoint;\n"
            "    return checkpoint ? s.models.find((m) => m.id === checkpoint) : undefined;\n"
            "  });\n"
            "  const effectiveVoiceRecordingMaxSeconds = getEffectiveVoiceRecordingMaxSeconds(\n"
            "    voiceRecordingMaxSeconds,\n"
            "    activeVoiceModel,\n"
            "  );\n"
            "  const setPendingAudio = useChatRuntimeStore((s) => s.setPendingAudio);\n"
            "  const clearPendingAudio = useChatRuntimeStore((s) => s.clearPendingAudio);\n"
            "  const audioInputUnavailableReason = useChatRuntimeStore((s) => {\n"
            "    if (s.modelLoading) return \"Wait for model loading to finish before recording audio.\";\n"
            "    const checkpoint = s.params.checkpoint;\n"
            "    if (!checkpoint) return \"Load an audio-input model before recording audio.\";\n"
            "    const model = s.models.find((m) => m.id === checkpoint);\n"
            "    if (model?.hasAudioInput) return null;\n"
            "    const label = model?.name || checkpoint;\n"
            "    return `${label} does not expose audio input. Load a model with an audio-capable mmproj.`;\n"
            "  });\n"
            "  const audioInputEnabled = audioInputUnavailableReason === null;\n"
            "  const recorder = useNativeAudioRecorder(async (base64, name) => {\n"
            "    clearPendingAudio();\n"
    "    if (activeVoiceModel?.hasAudioInput) {\n"
    "      setPendingAudio(base64, name);\n"
    "      const currentText = aui.composer().getState().text;\n"
    "      const nextText = buildVoiceMessageText({\n"
    "        existingText: stripAudioOnlyMessagePlaceholder(currentText),\n"
    "        promptText: useChatRuntimeStore.getState().voiceMessagePromptText,\n"
    "      });\n"
            "      const sendText = nextText || AUDIO_ONLY_MESSAGE_PLACEHOLDER;\n"
            "      if (sendText !== currentText) aui.composer().setText(sendText);\n"
            "      return;\n"
            "    }\n"
            "    const transcript = await transcribeVoiceAudio(base64);\n"
            "    const currentText = aui.composer().getState().text;\n"
            "    const nextText = buildVoiceMessageText({\n"
            "      existingText: stripAudioOnlyMessagePlaceholder(currentText),\n"
            "      transcript,\n"
            "      promptText: useChatRuntimeStore.getState().voiceMessagePromptText,\n"
            "    });\n"
            "    if (nextText) aui.composer().setText(nextText);\n"
            "  }, effectiveVoiceRecordingMaxSeconds);\n"
            "  const recorderBusy = recorder.status === \"recording\" || recorder.status === \"processing\";\n"
            "  return (\n"
        )
        text = _replace_once(text, old, new, target, "thread recorder state")
        changed = True
    legacy_audio_gate = (
        "  const audioRecordingEnabled = useChatRuntimeStore((s) => {\n"
        "    const checkpoint = s.params.checkpoint;\n"
        "    if (!checkpoint || s.modelLoading) return false;\n"
        "    return Boolean(s.models.find((m) => m.id === checkpoint)?.hasAudioInput);\n"
        "  });\n"
    )
    if legacy_audio_gate in text:
        text = text.replace(legacy_audio_gate, "", 1)
        changed = True
    if (
        "  const isQueueRunning = Boolean(queueEntry);\n"
        "  const voiceRecordingMaxSeconds = useChatRuntimeStore(\n" in text
    ):
        text = text.replace(
            "  const isQueueRunning = Boolean(queueEntry);\n"
            "  const voiceRecordingMaxSeconds = useChatRuntimeStore(\n",
            "  const isQueueRunning = Boolean(queueEntry);\n"
            "  const aui = useAui();\n"
            "  const voiceRecordingMaxSeconds = useChatRuntimeStore(\n",
            1,
        )
        changed = True
    if (
        "const activeVoiceModel = useChatRuntimeStore" not in text
        and "const voiceRecordingMaxSeconds = useChatRuntimeStore" in text
    ):
        text = _replace_once(
            text,
            "  const voiceRecordingMaxSeconds = useChatRuntimeStore(\n"
            "    (s) => s.voiceRecordingMaxSeconds,\n"
            "  );\n",
            (
                "  const voiceRecordingMaxSeconds = useChatRuntimeStore(\n"
                "    (s) => s.voiceRecordingMaxSeconds,\n"
                "  );\n"
                "  const activeVoiceModel = useChatRuntimeStore((s) => {\n"
                "    const checkpoint = s.params.checkpoint;\n"
                "    return checkpoint ? s.models.find((m) => m.id === checkpoint) : undefined;\n"
                "  });\n"
                "  const effectiveVoiceRecordingMaxSeconds = getEffectiveVoiceRecordingMaxSeconds(\n"
                "    voiceRecordingMaxSeconds,\n"
                "    activeVoiceModel,\n"
                "  );\n"
            ),
            target,
            "thread effective voice recording limit",
        )
        changed = True
    if "}, voiceRecordingMaxSeconds);" in text and "effectiveVoiceRecordingMaxSeconds" in text:
        text = text.replace("}, voiceRecordingMaxSeconds);", "}, effectiveVoiceRecordingMaxSeconds);", 1)
        changed = True
    if "  const audioInputUnavailableReason = useChatRuntimeStore((s) => {\n" not in text:
        text = _replace_once(
            text,
            "  const clearPendingAudio = useChatRuntimeStore((s) => s.clearPendingAudio);\n"
            "  const recorder = useNativeAudioRecorder((base64, name) => {\n",
            (
                "  const clearPendingAudio = useChatRuntimeStore((s) => s.clearPendingAudio);\n"
                "  const audioInputUnavailableReason = useChatRuntimeStore((s) => {\n"
                "    if (s.modelLoading) return \"Wait for model loading to finish before recording audio.\";\n"
                "    const checkpoint = s.params.checkpoint;\n"
                "    if (!checkpoint) return \"Load an audio-input model before recording audio.\";\n"
                "    const model = s.models.find((m) => m.id === checkpoint);\n"
                "    if (model?.hasAudioInput) return null;\n"
                "    const label = model?.name || checkpoint;\n"
                "    return `${label} does not expose audio input. Load a model with an audio-capable mmproj.`;\n"
                "  });\n"
                "  const audioInputEnabled = audioInputUnavailableReason === null;\n"
                "  const recorder = useNativeAudioRecorder((base64, name) => {\n"
            ),
            target,
            "thread audio support state",
        )
        changed = True
    old_recorder_callback = """  const recorder = useNativeAudioRecorder((base64, name) => {
    clearPendingAudio();
    setPendingAudio(base64, name);
  }, effectiveVoiceRecordingMaxSeconds);
"""
    new_recorder_callback = """  const recorder = useNativeAudioRecorder(async (base64, name) => {
    clearPendingAudio();
    if (activeVoiceModel?.hasAudioInput) {
      setPendingAudio(base64, name);
      const currentText = aui.composer().getState().text;
      const nextText = buildVoiceMessageText({
        existingText: stripAudioOnlyMessagePlaceholder(currentText),
        promptText: useChatRuntimeStore.getState().voiceMessagePromptText,
      });
      const sendText = nextText || AUDIO_ONLY_MESSAGE_PLACEHOLDER;
      if (sendText !== currentText) aui.composer().setText(sendText);
      return;
    }
    const transcript = await transcribeVoiceAudio(base64);
    const currentText = aui.composer().getState().text;
    const nextText = buildVoiceMessageText({
      existingText: stripAudioOnlyMessagePlaceholder(currentText),
      transcript,
      promptText: useChatRuntimeStore.getState().voiceMessagePromptText,
    });
    if (nextText) aui.composer().setText(nextText);
  }, effectiveVoiceRecordingMaxSeconds);
"""
    if old_recorder_callback in text:
        text = text.replace(old_recorder_callback, new_recorder_callback, 1)
        changed = True
    old_native_recorder_history = """    if (activeVoiceModel?.hasAudioInput) {
      setPendingAudio(base64, name);
      const currentText = aui.composer().getState().text;
      const nextText = buildVoiceMessageText({
        existingText: currentText,
        promptText: useChatRuntimeStore.getState().voiceMessagePromptText,
      });
      if (nextText && nextText !== currentText) aui.composer().setText(nextText);
      return;
    }
"""
    new_native_recorder_history = """    if (activeVoiceModel?.hasAudioInput) {
      setPendingAudio(base64, name);
      const currentText = aui.composer().getState().text;
      const nextText = buildVoiceMessageText({
        existingText: stripAudioOnlyMessagePlaceholder(currentText),
        promptText: useChatRuntimeStore.getState().voiceMessagePromptText,
      });
      const sendText = nextText || AUDIO_ONLY_MESSAGE_PLACEHOLDER;
      if (sendText !== currentText) aui.composer().setText(sendText);
      return;
    }
"""
    if old_native_recorder_history in text:
        text = text.replace(old_native_recorder_history, new_native_recorder_history, 1)
        changed = True
    current_thread_native_recorder_history = """    if (activeVoiceModel?.hasAudioInput) {
      setPendingAudio(base64, name);
      const currentText = aui.composer().getState().text;
      let transcript: string | undefined;
      try {
        transcript = await transcribeVoiceAudio(base64);
      } catch (error) {
        console.warn("Voice transcription for chat history failed:", error);
        toast.error("Voice transcript failed; sending audio without saved transcript.");
      }
      const nextText = buildVoiceMessageText({
        existingText: currentText,
        transcript,
        promptText: useChatRuntimeStore.getState().voiceMessagePromptText,
      });
      if (nextText && nextText !== currentText) aui.composer().setText(nextText);
      return;
    }
"""
    if current_thread_native_recorder_history in text:
        text = text.replace(current_thread_native_recorder_history, new_native_recorder_history, 1)
        changed = True
    current_thread_asr_recorder_history = """    const transcript = await transcribeVoiceAudio(base64);
    const currentText = aui.composer().getState().text;
    const nextText = buildVoiceMessageText({
      existingText: currentText,
      transcript,
      promptText: useChatRuntimeStore.getState().voiceMessagePromptText,
    });
    if (nextText) aui.composer().setText(nextText);
"""
    updated_thread_asr_recorder_history = """    const transcript = await transcribeVoiceAudio(base64);
    const currentText = aui.composer().getState().text;
    const nextText = buildVoiceMessageText({
      existingText: stripAudioOnlyMessagePlaceholder(currentText),
      transcript,
      promptText: useChatRuntimeStore.getState().voiceMessagePromptText,
    });
    if (nextText) aui.composer().setText(nextText);
"""
    if current_thread_asr_recorder_history in text:
        text = text.replace(current_thread_asr_recorder_history, updated_thread_asr_recorder_history, 1)
        changed = True
    old_dictation = (
        "      <ComposerPrimitive.If dictation={false}>\n"
        "        <ComposerPrimitive.Dictate asChild={true}>\n"
        "          <TooltipIconButton\n"
        "            tooltip=\"Dictate\"\n"
        "            aria-label=\"Dictate\"\n"
        "            variant=\"ghost\"\n"
        "            className=\"size-8 rounded-full text-foreground\"\n"
        "          >\n"
        "            <MicIcon className=\"size-5\" />\n"
        "          </TooltipIconButton>\n"
        "        </ComposerPrimitive.Dictate>\n"
        "      </ComposerPrimitive.If>\n"
        "      <ComposerPrimitive.If dictation={true}>\n"
        "        <ComposerPrimitive.StopDictation asChild={true}>\n"
        "          <TooltipIconButton\n"
        "            tooltip=\"Stop dictation\"\n"
        "            aria-label=\"Stop dictation\"\n"
        "            variant=\"ghost\"\n"
        "            className=\"size-8 rounded-full text-destructive\"\n"
        "          >\n"
        "            <SquareIcon className=\"size-3 animate-pulse fill-current\" />\n"
        "          </TooltipIconButton>\n"
        "        </ComposerPrimitive.StopDictation>\n"
        "      </ComposerPrimitive.If>\n"
    )
    new_recorder = (
        "      {recorder.supported ? (\n"
        "        recorder.status === \"recording\" ? (\n"
        "          <TooltipIconButton\n"
        "            tooltip={`Stop recording (${recorder.secondsElapsed}/${recorder.secondsLimit}s)`}\n"
        "            aria-label=\"Stop native audio recording\"\n"
        "            variant=\"ghost\"\n"
        "            className=\"size-8 rounded-full text-destructive\"\n"
        "            onClick={recorder.stop}\n"
        "          >\n"
        "            <SquareIcon className=\"size-3 animate-pulse fill-current\" />\n"
        "          </TooltipIconButton>\n"
        "        ) : (\n"
        "          <TooltipIconButton\n"
        "            tooltip={recorder.status === \"processing\" ? \"Preparing voice message...\" : audioInputUnavailableReason ?? `Record native audio (max ${recorder.secondsLimit}s)`}\n"
        "            aria-label=\"Record native audio message\"\n"
        "            variant=\"ghost\"\n"
        "            className=\"size-8 rounded-full text-foreground\"\n"
        "            disabled={recordingDisabled || !audioInputEnabled || recorderBusy}\n"
        "            onClick={() => void recorder.start()}\n"
        "          >\n"
        "            {recorder.status === \"processing\" ? <Spinner className=\"size-4\" /> : <MicIcon className=\"size-5\" />}\n"
        "          </TooltipIconButton>\n"
        "        )\n"
        "      ) : null}\n"
    )
    if old_dictation in text:
        text = text.replace(old_dictation, new_recorder, 1)
        changed = True
    if "      {audioRecordingEnabled && recorder.supported ? (\n" in text:
        text = text.replace(
            "      {audioRecordingEnabled && recorder.supported ? (\n",
            "      {recorder.supported ? (\n",
            1,
        )
        changed = True
    if "            disabled={disabled || recorderBusy}\n" in text:
        text = text.replace(
            "            disabled={disabled || recorderBusy}\n",
            "            disabled={recordingDisabled || recorderBusy}\n",
            1,
        )
        changed = True
    if '            tooltip={recorder.status === "processing" ? "Preparing voice message..." : `Record native audio (max ${recorder.secondsLimit}s)`}\n' in text:
        text = text.replace(
            '            tooltip={recorder.status === "processing" ? "Preparing voice message..." : `Record native audio (max ${recorder.secondsLimit}s)`}\n',
            '            tooltip={recorder.status === "processing" ? "Preparing voice message..." : audioInputUnavailableReason ?? `Record native audio (max ${recorder.secondsLimit}s)`}\n',
            1,
        )
        changed = True
    if "            disabled={recordingDisabled || recorderBusy}\n" in text:
        text = text.replace(
            "            disabled={recordingDisabled || recorderBusy}\n",
            "            disabled={recordingDisabled || !audioInputEnabled || recorderBusy}\n",
            1,
        )
        changed = True
    if "          recordingDisabled={disabled || isComposing || hasPendingAttachments}\n" in text:
        text = text.replace(
            "          recordingDisabled={disabled || isComposing || hasPendingAttachments}\n",
            "          recordingDisabled={isComposing || hasPendingAttachments}\n",
            1,
        )
        changed = True
    if "          recordingDisabled={isComposing || hasPendingAttachments}\n" not in text:
        text = _replace_once(
            text,
            "          queueDisabled={!canQueueCurrentPrompt}\n",
            (
                "          recordingDisabled={isComposing || hasPendingAttachments}\n"
                "          queueDisabled={!canQueueCurrentPrompt}\n"
            ),
            target,
            "thread recorder disabled call prop",
        )
        changed = True
    if "  const ensureAudioOnlyComposerText = useCallback(() => {\n" not in text:
        text = _replace_once(
            text,
            (
                "  const canQueueCurrentPrompt =\n"
                "    composerText.trim().length > 0 &&\n"
                "    !hasAttachments &&\n"
                "    !hasPendingAudio &&\n"
                "    !isComposing &&\n"
                "    !hasPendingAttachments &&\n"
                "    !disabled &&\n"
                "    !overlay;\n"
            ),
            (
                "  const canQueueCurrentPrompt =\n"
                "    composerText.trim().length > 0 &&\n"
                "    !hasAttachments &&\n"
                "    !hasPendingAudio &&\n"
                "    !isComposing &&\n"
                "    !hasPendingAttachments &&\n"
                "    !disabled &&\n"
                "    !overlay;\n"
                "  const ensureAudioOnlyComposerText = useCallback(() => {\n"
                "    if (!hasPendingAudio || composerText.trim().length > 0 || hasAttachments) {\n"
                "      return;\n"
                "    }\n"
                "    const voicePrompt = useChatRuntimeStore.getState().voiceMessagePromptText.trim();\n"
                "    const sendText = voicePrompt || AUDIO_ONLY_MESSAGE_PLACEHOLDER;\n"
                "    flushResourcesSync(() => {\n"
                "      aui.composer().setText(sendText);\n"
                "    });\n"
                "  }, [aui, composerText, hasAttachments, hasPendingAudio]);\n"
            ),
            target,
            "thread audio-only composer text helper",
        )
        changed = True
    old_ensure = """  const ensureAudioOnlyComposerText = useCallback(() => {
    if (!hasPendingAudio || composerText.trim().length > 0 || hasAttachments) {
      return;
    }
    flushResourcesSync(() => {
      aui.composer().setText("Please respond to the attached voice message.");
    });
  }, [aui, composerText, hasAttachments, hasPendingAudio]);
"""
    new_ensure = """  const ensureAudioOnlyComposerText = useCallback(() => {
    if (!hasPendingAudio || composerText.trim().length > 0 || hasAttachments) {
      return;
    }
    const voicePrompt = useChatRuntimeStore.getState().voiceMessagePromptText.trim();
    const sendText = voicePrompt || AUDIO_ONLY_MESSAGE_PLACEHOLDER;
    flushResourcesSync(() => {
      aui.composer().setText(sendText);
    });
  }, [aui, composerText, hasAttachments, hasPendingAudio]);
"""
    if old_ensure in text:
        text = text.replace(old_ensure, new_ensure, 1)
        changed = True
    old_prompt_ensure = """  const ensureAudioOnlyComposerText = useCallback(() => {
    if (!hasPendingAudio || composerText.trim().length > 0 || hasAttachments) {
      return;
    }
    const voicePrompt = useChatRuntimeStore.getState().voiceMessagePromptText.trim();
    if (!voicePrompt) {
      return;
    }
    flushResourcesSync(() => {
      aui.composer().setText(voicePrompt);
    });
  }, [aui, composerText, hasAttachments, hasPendingAudio]);
"""
    if old_prompt_ensure in text:
        text = text.replace(old_prompt_ensure, new_ensure, 1)
        changed = True
    if "if (text.trim().length > 0 || attachments.length > 0) {\n      aui.composer().send();" in text:
        text = text.replace(
            "if (text.trim().length > 0 || attachments.length > 0) {\n      aui.composer().send();",
            "if (text.trim().length > 0 || attachments.length > 0 || useChatRuntimeStore.getState().pendingAudioBase64) {\n      aui.composer().send();",
            1,
        )
        changed = True
    if "      ensureAudioOnlyComposerText();\n      if (indexingActive && !overlay) {\n" not in text:
        text = _replace_once(
            text,
            "      if (indexingActive && !overlay) {\n",
            "      ensureAudioOnlyComposerText();\n      if (indexingActive && !overlay) {\n",
            target,
            "thread audio-only send text injection",
        )
        changed = True
    if "[disabled, shouldBlockSend, indexingActive, overlay, enqueueSend]," in text:
        text = text.replace(
            "[disabled, shouldBlockSend, indexingActive, overlay, enqueueSend],",
            "[disabled, shouldBlockSend, ensureAudioOnlyComposerText, indexingActive, overlay, enqueueSend],",
            1,
        )
        changed = True
    if "const UserMessageBody: FC = () => {" not in text:
        old_user_message_body = """const UserMessage: FC = () => {
  return (
    <MessagePrimitive.Root
      className="aui-user-message-root fade-in slide-in-from-bottom-1 mx-auto flex w-full max-w-(--thread-content-max-width) animate-in flex-col items-end gap-y-2 pt-6 pb-4 text-[15.5px] [font-weight:410] tracking-[0.01em] dark:tracking-[0.02em] duration-150"
      data-role="user"
    >
      <UserMessageAttachments />
      <UserMessageAudio />

      <div className="aui-user-message-content-wrapper flex max-w-[80%] min-w-0 flex-col items-end">
        <div className="aui-user-message-content wrap-break-word w-fit rounded-[24px] bg-[#f5f5f5] px-4 py-2.5 text-[#0d0d0d] dark:text-foreground dark:bg-card">
          <MessagePrimitive.Parts />
        </div>
        <div className="mt-1 -mr-[var(--icon-btn-inset)] flex min-h-8 items-center">
          <UserActionBar />
          <BranchPicker className="aui-user-branch-picker ml-0.5" />
        </div>
      </div>
    </MessagePrimitive.Root>
  );
};
"""
        new_user_message_body = """const UserMessageBody: FC = () => {
  const hasVisibleBody = useAuiState(({ message }) =>
    message.content.some((part) => {
      if (part.type !== "text") return true;
      return !isAudioOnlyMessagePlaceholderText(part.text);
    }),
  );
  if (!hasVisibleBody) {
    return null;
  }
  return (
    <div className="aui-user-message-content-wrapper flex max-w-[80%] min-w-0 flex-col items-end">
      <div className="aui-user-message-content wrap-break-word w-fit rounded-[24px] bg-[#f5f5f5] px-4 py-2.5 text-[#0d0d0d] dark:text-foreground dark:bg-card">
        <MessagePrimitive.Parts />
      </div>
      <div className="mt-1 -mr-[var(--icon-btn-inset)] flex min-h-8 items-center">
        <UserActionBar />
        <BranchPicker className="aui-user-branch-picker ml-0.5" />
      </div>
    </div>
  );
};

const UserMessage: FC = () => {
  return (
    <MessagePrimitive.Root
      className="aui-user-message-root fade-in slide-in-from-bottom-1 mx-auto flex w-full max-w-(--thread-content-max-width) animate-in flex-col items-end gap-y-2 pt-6 pb-4 text-[15.5px] [font-weight:410] tracking-[0.01em] dark:tracking-[0.02em] duration-150"
      data-role="user"
    >
      <UserMessageAttachments />
      <UserMessageAudio />
      <UserMessageBody />
    </MessagePrimitive.Root>
  );
};
"""
        if old_user_message_body in text:
            text = text.replace(old_user_message_body, new_user_message_body, 1)
            changed = True
    if changed:
        target.write_text(text, encoding="utf-8", newline="\n")
    return changed


def _apply_native_audio_shared_composer_shim(frontend: Path) -> bool:
    target = frontend / "src" / "features" / "chat" / "shared-composer.tsx"
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    changed = False
    if 'from "./native-audio-recorder";' not in text:
        text = _replace_once(
            text,
            'import { McpComposerButton } from "./mcp-composer-button";\n',
            (
                'import { McpComposerButton } from "./mcp-composer-button";\n'
                'import { AUDIO_ONLY_MESSAGE_PLACEHOLDER, buildVoiceMessageText, getEffectiveVoiceRecordingMaxSeconds, stripAudioOnlyMessagePlaceholder, useNativeAudioRecorder } from "./native-audio-recorder";\n'
            ),
            target,
            "shared recorder import",
        )
        changed = True
    elif (
        'import { getEffectiveVoiceRecordingMaxSeconds, useNativeAudioRecorder } from "./native-audio-recorder";\n'
        not in text
        and 'import { useNativeAudioRecorder } from "./native-audio-recorder";\n' in text
    ):
        text = text.replace(
            'import { useNativeAudioRecorder } from "./native-audio-recorder";\n',
            'import { AUDIO_ONLY_MESSAGE_PLACEHOLDER, buildVoiceMessageText, getEffectiveVoiceRecordingMaxSeconds, stripAudioOnlyMessagePlaceholder, useNativeAudioRecorder } from "./native-audio-recorder";\n',
            1,
        )
        changed = True
    if (
        'import { AUDIO_ONLY_MESSAGE_PLACEHOLDER, buildVoiceMessageText, getEffectiveVoiceRecordingMaxSeconds, stripAudioOnlyMessagePlaceholder, useNativeAudioRecorder } from "./native-audio-recorder";\n'
        not in text
        and 'import { getEffectiveVoiceRecordingMaxSeconds, useNativeAudioRecorder } from "./native-audio-recorder";\n' in text
    ):
        text = text.replace(
            'import { getEffectiveVoiceRecordingMaxSeconds, useNativeAudioRecorder } from "./native-audio-recorder";\n',
            'import { AUDIO_ONLY_MESSAGE_PLACEHOLDER, buildVoiceMessageText, getEffectiveVoiceRecordingMaxSeconds, stripAudioOnlyMessagePlaceholder, useNativeAudioRecorder } from "./native-audio-recorder";\n',
            1,
        )
        changed = True
    old_shared_recorder_import = 'import { buildVoiceMessageText, getEffectiveVoiceRecordingMaxSeconds, useNativeAudioRecorder } from "./native-audio-recorder";\n'
    new_shared_recorder_import = 'import { AUDIO_ONLY_MESSAGE_PLACEHOLDER, buildVoiceMessageText, getEffectiveVoiceRecordingMaxSeconds, stripAudioOnlyMessagePlaceholder, useNativeAudioRecorder } from "./native-audio-recorder";\n'
    if old_shared_recorder_import in text:
        text = text.replace(old_shared_recorder_import, new_shared_recorder_import, 1)
        changed = True
    if 'import { loadModel, transcribeVoiceAudio, validateModel } from "./api/chat-api";\n' not in text:
        if 'import { loadModel, validateModel } from "./api/chat-api";\n' in text:
            text = text.replace(
                'import { loadModel, validateModel } from "./api/chat-api";\n',
                'import { loadModel, transcribeVoiceAudio, validateModel } from "./api/chat-api";\n',
                1,
            )
            changed = True
    if 'import { Spinner } from "@/components/ui/spinner";' not in text:
        text = _replace_once(
            text,
            'import { Button } from "@/components/ui/button";\n',
            (
                'import { Button } from "@/components/ui/button";\n'
                'import { Spinner } from "@/components/ui/spinner";\n'
            ),
            target,
            "shared spinner import",
        )
        changed = True
    if "const voiceRecordingMaxSeconds = useChatRuntimeStore" not in text:
        old = (
            "  const clearPendingAudioStore = useChatRuntimeStore(\n"
            "    (s) => s.clearPendingAudio,\n"
            "  );\n"
        )
        new = (
            old
            + "  const voiceRecordingMaxSeconds = useChatRuntimeStore(\n"
            + "    (s) => s.voiceRecordingMaxSeconds,\n"
            + "  );\n"
            + "  const effectiveVoiceRecordingMaxSeconds = getEffectiveVoiceRecordingMaxSeconds(\n"
            + "    voiceRecordingMaxSeconds,\n"
            + "    activeModel,\n"
            + "  );\n"
            + "  const nativeRecorder = useNativeAudioRecorder(async (base64, name) => {\n"
            + "    if (activeModel?.hasAudioInput) {\n"
            + "      setPendingAudio({ name, base64 });\n"
            + "      setPendingAudioStore(base64, name);\n"
            + "      const nextText = buildVoiceMessageText({\n"
            + "        existingText: stripAudioOnlyMessagePlaceholder(text),\n"
            + "        promptText: useChatRuntimeStore.getState().voiceMessagePromptText,\n"
            + "      });\n"
            + "      const sendText = nextText || AUDIO_ONLY_MESSAGE_PLACEHOLDER;\n"
            + "      if (sendText !== text) setText(sendText);\n"
            + "      return;\n"
            + "    }\n"
            + "    const transcript = await transcribeVoiceAudio(base64);\n"
            + "    const nextText = buildVoiceMessageText({\n"
            + "      existingText: stripAudioOnlyMessagePlaceholder(text),\n"
            + "      transcript,\n"
            + "      promptText: useChatRuntimeStore.getState().voiceMessagePromptText,\n"
            + "    });\n"
            + "    if (nextText) setText(nextText);\n"
            + "  }, effectiveVoiceRecordingMaxSeconds);\n"
            + "  const nativeRecorderBusy =\n"
            + "    nativeRecorder.status === \"recording\" || nativeRecorder.status === \"processing\";\n"
            + "  const nativeAudioUnavailableReason = !modelLoaded\n"
            + "    ? \"Load an audio-input model before recording audio.\"\n"
            + "    : activeModel?.hasAudioInput\n"
            + "      ? null\n"
            + "      : `${activeModel?.name || \"Current model\"} does not expose audio input. Load a model with an audio-capable mmproj.`;\n"
            + "  const nativeRecordingEnabled =\n"
            + "    nativeRecorder.supported && nativeAudioUnavailableReason === null;\n"
        )
        text = _replace_once(text, old, new, target, "shared recorder state")
        changed = True
    if "  const nativeRecordingEnabled = Boolean(activeModel?.hasAudioInput);\n" in text:
        text = text.replace(
            "  const nativeRecordingEnabled = Boolean(activeModel?.hasAudioInput);\n",
            "",
            1,
        )
        changed = True
    if (
        "const effectiveVoiceRecordingMaxSeconds = getEffectiveVoiceRecordingMaxSeconds" not in text
        and "const voiceRecordingMaxSeconds = useChatRuntimeStore" in text
    ):
        text = _replace_once(
            text,
            "  const voiceRecordingMaxSeconds = useChatRuntimeStore(\n"
            "    (s) => s.voiceRecordingMaxSeconds,\n"
            "  );\n",
            (
                "  const voiceRecordingMaxSeconds = useChatRuntimeStore(\n"
                "    (s) => s.voiceRecordingMaxSeconds,\n"
                "  );\n"
                "  const effectiveVoiceRecordingMaxSeconds = getEffectiveVoiceRecordingMaxSeconds(\n"
                "    voiceRecordingMaxSeconds,\n"
                "    activeModel,\n"
                "  );\n"
            ),
            target,
            "shared effective voice recording limit",
        )
        changed = True
    if "}, voiceRecordingMaxSeconds);" in text and "effectiveVoiceRecordingMaxSeconds" in text:
        text = text.replace("}, voiceRecordingMaxSeconds);", "}, effectiveVoiceRecordingMaxSeconds);", 1)
        changed = True
    old_shared_recorder_callback = """  const nativeRecorder = useNativeAudioRecorder((base64, name) => {
    setPendingAudio({ name, base64 });
    setPendingAudioStore(base64, name);
  }, effectiveVoiceRecordingMaxSeconds);
"""
    new_shared_recorder_callback = """  const nativeRecorder = useNativeAudioRecorder(async (base64, name) => {
    if (activeModel?.hasAudioInput) {
      setPendingAudio({ name, base64 });
      setPendingAudioStore(base64, name);
      const nextText = buildVoiceMessageText({
        existingText: stripAudioOnlyMessagePlaceholder(text),
        promptText: useChatRuntimeStore.getState().voiceMessagePromptText,
      });
      const sendText = nextText || AUDIO_ONLY_MESSAGE_PLACEHOLDER;
      if (sendText !== text) setText(sendText);
      return;
    }
    const transcript = await transcribeVoiceAudio(base64);
    const nextText = buildVoiceMessageText({
      existingText: stripAudioOnlyMessagePlaceholder(text),
      transcript,
      promptText: useChatRuntimeStore.getState().voiceMessagePromptText,
    });
    if (nextText) setText(nextText);
  }, effectiveVoiceRecordingMaxSeconds);
"""
    if old_shared_recorder_callback in text:
        text = text.replace(old_shared_recorder_callback, new_shared_recorder_callback, 1)
        changed = True
    old_shared_native_recorder_history = """    if (activeModel?.hasAudioInput) {
      setPendingAudio({ name, base64 });
      setPendingAudioStore(base64, name);
      const nextText = buildVoiceMessageText({
        existingText: text,
        promptText: useChatRuntimeStore.getState().voiceMessagePromptText,
      });
      if (nextText && nextText !== text) setText(nextText);
      return;
    }
"""
    new_shared_native_recorder_history = """    if (activeModel?.hasAudioInput) {
      setPendingAudio({ name, base64 });
      setPendingAudioStore(base64, name);
      const nextText = buildVoiceMessageText({
        existingText: stripAudioOnlyMessagePlaceholder(text),
        promptText: useChatRuntimeStore.getState().voiceMessagePromptText,
      });
      const sendText = nextText || AUDIO_ONLY_MESSAGE_PLACEHOLDER;
      if (sendText !== text) setText(sendText);
      return;
    }
"""
    if old_shared_native_recorder_history in text:
        text = text.replace(old_shared_native_recorder_history, new_shared_native_recorder_history, 1)
        changed = True
    current_shared_native_recorder_history = """    if (activeModel?.hasAudioInput) {
      setPendingAudio({ name, base64 });
      setPendingAudioStore(base64, name);
      let transcript: string | undefined;
      try {
        transcript = await transcribeVoiceAudio(base64);
      } catch (error) {
        console.warn("Voice transcription for chat history failed:", error);
        toast.error("Voice transcript failed; sending audio without saved transcript.");
      }
      const nextText = buildVoiceMessageText({
        existingText: text,
        transcript,
        promptText: useChatRuntimeStore.getState().voiceMessagePromptText,
      });
      if (nextText && nextText !== text) setText(nextText);
      return;
    }
"""
    if current_shared_native_recorder_history in text:
        text = text.replace(current_shared_native_recorder_history, new_shared_native_recorder_history, 1)
        changed = True
    current_shared_asr_recorder_history = """    const transcript = await transcribeVoiceAudio(base64);
    const nextText = buildVoiceMessageText({
      existingText: text,
      transcript,
      promptText: useChatRuntimeStore.getState().voiceMessagePromptText,
    });
    if (nextText) setText(nextText);
"""
    updated_shared_asr_recorder_history = """    const transcript = await transcribeVoiceAudio(base64);
    const nextText = buildVoiceMessageText({
      existingText: stripAudioOnlyMessagePlaceholder(text),
      transcript,
      promptText: useChatRuntimeStore.getState().voiceMessagePromptText,
    });
    if (nextText) setText(nextText);
"""
    if current_shared_asr_recorder_history in text:
        text = text.replace(current_shared_asr_recorder_history, updated_shared_asr_recorder_history, 1)
        changed = True
    old_shared_audio_submit = """    if (pendingAudio) {
      content.push({ type: "audio", audio: pendingAudio.base64 });
    }
    if (msg) {
      content.push({ type: "text", text: msg });
    }
"""
    new_shared_audio_submit = """    let messageText = msg;
    if (pendingAudio && activeModel?.hasAudioInput) {
      content.push({ type: "audio", audio: pendingAudio.base64 });
      messageText = buildVoiceMessageText({
        existingText: msg,
        promptText: useChatRuntimeStore.getState().voiceMessagePromptText,
      });
    } else if (pendingAudio) {
      const transcript = await transcribeVoiceAudio(pendingAudio.base64);
      messageText = buildVoiceMessageText({
        existingText: msg,
        transcript,
        promptText: useChatRuntimeStore.getState().voiceMessagePromptText,
      });
    }
    if (messageText.trim()) {
      content.push({ type: "text", text: messageText.trim() });
    }
"""
    if old_shared_audio_submit in text:
        text = text.replace(old_shared_audio_submit, new_shared_audio_submit, 1)
        changed = True
    if "    const msg = text.trim();\n" in text:
        text = text.replace(
            "    const msg = text.trim();\n",
            "    const msg = stripAudioOnlyMessagePlaceholder(text).trim();\n",
            1,
        )
        changed = True
    if "  const nativeAudioUnavailableReason = !modelLoaded\n" not in text:
        text = _replace_once(
            text,
            "  const nativeRecorderBusy =\n"
            "    nativeRecorder.status === \"recording\" || nativeRecorder.status === \"processing\";\n",
            (
                "  const nativeRecorderBusy =\n"
                "    nativeRecorder.status === \"recording\" || nativeRecorder.status === \"processing\";\n"
                "  const nativeAudioUnavailableReason = !modelLoaded\n"
                "    ? \"Load an audio-input model before recording audio.\"\n"
                "    : activeModel?.hasAudioInput\n"
                "      ? null\n"
                "      : `${activeModel?.name || \"Current model\"} does not expose audio input. Load a model with an audio-capable mmproj.`;\n"
                "  const nativeRecordingEnabled =\n"
                "    nativeRecorder.supported && nativeAudioUnavailableReason === null;\n"
            ),
            target,
            "shared audio support state",
        )
        changed = True
    old_dictation = (
        "          {dictationSupported && (\n"
        "            <>\n"
        "              {!isDictating ? (\n"
        "                <TooltipIconButton\n"
        "                  tooltip=\"Dictate\"\n"
        "                  side=\"bottom\"\n"
        "                  variant=\"ghost\"\n"
        "                  size=\"icon\"\n"
        "                  className=\"size-8 rounded-full text-muted-foreground\"\n"
        "                  onClick={startDictation}\n"
        "                  aria-label=\"Dictate\"\n"
        "                >\n"
        "                  <MicIcon className=\"size-4\" />\n"
        "                </TooltipIconButton>\n"
        "              ) : (\n"
        "                <TooltipIconButton\n"
        "                  tooltip=\"Stop dictation\"\n"
        "                  side=\"bottom\"\n"
        "                  variant=\"ghost\"\n"
        "                  size=\"icon\"\n"
        "                  className=\"size-8 rounded-full text-destructive\"\n"
        "                  onClick={stopDictation}\n"
        "                  aria-label=\"Stop dictation\"\n"
        "                >\n"
        "                  <SquareIcon className=\"size-3 animate-pulse fill-current\" />\n"
        "                </TooltipIconButton>\n"
        "              )}\n"
        "            </>\n"
        "          )}\n"
    )
    new_recorder = (
        "          {nativeRecorder.supported ? (\n"
        "            nativeRecorder.status === \"recording\" ? (\n"
        "              <TooltipIconButton\n"
        "                tooltip={`Stop recording (${nativeRecorder.secondsElapsed}/${nativeRecorder.secondsLimit}s)`}\n"
        "                side=\"bottom\"\n"
        "                variant=\"ghost\"\n"
        "                size=\"icon\"\n"
        "                className=\"size-8 rounded-full text-destructive\"\n"
        "                onClick={nativeRecorder.stop}\n"
        "                aria-label=\"Stop native audio recording\"\n"
        "              >\n"
        "                <SquareIcon className=\"size-3 animate-pulse fill-current\" />\n"
        "              </TooltipIconButton>\n"
        "            ) : (\n"
        "              <TooltipIconButton\n"
        "                tooltip={nativeRecorder.status === \"processing\" ? \"Preparing voice message...\" : nativeAudioUnavailableReason ?? `Record native audio (max ${nativeRecorder.secondsLimit}s)`}\n"
        "                side=\"bottom\"\n"
        "                variant=\"ghost\"\n"
        "                size=\"icon\"\n"
        "                className=\"size-8 rounded-full text-muted-foreground\"\n"
        "                disabled={!nativeRecordingEnabled || nativeRecorderBusy}\n"
        "                onClick={() => void nativeRecorder.start()}\n"
        "                aria-label=\"Record native audio message\"\n"
        "              >\n"
        "                {nativeRecorder.status === \"processing\" ? <Spinner className=\"size-4\" /> : <MicIcon className=\"size-4\" />}\n"
        "              </TooltipIconButton>\n"
        "            )\n"
        "          ) : null}\n"
    )
    if old_dictation in text:
        text = text.replace(old_dictation, new_recorder, 1)
        changed = True
    if "          {nativeRecordingEnabled && nativeRecorder.supported ? (\n" in text:
        text = text.replace(
            "          {nativeRecordingEnabled && nativeRecorder.supported ? (\n",
            "          {nativeRecorder.supported ? (\n",
            1,
        )
        changed = True
    if '                tooltip={nativeRecorder.status === "processing" ? "Preparing voice message..." : `Record native audio (max ${nativeRecorder.secondsLimit}s)`}\n' in text:
        text = text.replace(
            '                tooltip={nativeRecorder.status === "processing" ? "Preparing voice message..." : `Record native audio (max ${nativeRecorder.secondsLimit}s)`}\n',
            '                tooltip={nativeRecorder.status === "processing" ? "Preparing voice message..." : nativeAudioUnavailableReason ?? `Record native audio (max ${nativeRecorder.secondsLimit}s)`}\n',
            1,
        )
        changed = True
    if "                disabled={nativeRecorderBusy}\n" in text:
        text = text.replace(
            "                disabled={nativeRecorderBusy}\n",
            "                disabled={!nativeRecordingEnabled || nativeRecorderBusy}\n",
            1,
        )
        changed = True
    if changed:
        target.write_text(text, encoding="utf-8", newline="\n")
    return changed


def _apply_assistant_prefix_continuation_utils_shim(frontend: Path) -> bool:
    target = frontend / "src" / "features" / "chat" / "utils" / "update-thread-message.ts"
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    changed = False
    if "extractAssistantContinuationPrefixText" not in text:
        text = _replace_once(
            text,
            """  return parts;
}

export async function updateThreadMessage(args: {
""",
            """  return parts;
}

export function extractAssistantContinuationPrefixText(text: string): string {
  return parseTaggedTextToContent(text)
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("\\n\\n")
    .trimStart();
}

export async function updateThreadMessage(args: {
""",
            target,
            "assistant continuation text-only prefix helper",
        )
        changed = True
    if changed:
        target.write_text(text, encoding="utf-8", newline="\n")
    return changed


def _apply_assistant_prefix_continuation_thread_shim(frontend: Path) -> bool:
    target = frontend / "src" / "components" / "assistant-ui" / "thread.tsx"
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    changed = False
    old_import = 'import { extractTaggedText, updateThreadMessage } from "@/features/chat/utils/update-thread-message";\n'
    new_import = 'import { extractAssistantContinuationPrefixText, extractTaggedText, updateThreadMessage } from "@/features/chat/utils/update-thread-message";\n'
    if old_import in text:
        text = text.replace(old_import, new_import, 1)
        changed = True
    if ASSISTANT_PREFIX_CONTINUATION_SHIM_MARKER not in text:
        text = _replace_once(
            text,
            "/**\n * AssistantMessage handles the display and inline-editing of AI responses.\n",
            (
                "function normalizeAssistantContinuationPrefix(text: string): string {\n"
                f"  // {ASSISTANT_PREFIX_CONTINUATION_SHIM_MARKER}: prefill uses visible answer text only.\n"
                "  return extractAssistantContinuationPrefixText(text)\n"
                '    .replaceAll("<THINK>", "<think>")\n'
                '    .replaceAll("</THINK>", "</think>");\n'
                "}\n\n"
                "/**\n * AssistantMessage handles the display and inline-editing of AI responses.\n"
            ),
            target,
            "assistant continuation prefix normalizer",
        )
        changed = True
    elif 'return text.replaceAll("<THINK>", "<think>").replaceAll("</THINK>", "</think>");' in text:
        text = text.replace(
            '  // UNSLOTH_ASSISTANT_PREFIX_CONTINUATION_SHIM: edit text uses tagged-text exports.\n'
            '  return text.replaceAll("<THINK>", "<think>").replaceAll("</THINK>", "</think>");\n',
            '  // UNSLOTH_ASSISTANT_PREFIX_CONTINUATION_SHIM: prefill uses visible answer text only.\n'
            '  return extractAssistantContinuationPrefixText(text)\n'
            '    .replaceAll("<THINK>", "<think>")\n'
            '    .replaceAll("</THINK>", "</think>");\n',
            1,
        )
        changed = True
    if "const messageParentId = useAuiState(({ message }) => message.parentId);" not in text:
        text = _replace_once(
            text,
            "  const messageId = useAuiState(({ message }) => message.id);\n",
            "  const messageId = useAuiState(({ message }) => message.id);\n"
            "  const messageParentId = useAuiState(({ message }) => message.parentId);\n",
            target,
            "assistant continuation message parent id",
        )
        changed = True
    old_handle_save = (
        "  const handleSave = async () => {\n"
        '    const finalText = textareaRef.current?.value || "";\n'
        "    \n"
        "    // Prioritize the specific thread item ID, then fallback to the global active thread ID\n"
        "    const remoteId = aui.threadListItem().getState().remoteId \n"
        "                  || useChatRuntimeStore.getState().activeThreadId;\n"
        "\n"
        '    if (!remoteId || remoteId === "" || remoteId === "/") {\n'
        '      toast.error("Save failed: No thread ID found.");\n'
        "      setEditingId(null);\n"
        "      return;\n"
        "    }\n"
        "\n"
        "    try {\n"
        "      await updateThreadMessage({\n"
        "        thread: { \n"
        "          export: () => aui.thread().export(), \n"
        "          import: (data) => aui.thread().import(data) \n"
        "        },\n"
        "        messageId,\n"
        "        remoteId,\n"
        "        newText: finalText,\n"
        "        isIncognito: incognito,\n"
        "      });\n"
        "    } catch (error) {\n"
        '      console.error("UI: Error during save:", error);\n'
        '      toast.error("Failed to save message edits.");\n'
        "    } finally {\n"
        "      setEditingId(null);\n"
        "    }\n"
        "  };\n"
    )
    new_handle_save = """  const [isSavingEdit, setIsSavingEdit] = useState(false);

  const saveEditedResponse = async (): Promise<string | null> => {
    const finalText = textareaRef.current?.value || "";

    // Prioritize the specific thread item ID, then fallback to the global active thread ID
    const remoteId = aui.threadListItem().getState().remoteId
                  || useChatRuntimeStore.getState().activeThreadId;

    if (!remoteId || remoteId === "" || remoteId === "/") {
      toast.error("Save failed: No thread ID found.");
      setEditingId(null);
      return null;
    }

    setIsSavingEdit(true);
    try {
      await updateThreadMessage({
        thread: {
          export: () => aui.thread().export(),
          import: (data) => aui.thread().import(data)
        },
        messageId,
        remoteId,
        newText: finalText,
        isIncognito: incognito,
      });
      return finalText;
    } catch (error) {
      console.error("UI: Error during save:", error);
      toast.error("Failed to save message edits.");
      return null;
    } finally {
      setIsSavingEdit(false);
      setEditingId(null);
    }
  };

  const handleSave = async () => {
    await saveEditedResponse();
  };

  const handleSaveAndContinue = async () => {
    const finalText = await saveEditedResponse();
    if (finalText === null) return;
    aui.thread().startRun({
      parentId: messageParentId,
      sourceId: messageId,
      runConfig: {
        custom: {
          assistantContinuationMode: "prefix",
          assistantPrefix: normalizeAssistantContinuationPrefix(finalText),
          assistantPrefixSourceMessageId: messageId,
        },
      },
    });
  };
"""
    if old_handle_save in text:
        text = text.replace(old_handle_save, new_handle_save, 1)
        changed = True
    if "const messageRuntime = aui.message();" in text:
        text = text.replace("    const messageRuntime = aui.message();\n", "", 1)
        changed = True
    if "messageRuntime.reload({" in text:
        text = text.replace(
            "    messageRuntime.reload({\n"
            "      runConfig: {\n",
            "    aui.thread().startRun({\n"
            "      parentId: messageParentId,\n"
            "      sourceId: messageId,\n"
            "      runConfig: {\n",
            1,
        )
        changed = True
    if "void handleSaveAndContinue();" not in text:
        text = _replace_once(
            text,
            """                if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                  handleSave();
                }
""",
            """                if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                  if (e.shiftKey) {
                    void handleSaveAndContinue();
                  } else {
                    void handleSave();
                  }
                }
""",
            target,
            "assistant edit save keyboard shortcut",
        )
        changed = True
    if "Save & Continue" not in text:
        text = _replace_once(
            text,
            """              <Button size="sm" variant="ghost" onClick={() => setEditingId(null)} className="h-8 text-xs">Cancel</Button>
              <Button size="sm" onClick={handleSave} className="h-8 text-xs">Save</Button>
""",
            """              <Button size="sm" variant="ghost" disabled={isSavingEdit} onClick={() => setEditingId(null)} className="h-8 text-xs">Cancel</Button>
              <Button size="sm" variant="secondary" disabled={isSavingEdit} onClick={handleSaveAndContinue} className="h-8 text-xs">Save & Continue</Button>
              <Button size="sm" disabled={isSavingEdit} onClick={handleSave} className="h-8 text-xs">Save</Button>
""",
            target,
            "assistant edit save and continue button",
        )
        changed = True
    if changed:
        target.write_text(text, encoding="utf-8", newline="\n")
    return changed


def _apply_assistant_prefix_continuation_adapter_shim(frontend: Path) -> bool:
    target = frontend / "src" / "features" / "chat" / "api" / "chat-adapter.ts"
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    changed = False
    if ASSISTANT_PREFIX_CONTINUATION_SHIM_MARKER not in text:
        text = _replace_once(
            text,
            "function collectTextParts(message: RunMessage): string[] {\n",
            (
                f"// {ASSISTANT_PREFIX_CONTINUATION_SHIM_MARKER}: /asst starts an assistant prefill branch.\n"
                "function parseAssistantPrefillCommand(text: string): string | null {\n"
                '  const match = text.match(/^\\s*\\/asst(?:\\s+([\\s\\S]*))?$/i);\n'
                "  if (!match) return null;\n"
                "  return (match[1] ?? \"\").trimStart();\n"
                "}\n\n"
                "function isAssistantPrefillCommandMessage(message: RunMessage): boolean {\n"
                '  if (message.role !== "user") return false;\n'
                "  const textParts = collectTextParts(message);\n"
                "  return textParts.length === 1 && parseAssistantPrefillCommand(textParts[0]) !== null;\n"
                "}\n\n"
                "function collectLatestAssistantPrefillCommand(messages: RunMessages): string | null {\n"
                "  const latest = messages.at(-1);\n"
                '  if (!latest || latest.role !== "user") return null;\n'
                "  const textParts = collectTextParts(latest);\n"
                "  if (textParts.length !== 1) return null;\n"
                "  return parseAssistantPrefillCommand(textParts[0]);\n"
                "}\n\n"
                "function collectTextParts(message: RunMessage): string[] {\n"
            ),
            target,
            "assistant prefix command helpers",
        )
        changed = True
    if "async *run({ messages, runConfig, abortSignal, unstable_threadId })" not in text:
        text = _replace_once(
            text,
            "    async *run({ messages, abortSignal, unstable_threadId }) {\n",
            "    async *run({ messages, runConfig, abortSignal, unstable_threadId }) {\n",
            target,
            "adapter runConfig argument",
        )
        changed = True
    if "const requestedAssistantContinuationPrefix" not in text:
        text = _replace_once(
            text,
            "      let runtime = useChatRuntimeStore.getState();\n",
            (
                "      let runtime = useChatRuntimeStore.getState();\n"
                "      const runConfigAssistantPrefix =\n"
                "        runConfig?.custom?.assistantContinuationMode === \"prefix\" &&\n"
                "        typeof runConfig.custom.assistantPrefix === \"string\"\n"
                "          ? runConfig.custom.assistantPrefix\n"
                "          : \"\";\n"
                "      const slashCommandAssistantPrefix =\n"
                "        collectLatestAssistantPrefillCommand(messages) ?? \"\";\n"
                "      const requestedAssistantContinuationPrefix =\n"
                "        runConfigAssistantPrefix || slashCommandAssistantPrefix;\n"
            ),
            target,
            "assistant prefix run config",
        )
        changed = True
    if "return [{ role: \"user\", content: \"\" }];" not in text:
        text = _replace_once(
            text,
            """  if (message.role === "assistant") {
    return serializeAssistantReplayMessages(message);
  }
""",
            """  if (isAssistantPrefillCommandMessage(message)) {
    return [{ role: "user", content: "" }];
  }

  if (message.role === "assistant") {
    return serializeAssistantReplayMessages(message);
  }
""",
            target,
            "bridge assistant prefix slash commands",
        )
        changed = True
    old_drop_block = """      for (const message of messages) {
        if (isAssistantPrefillCommandMessage(message)) {
          continue;
        }
        if (isAnthropicRefusalMessage(message)) {
          const last = survivingMessages.at(-1);
          if (last && last.role === "user") {
            survivingMessages.pop();
          }
          continue;
        }
        survivingMessages.push(message);
      }
"""
    if old_drop_block in text:
        text = _replace_once(
            text,
            old_drop_block,
            """      for (const message of messages) {
        if (isAnthropicRefusalMessage(message)) {
          const last = survivingMessages.at(-1);
          if (last && last.role === "user") {
            survivingMessages.pop();
          }
          continue;
        }
        survivingMessages.push(message);
      }
""",
            target,
            "keep assistant prefix slash command bridge messages",
        )
        changed = True
    if "content: requestedAssistantContinuationPrefix" not in text:
        text = _replace_once(
            text,
            """      const outboundMessages = survivingMessages
        .flatMap(toOpenAIMessages)
        .filter((message): message is NonNullable<typeof message> =>
          Boolean(message),
        );
""",
            """      const outboundMessages = survivingMessages
        .flatMap(toOpenAIMessages)
        .filter((message): message is NonNullable<typeof message> =>
          Boolean(message),
        );
      if (requestedAssistantContinuationPrefix) {
        outboundMessages.push({
          role: "assistant",
          content: requestedAssistantContinuationPrefix,
        });
      }
""",
            target,
            "append assistant prefix prefill message",
        )
        changed = True
    if "const lastOutboundRole = outboundMessages.at(-1)?.role;" not in text:
        text = _replace_once(
            text,
            """      if (requestedAssistantContinuationPrefix) {
        outboundMessages.push({
          role: "assistant",
          content: requestedAssistantContinuationPrefix,
        });
      }
""",
            """      if (requestedAssistantContinuationPrefix) {
        const lastOutboundRole = outboundMessages.at(-1)?.role;
        if (lastOutboundRole !== "user" && lastOutboundRole !== "tool") {
          outboundMessages.push({ role: "user", content: "" });
        }
        outboundMessages.push({
          role: "assistant",
          content: requestedAssistantContinuationPrefix,
        });
      }
""",
            target,
            "guard assistant prefix with bridge user message",
        )
        changed = True
    if "let cumulativeText = requestedAssistantContinuationPrefix;" not in text:
        text = _replace_once(
            text,
            '      let cumulativeText = "";\n',
            "      let cumulativeText = requestedAssistantContinuationPrefix;\n",
            target,
            "seed assistant stream with prefix",
        )
        changed = True
    if "pendingAssistantContinuationPrefixEcho" not in text:
        text = _replace_once(
            text,
            "      let cumulativeText = requestedAssistantContinuationPrefix;\n",
            (
                "      let cumulativeText = requestedAssistantContinuationPrefix;\n"
                "      let pendingAssistantContinuationPrefixEcho = requestedAssistantContinuationPrefix;\n"
                "      const stripAssistantContinuationPrefixEcho = (text: string): string => {\n"
                "        if (!pendingAssistantContinuationPrefixEcho || !text) return text;\n"
                "        if (pendingAssistantContinuationPrefixEcho.startsWith(text)) {\n"
                "          pendingAssistantContinuationPrefixEcho =\n"
                "            pendingAssistantContinuationPrefixEcho.slice(text.length);\n"
                "          return \"\";\n"
                "        }\n"
                "        if (text.startsWith(pendingAssistantContinuationPrefixEcho)) {\n"
                "          const stripped = text.slice(\n"
                "            pendingAssistantContinuationPrefixEcho.length,\n"
                "          );\n"
                "          pendingAssistantContinuationPrefixEcho = \"\";\n"
                "          return stripped;\n"
                "        }\n"
                "        pendingAssistantContinuationPrefixEcho = \"\";\n"
                "        return text;\n"
                "      };\n"
            ),
            target,
            "strip echoed assistant prefix from stream",
        )
        changed = True
    if "if (requestedAssistantContinuationPrefix) {\n        yield {" not in text:
        text = _replace_once(
            text,
            """        return pinTextThoughtSignature(assembled);
      };
      const parseToolProvenance = (
""",
            """        return pinTextThoughtSignature(assembled);
      };
      if (requestedAssistantContinuationPrefix) {
        yield {
          content: buildAssistantContent(cumulativeText),
          metadata: {
            timing: buildTiming(streamStartTime, totalChunks, firstTokenTime),
            custom: { reasoningDuration },
          },
        };
      }
      const parseToolProvenance = (
""",
            target,
            "yield initial assistant prefix",
        )
        changed = True
    if "stripAssistantContinuationPrefixEcho(delta)" not in text:
        text = _replace_once(
            text,
            """              if (delta) {
                closeReasoningContent();
                cumulativeText += delta;
              }
""",
            """              if (delta) {
                closeReasoningContent();
                const strippedDelta = stripAssistantContinuationPrefixEcho(delta);
                if (strippedDelta) {
                  cumulativeText += strippedDelta;
                }
              }
""",
            target,
            "apply assistant prefix echo stripper",
        )
        changed = True
    if changed:
        target.write_text(text, encoding="utf-8", newline="\n")
    return changed


def _apply_assistant_ui_source_id_reload_shim(frontend: Path) -> bool:
    targets = (
        frontend
        / "node_modules"
        / "@assistant-ui"
        / "core"
        / "src"
        / "runtimes"
        / "local"
        / "local-thread-runtime-core.ts",
        frontend
        / "node_modules"
        / "@assistant-ui"
        / "core"
        / "dist"
        / "runtimes"
        / "local"
        / "local-thread-runtime-core.js",
    )
    changed = False
    for target in targets:
        if not target.exists():
            echo(f"  assistant-ui runtime package file not found; skipping {target.name}")
            continue
        text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
        if "sourceId, runConfig" in text and "const id = sourceId ?? generateId();" in text:
            continue
        if target.suffix == ".ts":
            text = _replace_once(
                text,
                "  public async startRun(\n"
                "    { parentId, runConfig }: StartRunConfig,\n",
                "  public async startRun(\n"
                "    { parentId, sourceId, runConfig }: StartRunConfig,\n",
                target,
                "assistant-ui sourceId reload argument",
            )
        else:
            text = _replace_once(
                text,
                "    async startRun({ parentId, runConfig }, runCallback) {\n",
                "    async startRun({ parentId, sourceId, runConfig }, runCallback) {\n",
                target,
                "assistant-ui sourceId reload argument",
            )
        text = _replace_once(
            text,
            "        const id = generateId();\n",
            "        const id = sourceId ?? generateId();\n",
            target,
            "assistant-ui sourceId reload id reuse",
        )
        target.write_text(text, encoding="utf-8", newline="\n")
        changed = True
    return changed


def apply_assistant_prefix_continuation_shim(studio_home: Path) -> None:
    frontend = studio_package_dir(studio_home) / "frontend"
    if not frontend.exists():
        raise FileNotFoundError(f"Missing frontend at {frontend}")
    changed = False
    changed |= _apply_assistant_ui_source_id_reload_shim(frontend)
    changed |= _apply_assistant_prefix_continuation_utils_shim(frontend)
    changed |= _apply_assistant_prefix_continuation_thread_shim(frontend)
    changed |= _apply_assistant_prefix_continuation_adapter_shim(frontend)
    if changed:
        echo("  applied assistant prefix continuation shim to Studio web UI")
    else:
        echo("  assistant prefix continuation shim already present")


def apply_native_audio_mic_shim(studio_home: Path, *, build_frontend: bool = False) -> None:
    frontend = studio_package_dir(studio_home) / "frontend"
    if not frontend.exists():
        raise FileNotFoundError(f"Missing frontend at {frontend}")
    changed = False
    recorder_path = frontend / "src" / "features" / "chat" / "native-audio-recorder.ts"
    changed |= _write_text_if_changed(recorder_path, NATIVE_AUDIO_RECORDER_TS)
    changed |= _apply_asr_fallback_backend_schema_shim(studio_home)
    changed |= _apply_asr_fallback_backend_route_shim(studio_home)
    changed |= _apply_native_audio_backend_settings_shim(studio_home)
    changed |= _apply_native_audio_frontend_api_shim(frontend)
    changed |= _apply_audio_only_placeholder_chat_adapter_shim(frontend)
    changed |= _apply_native_audio_settings_storage_shim(frontend)
    changed |= _apply_native_audio_runtime_store_shim(frontend)
    changed |= _apply_gemma_recommended_preset_shim(frontend)
    changed |= _apply_native_audio_settings_panel_shim(frontend)
    changed |= _apply_native_audio_thread_shim(frontend)
    changed |= _apply_native_audio_shared_composer_shim(frontend)
    changed |= _apply_asr_fallback_frontend_shim(frontend)
    changed |= _apply_sidebar_layout_fallback_shim(frontend)
    changed |= _apply_chat_layout_fallback_shim(frontend)
    changed |= _apply_settings_dialog_layout_shim(frontend)
    changed |= _apply_tailwind_safe_source_shim(frontend)

    if changed:
        echo("  applied native audio mic shim to Studio web UI")
    else:
        echo("  native audio mic shim already present")
    if build_frontend:
        echo("  rebuilding Studio frontend...")
        completed = subprocess.run(["npm.cmd", "run", "build"], cwd=frontend)
        if completed.returncode != 0:
            raise SystemExit(completed.returncode)


def apply_studio_patch_stack(studio_home: Path, *, build_frontend: bool = False) -> None:
    restore_studio_patch_base(studio_home)
    echo("  applying Studio patch stack in fixed order")
    apply_llama_local_zip_shim(studio_home)
    apply_chat_template_override_shim(studio_home)
    apply_speculative_type_shim(studio_home)
    apply_cli_api_key_reuse_shim(studio_home)
    apply_openai_reasoning_passthrough_shim(studio_home)
    apply_openai_request_autoload_shim_v2(studio_home)
    apply_openai_autoload_speculative_shim(studio_home)
    apply_web_ui_tool_policy_shim(studio_home)
    apply_embedding_extra_args_shim(studio_home)
    apply_fixed_llama_port_shim(studio_home)
    apply_native_audio_mic_shim(studio_home, build_frontend=False)
    apply_assistant_prefix_continuation_shim(studio_home)
    if build_frontend:
        frontend = studio_package_dir(studio_home) / "frontend"
        echo("  rebuilding Studio frontend...")
        completed = subprocess.run(["npm.cmd", "run", "build"], cwd=frontend)
        if completed.returncode != 0:
            raise SystemExit(completed.returncode)


def _resolve_secret_ref(secret: dict[str, Any]) -> str:
    env_name = secret.get("env")
    if env_name:
        value = os.environ.get(str(env_name))
        if not value and str(env_name) == "MCPPROXY_AGENTS_TOKEN":
            value = os.environ.get("MCPPROXY_AGENT_TOKEN")
        if value:
            return value

    op_ref = secret.get("op")
    if op_ref:
        completed = subprocess.run(
            ["op", "read", str(op_ref)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or "op read failed"
            raise RuntimeError(f"Could not resolve 1Password secret {op_ref}: {detail}")
        value = completed.stdout.strip()
        if not value:
            raise RuntimeError(f"1Password secret {op_ref} resolved to an empty value")
        return value

    value = secret.get("value")
    if value:
        return str(value)
    raise ValueError("secret must define one of: env, op, value")


def _headers_from_mcp_server_config(server: dict[str, Any]) -> dict[str, str] | None:
    headers = {str(k): str(v) for k, v in (server.get("headers") or {}).items()}
    auth = server.get("authorization")
    if isinstance(auth, dict):
        auth_type = str(auth.get("type", "")).lower()
        if auth_type == "bearer":
            token = _resolve_secret_ref(auth)
            headers["Authorization"] = f"Bearer {token}"
        elif auth_type:
            raise ValueError(f"Unsupported authorization type: {auth_type}")
    return headers or None


def _ensure_mcp_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mcp_servers (
            id TEXT NOT NULL PRIMARY KEY,
            display_name TEXT NOT NULL,
            url TEXT NOT NULL,
            headers_json TEXT,
            is_enabled INTEGER NOT NULL DEFAULT 1,
            use_oauth INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(mcp_servers)").fetchall()}
    if "use_oauth" not in cols:
        conn.execute(
            "ALTER TABLE mcp_servers ADD COLUMN use_oauth INTEGER NOT NULL DEFAULT 0"
        )


def _sync_mcp_config(
    studio_home: Path,
    config_path: Path,
    *,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    if not config_path.exists():
        raise FileNotFoundError(f"MCP config not found: {config_path}")
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    servers = payload.get("servers")
    if not isinstance(servers, list):
        raise ValueError(f"{config_path} must contain a top-level 'servers' list")

    rows: list[dict[str, Any]] = []
    for server in servers:
        if not isinstance(server, dict):
            raise ValueError("Each MCP server entry must be an object")
        server_id = str(server.get("id", "")).strip()
        display_name = str(server.get("display_name", "")).strip()
        url = str(server.get("url", "")).strip()
        if not server_id or not display_name or not url:
            raise ValueError("Each MCP server must define id, display_name, and url")
        headers = None if dry_run else _headers_from_mcp_server_config(server)
        rows.append(
            {
                "id": server_id,
                "display_name": display_name,
                "url": url,
                "headers_json": (
                    json.dumps(headers, separators=(",", ":")) if headers else None
                ),
                "is_enabled": bool(server.get("is_enabled", True)),
                "use_oauth": bool(server.get("use_oauth", False)),
                "has_headers": bool(
                    server.get("headers") or server.get("authorization")
                ),
            }
        )

    if dry_run:
        return rows

    db_path = studio_home / "studio.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        _ensure_mcp_schema(conn)
        now_sql = "strftime('%Y-%m-%dT%H:%M:%f+00:00','now')"
        for row in rows:
            exists = conn.execute(
                "SELECT 1 FROM mcp_servers WHERE id = ?", (row["id"],)
            ).fetchone()
            if exists:
                conn.execute(
                    f"""
                    UPDATE mcp_servers
                       SET display_name = ?,
                           url = ?,
                           headers_json = ?,
                           is_enabled = ?,
                           use_oauth = ?,
                           updated_at = {now_sql}
                     WHERE id = ?
                    """,
                    (
                        row["display_name"],
                        row["url"],
                        row["headers_json"],
                        int(row["is_enabled"]),
                        int(row["use_oauth"]),
                        row["id"],
                    ),
                )
            else:
                conn.execute(
                    f"""
                    INSERT INTO mcp_servers
                        (id, display_name, url, headers_json,
                         is_enabled, use_oauth, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, {now_sql}, {now_sql})
                    """,
                    (
                        row["id"],
                        row["display_name"],
                        row["url"],
                        row["headers_json"],
                        int(row["is_enabled"]),
                        int(row["use_oauth"]),
                    ),
                )
        conn.commit()
    finally:
        conn.close()
    return rows


def sync_mcp(args: argparse.Namespace) -> None:
    studio_home = Path(args.studio_home)
    config_path = Path(args.config)
    rows = _sync_mcp_config(studio_home, config_path, dry_run=args.dry_run)
    action = "Would sync" if args.dry_run else "Synced"
    echo(f"{action} {len(rows)} MCP server(s) from {config_path}:")
    for row in rows:
        enabled = "enabled" if row["is_enabled"] else "disabled"
        auth = "headers" if row["has_headers"] else "no headers"
        echo(f"  {row['id']}: {row['display_name']} ({enabled}, {auth}) -> {row['url']}")


def setup(args: argparse.Namespace) -> None:
    studio_home = Path(args.studio_home)
    model_root = Path(args.model_root)
    zip_dir = Path(args.zip_dir)
    echo("=== Unsloth Studio setup ===")
    echo(f"StudioHome={studio_home}  ModelRoot={model_root}  tag={args.release_tag}  cuda={args.runtime}")

    unsloth = resolve_unsloth_exe(studio_home)
    python = resolve_studio_python(studio_home)
    llama_dir = studio_home / "llama.cpp"

    echo("\n[1/6] Applying deterministic Studio patch stack...")
    apply_studio_patch_stack(studio_home, build_frontend=False)

    echo(f"\n[2/6] Locating prebuilt zips in {zip_dir} ...")
    bin_zip, cudart_zip = find_llama_zips(zip_dir, args.release_tag, args.runtime)
    echo(f"  bin:    {bin_zip}")
    echo(f"  cudart: {cudart_zip}")

    echo(f"\n[3/6] Installing llama.cpp {args.release_tag} (cuda-{args.runtime}) from local zips...")
    staging = studio_home / ".staging"
    if staging.exists():
        for child in staging.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)

    env = os.environ.copy()
    env["UNSLOTH_STUDIO_HOME"] = str(studio_home)
    env["UNSLOTH_LLAMA_LOCAL_DIR"] = str(zip_dir)
    env["UNSLOTH_LLAMA_RELEASE_TAG"] = args.release_tag
    env["UNSLOTH_VERBOSE"] = "1"
    run(
        [
            python,
            studio_package_dir(studio_home) / "install_llama_prebuilt.py",
            "--install-dir",
            llama_dir,
            "--llama-tag",
            args.release_tag,
            "--published-repo",
            "ggml-org/llama.cpp",
            "--published-release-tag",
            args.release_tag,
        ],
        env=env,
    )

    echo("\n[4/6] Marking install Studio-owned and running 'unsloth studio setup'...")
    ensure_dir(llama_dir)
    (llama_dir / ".unsloth-studio-owned").write_text("", encoding="utf-8")
    if args.skip_base:
        env["SKIP_STUDIO_BASE"] = "1"
    else:
        env.pop("SKIP_STUDIO_BASE", None)
    run([unsloth, "studio", "setup", "--verbose"], env=env)
    apply_studio_patch_stack(studio_home, build_frontend=True)

    if args.register_model_root:
        echo(f"\n[5/6] Registering model folder {model_root} as a Studio scan folder...")
        register_scan_folder(studio_home, model_root)
    else:
        echo(
            "\n[5/6] Skipping model-root scan-folder registration; "
            "HF cache repos are discovered as downloaded models."
        )

    echo("\n[6/6] Installing unsloth.cmd launcher...")
    install_unsloth_wrapper(studio_home)
    echo("\nDone. Launch the web UI with: just unsloth serve")


def serve(args: argparse.Namespace) -> None:
    studio_home = Path(args.studio_home)
    unsloth = resolve_unsloth_exe(studio_home)
    source_repo = resolve_unsloth_source_repo(args.source_repo)
    if source_repo is not None:
        prepare_unsloth_source_repo(
            source_repo,
            build_frontend=args.source_build_frontend,
        )
        if args.source_apply_shims:
            echo("  source mode requested with shim patch stack enabled")
            apply_studio_patch_stack(studio_home, build_frontend=args.patch_web)
        else:
            echo("  source mode active; skipping win-models Studio shim patch stack")
    else:
        apply_studio_patch_stack(studio_home, build_frontend=args.patch_web)
    if args.parallel < 1:
        raise ValueError("--parallel must be at least 1")
    bind_host = "0.0.0.0" if args.lan else args.host
    model = args.model
    model_path = Path(model)
    is_local_model = (
        model_path.is_absolute()
        or model.startswith(".")
        or "\\" in model
        or model.lower().endswith(".gguf")
    )
    if is_local_model and not model_path.exists():
        raise FileNotFoundError(f"Model not found: {model_path}")

    env = os.environ.copy()
    env["UNSLOTH_STUDIO_HOME"] = str(studio_home)
    env["LOG_LEVEL"] = args.log_level
    env["ENVIRONMENT_TYPE"] = "production"
    env["UNSLOTH_DEFAULT_MODEL"] = model
    env["UNSLOTH_CONTEXT_LENGTH"] = str(args.max_seq_length)
    if source_repo is not None:
        configure_unsloth_source_env(env, source_repo)
    if args.llama_port > 0:
        env["UNSLOTH_LLAMA_PORT"] = str(args.llama_port)
        env["WIN_MODELS_LLAMA_PORT"] = str(args.llama_port)
    env["UNSLOTH_AUTOLOAD_LOAD_IN_4BIT"] = "1"
    if args.hf_cache_dir:
        hf_cache_dir = Path(args.hf_cache_dir)
        hf_cache_dir.mkdir(parents=True, exist_ok=True)
        env["HUGGINGFACE_HUB_CACHE"] = str(hf_cache_dir)
        env["HF_HUB_CACHE"] = str(hf_cache_dir)
    if args.cache_type_kv:
        env["UNSLOTH_CACHE_TYPE_KV"] = args.cache_type_kv
    if args.reasoning_format:
        env["UNSLOTH_REASONING_FORMAT"] = args.reasoning_format
    if args.speculative_type:
        env["UNSLOTH_SPECULATIVE_TYPE"] = args.speculative_type
    if args.chat_template_file:
        chat_template_file = Path(args.chat_template_file).resolve()
        if not chat_template_file.is_file():
            raise FileNotFoundError(f"Chat template file not found: {chat_template_file}")
        env["UNSLOTH_CHAT_TEMPLATE_FILE"] = str(chat_template_file)

    port_check = powershell(f"Get-NetTCPConnection -LocalPort {args.port} -State Listen -ErrorAction SilentlyContinue", check=False)
    if port_check.strip():
        echo(f"A server is already listening on port {args.port}.")
        if args.open:
            open_url(f"http://localhost:{args.port}")
        return

    command = [
        unsloth,
        "studio",
        "run",
        "--model",
        model,
        "--max-seq-length",
        str(args.max_seq_length),
        "--parallel",
        str(args.parallel),
        "-H",
        bind_host,
        "-p",
        str(args.port),
        "--yes",
    ]
    if args.max_seq_length > 0:
        command.extend(["-c", str(args.max_seq_length)])
    if args.cache_type_kv:
        command.extend(
            [
                "--cache-type-k",
                args.cache_type_kv,
                "--cache-type-v",
                args.cache_type_kv,
            ]
        )
    if args.reasoning_format:
        command.extend(["--reasoning-format", args.reasoning_format])
    embed_args = embedding_llama_args(model)
    if embed_args:
        command.extend(embed_args)
    if args.enable_tools is True:
        command.append("--enable-tools")
    elif args.enable_tools is False:
        command.append("--disable-tools")
    if args.verbose_llama:
        command.append("--verbose")

    echo(f"Starting Unsloth Studio on http://{bind_host}:{args.port}  (LOG_LEVEL={args.log_level})")
    echo(f"Context length={args.max_seq_length}; parallel slots={args.parallel}; per-slot context={args.max_seq_length // args.parallel}")
    if args.llama_port > 0:
        echo(f"llama-server fixed port: {args.llama_port}")
    if args.max_seq_length > 0:
        echo(f"llama-server context override: -c {args.max_seq_length}")
    if args.cache_type_kv:
        echo(f"llama-server KV cache override: --cache-type-k {args.cache_type_kv} --cache-type-v {args.cache_type_kv}")
    if args.reasoning_format:
        echo(f"llama-server reasoning parser: --reasoning-format {args.reasoning_format}")
    if args.speculative_type:
        echo(f"Studio speculative decoding mode: {args.speculative_type}")
    if embed_args:
        echo(f"llama-server embedding mode: {' '.join(embed_args)}")
    if args.chat_template_file:
        echo(f"Studio chat template override: {Path(args.chat_template_file).resolve()}")
    if source_repo is not None:
        echo(f"Unsloth source mode: {source_repo}")
        if not args.source_apply_shims:
            echo("Studio shim patch stack: skipped")
    if args.enable_tools is True:
        echo("Studio tool policy: forced on for web UI requests; generated API keys remain blocked.")
    elif args.enable_tools is False:
        echo("Studio tool policy: forced off for web UI and API requests.")
    else:
        echo("Studio tool policy: web UI may opt in; generated API keys cannot enable server-side tools.")
    echo("Backend output will stream in this terminal. Press Ctrl+C to stop.")
    echo(f"llama-server log: {studio_home}\\logs\\llama-server\\llama-*-port-*.log")
    if args.lan:
        echo("Bound to 0.0.0.0; open via localhost on this machine for browser secure-context features.")
    if args.open:
        echo(f"Opening http://localhost:{args.port}; refresh once Studio finishes starting if needed.")
        open_url(f"http://localhost:{args.port}")
    run(command, env=env)


def prepare_source(args: argparse.Namespace) -> None:
    source_repo = resolve_unsloth_source_repo(args.source_repo)
    if source_repo is None:
        raise ValueError(
            "No source repo configured. Pass --source-repo or set UNSLOTH_SOURCE_REPO."
        )
    prepare_unsloth_source_repo(
        source_repo,
        build_frontend=args.build_frontend,
    )
    echo("Source repo is ready for: win-models unsloth serve --source-repo <path>")


def stop(args: argparse.Namespace) -> None:
    studio_home = Path(args.studio_home)
    pid_file = studio_home / "studio.pid"
    pids: set[int] = set()
    command = (
        f"Get-NetTCPConnection -LocalPort {args.port} -State Listen -ErrorAction SilentlyContinue "
        "| ForEach-Object { $_.OwningProcess }"
    )
    for line in powershell(command, check=False).splitlines():
        line = line.strip()
        if line.isdigit():
            pids.add(int(line))
    if pid_file.exists():
        text = pid_file.read_text(encoding="utf-8", errors="ignore").strip()
        if text.isdigit():
            pids.add(int(text))

    if not pids:
        echo(f"No Unsloth Studio server found on port {args.port}.")
        if pid_file.exists():
            pid_file.unlink()
            echo("Removed stale studio.pid.")
        return

    for pid in sorted(pids):
        subprocess.run(["taskkill.exe", "/PID", str(pid), "/T"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    still_listening = powershell(command, check=False).strip()
    if still_listening:
        echo("Forcing shutdown...")
        for pid in sorted(pids):
            subprocess.run(["taskkill.exe", "/F", "/PID", str(pid), "/T"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if pid_file.exists():
        pid_file.unlink()
    echo(f"Stopped Unsloth Studio (port {args.port}).")


def register(args: argparse.Namespace) -> None:
    studio_home = Path(args.studio_home)
    resolve_unsloth_exe(studio_home)
    register_scan_folder(studio_home, Path(args.path))
    echo(f"Registered {args.path}. Refresh the Models page in the web UI to see it.")


def patch_web(args: argparse.Namespace) -> None:
    studio_home = Path(args.studio_home)
    resolve_studio_python(studio_home)
    apply_studio_patch_stack(studio_home, build_frontend=not args.no_build)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="win-models unsloth")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("setup")
    p.add_argument("--studio-home", default=str(DEFAULT_STUDIO_HOME))
    p.add_argument("--model-root", default=str(DEFAULT_MODEL_ROOT))
    p.add_argument("--zip-dir", default=str(Path.home() / "Downloads"))
    p.add_argument("--release-tag", default="b9821")
    p.add_argument("--runtime", default="13.3")
    p.add_argument("--skip-base", action="store_true")
    p.add_argument("--register-model-root", action="store_true")
    p.set_defaults(func=setup)

    p = sub.add_parser("sync-mcp")
    p.add_argument("--studio-home", default=str(DEFAULT_STUDIO_HOME))
    p.add_argument("--config", default=str(DEFAULT_MCP_CONFIG))
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=sync_mcp)

    p = sub.add_parser("serve")
    p.add_argument("--studio-home", default=str(DEFAULT_STUDIO_HOME))
    p.add_argument("--hf-cache-dir", default="")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--model", required=True)
    p.add_argument("--max-seq-length", type=int, required=True)
    p.add_argument("--parallel", type=int, default=1)
    p.add_argument("--cache-type-kv", default="q8_0")
    p.add_argument("--reasoning-format", choices=("none", "deepseek", "deepseek-legacy"), default="deepseek")
    p.add_argument("--speculative-type", choices=("auto", "off", "mtp", "mtp+ngram", "ngram"), default="")
    p.add_argument("--chat-template-file", default="")
    p.add_argument("--port", type=int, default=DEFAULT_STUDIO_PORT)
    p.add_argument("--llama-port", type=int, default=DEFAULT_LLAMA_PORT)
    p.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")
    p.add_argument("--lan", action="store_true")
    p.add_argument("--open", action="store_true")
    p.add_argument("--source-repo", default=os.environ.get("UNSLOTH_SOURCE_REPO", ""))
    p.add_argument(
        "--source-build-frontend",
        action=argparse.BooleanOptionalAction,
        default=os.environ.get("UNSLOTH_SOURCE_BUILD_FRONTEND", "").lower()
        in {"1", "true", "yes", "on"},
    )
    p.add_argument(
        "--source-apply-shims",
        action="store_true",
        help="Apply the win-models shim patch stack even when --source-repo is active.",
    )
    tools_policy = p.add_mutually_exclusive_group()
    tools_policy.add_argument("--enable-tools", dest="enable_tools", action="store_true", default=None)
    tools_policy.add_argument("--disable-tools", dest="enable_tools", action="store_false")
    tools_policy.add_argument("--no-enable-tools", dest="enable_tools", action="store_false", help=argparse.SUPPRESS)
    p.add_argument("--verbose-llama", action="store_true")
    p.add_argument("--patch-web", action=argparse.BooleanOptionalAction, default=False)
    p.set_defaults(func=serve)

    p = sub.add_parser("stop")
    p.add_argument("--studio-home", default=str(DEFAULT_STUDIO_HOME))
    p.add_argument("--port", type=int, default=DEFAULT_STUDIO_PORT)
    p.set_defaults(func=stop)

    p = sub.add_parser("register")
    p.add_argument("path")
    p.add_argument("--studio-home", default=str(DEFAULT_STUDIO_HOME))
    p.set_defaults(func=register)

    p = sub.add_parser("prepare-source")
    p.add_argument("--source-repo", default=os.environ.get("UNSLOTH_SOURCE_REPO", ""))
    p.add_argument("--build-frontend", action="store_true")
    p.set_defaults(func=prepare_source)

    p = sub.add_parser("patch-web")
    p.add_argument("--studio-home", default=str(DEFAULT_STUDIO_HOME))
    p.add_argument("--no-build", action="store_true")
    p.set_defaults(func=patch_web)
    return parser


def main(argv: list[str] | None = None) -> None:
    load_dotenv_secret()
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
