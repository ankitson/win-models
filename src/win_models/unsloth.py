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
DEFAULT_MCP_CONFIG = (
    Path(__file__).resolve().parents[1] / "unsloth" / "mcp-servers.json"
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
        echo("  source mode active (shims are maintained directly in the source checkout)")
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
    if args.llama_args:
        # Strip leading -- separator if present
        extra = list(args.llama_args)
        if extra and extra[0] == "--":
            extra = extra[1:]
        command.extend(extra)

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
    if args.llama_args:
        echo(f"llama-server extra args: {' '.join(args.llama_args)}")
    if args.speculative_type:
        echo(f"Studio speculative decoding mode: {args.speculative_type}")
    if embed_args:
        echo(f"llama-server embedding mode: {' '.join(embed_args)}")
    if args.chat_template_file:
        echo(f"Studio chat template override: {Path(args.chat_template_file).resolve()}")
    if (env.get("UNSLOTH_PROMPT_LOG") or "").strip().lower() in {"1", "true", "yes", "on"}:
        prompt_log_file = env.get("UNSLOTH_PROMPT_LOG_FILE") or str(
            studio_home / "logs" / "unsloth-prompts.jsonl"
        )
        echo(f"Studio prompt input log: {prompt_log_file}")
    if source_repo is not None:
        echo(f"Unsloth source mode: {source_repo}")
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

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="win-models unsloth")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("setup")
    p.add_argument("--studio-home", default=str(DEFAULT_STUDIO_HOME))
    p.add_argument("--model-root", default=str(DEFAULT_MODEL_ROOT))
    p.add_argument("--zip-dir", default=str(Path.home() / "Downloads"))
    p.add_argument("--release-tag", default="b9878")
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
    tools_policy = p.add_mutually_exclusive_group()
    tools_policy.add_argument("--enable-tools", dest="enable_tools", action="store_true", default=None)
    tools_policy.add_argument("--disable-tools", dest="enable_tools", action="store_false")
    tools_policy.add_argument("--no-enable-tools", dest="enable_tools", action="store_false", help=argparse.SUPPRESS)
    p.add_argument("--verbose-llama", action="store_true")
    p.add_argument("llama_args", nargs=argparse.REMAINDER, help="Extra args passed directly to llama-server (after --)")
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
    return parser

def main(argv: list[str] | None = None) -> None:
    load_dotenv_secret()
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)