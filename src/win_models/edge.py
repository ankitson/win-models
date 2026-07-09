"""Edge server manager — start/stop for the full local stack.

Replaces start-edge.ps1 / stop-edge.ps1 with pure Python to avoid
nested PowerShell hangs (Get-CimInstance, pipeline deadlocks, etc.).

Process lifecycle is handled by :mod:`win_models.lifecycle` which is
built on psutil — no ``taskkill`` / ``netstat`` / ``tasklist`` shell-outs.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import time
from pathlib import Path

from dotenv import load_dotenv

from .common import echo
from .config import (
    DEFAULT_HOST,
    DEFAULT_LLAMA_PORT,
    DEFAULT_MODEL_ROOT,
    DEFAULT_STUDIO_HOME,
    DEFAULT_STUDIO_PORT,
    REPO_ROOT,
    get_default_model_key,
    get_model_config,
    load_model_configs,
)
from .lifecycle import (
    ManagedProcess,
    PidFile,
    pids_by_name,
    pids_on_port,
    stop_by_name,
    stop_on_port,
)

_NO_WINDOW = (
    subprocess.CREATE_NO_WINDOW
    if hasattr(subprocess, "CREATE_NO_WINDOW")
    else 0
)


# ── .env.secret loader (ports scripts/Import-DotEnvSecret.ps1) ──────────────

_ENV_SECRET_PATH = REPO_ROOT / ".env.secret"


def _load_dotenv_secret(path: Path = _ENV_SECRET_PATH) -> None:
    """Load .env.secret into os.environ via python-dotenv (no overrides)."""
    if not path.is_file():
        return
    load_dotenv(path, override=False)


# ── Stop ────────────────────────────────────────────────────────────────────


def stop(args: argparse.Namespace) -> None:
    """Stop the full edge stack: Studio, Caddy, llama-server, etc."""
    try:
        ports: set[int] = set()
        if args.port:
            ports.add(args.port)
        if args.llama_port:
            ports.add(args.llama_port)
        if args.lmstudio_port:
            ports.add(args.lmstudio_port)
        if args.asr_port:
            ports.add(args.asr_port)
        if args.comfy_port:
            ports.add(args.comfy_port)
        if args.caddy or getattr(args, "all", False):
            ports.add(443)
            ports.add(80)

        # 1. Kill by port (fast, targeted)
        if ports:
            echo(f"Looking for processes on ports: {sorted(ports)}")
            for port in sorted(ports):
                stop_on_port(port)

        # 2. Kill by known process names (catches orphans not listening)
        for name in ("llama-server", "caddy", "litert-lm"):
            stop_by_name(name)

    except Exception:
        import traceback

        echo(f"Error in stop: {traceback.format_exc()}")
        # Best-effort: always exit clean
        raise SystemExit(0)


# ── Start ───────────────────────────────────────────────────────────────────


def start(args: argparse.Namespace) -> None:
    """Start the full edge stack: Studio, Caddy, LM Studio, ASR, ComfyUI."""
    _load_dotenv_secret()

    log_dir = REPO_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    studio_home = Path(args.studio_home) if args.studio_home else DEFAULT_STUDIO_HOME
    model_root = Path(args.model_root) if args.model_root else DEFAULT_MODEL_ROOT
    hf_cache = Path(args.hf_cache) if args.hf_cache else model_root

    # ── Load model config ──────────────────────────────────────────────
    model_key = args.model_key or get_default_model_key()
    cfg = get_model_config(model_key) if model_key else None
    if cfg is None:
        model_ref = (
            os.environ.get("UNSLOTH_DEFAULT_MODEL")
            or "unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_XL"
        )
        ctx = int(os.environ.get("UNSLOTH_CONTEXT_LENGTH", "131072"))
        cache_kv = os.environ.get("UNSLOTH_CACHE_TYPE_KV", "q8_0")
        reason_fmt = os.environ.get("UNSLOTH_REASONING_FORMAT", "deepseek")
    else:
        model_ref = cfg.hf_model_ref
        ctx = cfg.context_length
        cache_kv = cfg.cache_type_kv
        reason_fmt = cfg.reasoning_format

    echo(f"Model: {model_ref} ({model_key or 'default'})")
    echo(f"Context: {ctx}, Cache KV: {cache_kv}, Reasoning: {reason_fmt}")

    # ── Propagate env vars to children (matching start-edge.ps1) ──────
    os.environ.setdefault("UNSLOTH_PROMPT_LOG", "1")
    os.environ.setdefault("UNSLOTH_PROMPT_LOG_FILE", str(log_dir / "unsloth-prompts.jsonl"))
    os.environ.setdefault("WIN_MODELS_LLAMA_LOG", "1")
    os.environ.setdefault("WIN_MODELS_LLAMA_LOG_FILE", str(log_dir / "llama-io.jsonl"))
    os.environ.setdefault("UNSLOTH_LLAMA_LOG_DIR", str(log_dir / "llama-server"))

    # ── Stop anything already running ─────────────────────────────────
    stop(
        argparse.Namespace(
            port=args.studio_port,
            llama_port=args.llama_port,
            lmstudio_port=args.lmstudio_port,
            asr_port=args.asr_port,
            comfy_port=args.comfy_port,
            caddy=args.caddy,
            all=False,
        )
    )

    # ── Launch services ───────────────────────────────────────────────
    processes: list[ManagedProcess] = []

    # ── Unsloth Studio ───────────────────────────────────────────────
    if args.studio:
        # Sync MCP config before starting Studio (matching start-edge.ps1 line 62)
        echo("Syncing MCP config...")
        _run_cli("unsloth", "sync-mcp", "--studio-home", str(studio_home))

        py = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
        if not py.exists():
            echo(f"Error: venv python not found at {py}")
        else:
            unsloth_cmd = [
                str(py),
                "-m", "win_models.cli",
                "unsloth", "serve",
                "--studio-home", str(studio_home),
                "--hf-cache-dir", str(hf_cache),
                "--model", model_ref,
                "--max-seq-length", str(ctx),
                "--parallel", str(args.parallel),
                "--reasoning-format", reason_fmt,
                "--port", str(args.studio_port),
            ]
            if args.llama_port:
                unsloth_cmd += ["--llama-port", str(args.llama_port)]
            if args.chat_template:
                unsloth_cmd += ["--chat-template-file", args.chat_template]
            if args.source_repo:
                unsloth_cmd += ["--source-repo", args.source_repo]
            if args.source_build:
                unsloth_cmd += ["--source-build-frontend"]
            # Pass speculative type if set and not "off" (matches start-edge.ps1 line 263)
            speculative_type = os.environ.get("UNSLOTH_SPECULATIVE_TYPE", "off")
            if speculative_type and speculative_type.lower() != "off":
                unsloth_cmd += ["--speculative-type", speculative_type]
            # Pass extra llama-server args after -- (matches start-edge.ps1 line 273-275).
            # Always include --log-timestamps so llama-server output is timestamped,
            # and --log-prompts-dir to keep prompt logs inside the repo's log dir.
            llama_extra = [
                "--log-timestamps",
                "--log-prompts-dir", str(log_dir / "llama-prompts"),
            ]
            # Per-model config takes priority; env var is the fallback.
            user_extra = cfg.llama_extra_args if cfg and cfg.llama_extra_args else os.environ.get("UNSLOTH_LLAMA_EXTRA_ARGS", "")
            if user_extra:
                llama_extra += user_extra.split()
            unsloth_cmd += ["--"] + llama_extra
            proc = ManagedProcess(
                args=unsloth_cmd,
                log_name="edge-unsloth",
                log_dir=log_dir,
                pid_file=log_dir / "edge-unsloth.pid",
            )
            proc.start()
            processes.append(proc)

    # ── Caddy reverse proxy ───────────────────────────────────────────
    if args.caddy:
        caddy_bin = _find_caddy()
        if not caddy_bin:
            echo("Warning: Caddy not found on PATH.")
        elif not (caddy_config := REPO_ROOT / "Caddyfile").exists():
            echo(f"Error: Caddyfile not found at {caddy_config}")
        elif not os.environ.get("CLOUDFLARE_API_TOKEN"):
            echo("Error: CLOUDFLARE_API_TOKEN is not set — Caddy cannot solve"
                 " the DNS-01 challenge. Load .env.secret (or export it) and re-run.")
        else:
            caddyfile = str(REPO_ROOT / "Caddyfile")
            # Validate the config so a typo doesn't start a Caddy that dies silently.
            validation = _run_caddy_validate(caddy_bin, caddyfile)
            if validation is not None:
                echo(f"Error: Caddyfile failed validation:\n{validation}")
            else:
                proc = ManagedProcess(
                    args=[caddy_bin, "run", "--config", caddyfile],
                    log_name="caddy",
                    log_dir=log_dir,
                    pid_file=log_dir / "caddy.pid",
                )
                proc.start()
                processes.append(proc)

    # ── LM Studio ──────────────────────────────────────────────────────
    if args.lmstudio:
        # Sync models before serving (matching start-edge.ps1 lines 64-70)
        echo("Syncing LM Studio models...")
        _run_cli("lmstudio", "setup")

        py = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
        if py.exists():
            proc = ManagedProcess(
                args=[
                    str(py),
                    "-m", "win_models.cli",
                    "lmstudio", "serve",
                    "--host", args.lmstudio_host,
                    "--port", str(args.lmstudio_port),
                ],
                log_name="lmstudio",
                log_dir=log_dir,
                pid_file=log_dir / "lmstudio.pid",
            )
            proc.start()
            processes.append(proc)

    # ── Parakeet ASR ──────────────────────────────────────────────────
    if args.asr:
        asr_home = (
            Path(args.asr_home) if args.asr_home else model_root.parent / "parakeet-asr"
        )
        asr_py = asr_home / ".venv" / "Scripts" / "python.exe"
        if asr_py.exists():
            proc = ManagedProcess(
                args=[
                    str(asr_py),
                    "-m", "win_models.parakeet_asr",
                    "serve",
                    "--host", args.asr_host,
                    "--port", str(args.asr_port),
                    "--model", args.asr_model,
                    "--cache-dir", str(hf_cache),
                ],
                log="parakeet-asr",
                log_dir=log_dir,
                pid_file=log_dir / "parakeet-asr.pid",
            )
            proc.start()
            processes.append(proc)

    # ── ComfyUI ────────────────────────────────────────────────────────
    # Disabled by default (matching start-edge.ps1 lines 146-158 which are
    # commented out).  Enable via --comfy or WIN_MODELS_COMFYUI_ENABLED=1.
    comfy_enabled = args.comfy or os.environ.get("WIN_MODELS_COMFYUI_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
    if comfy_enabled:
        comfy_home = Path(args.comfy_home) if args.comfy_home else _default_comfy_home()
        comfy_model_root = Path(args.comfy_model_root) if args.comfy_model_root else model_root / "comfyui"
        comfy_port_str = os.environ.get("COMFYUI_PORT", "8188")
        comfy_memory = args.comfy_memory_mode or os.environ.get("COMFYUI_MEMORY", "auto")
        py = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
        if not py.exists():
            echo(f"Error: venv python not found at {py}")
        elif not comfy_home.exists():
            echo(f"Warning: ComfyUI home not found at {comfy_home} — skipping ComfyUI.")
        else:
            proc = ManagedProcess(
                args=[
                    str(py),
                    "-m", "win_models.cli",
                    "comfy", "serve",
                    "--comfy-home", str(comfy_home),
                    "--model-root", str(comfy_model_root),
                    "--host", "127.0.0.1",
                    "--port", comfy_port_str,
                    "--log-dir", str(log_dir),
                    "--memory-mode", comfy_memory,
                ],
                log_name="comfyui",
                log_dir=log_dir,
                pid_file=log_dir / "comfyui.pid",
            )
            proc.start()
            processes.append(proc)

    # ── OpenCode headless server ──────────────────────────────────────
    # Always enabled unless explicitly disabled (matching start-edge.ps1 lines 89-96)
    opencode_enabled = os.environ.get("WIN_MODELS_OPENCODE_ENABLED", "1")
    if opencode_enabled.lower() not in {"0", "false", "no", "off"}:
        echo("Starting OpenCode server...")
        opencode_script = REPO_ROOT / "scripts" / "start-opencode.ps1"
        if opencode_script.exists():
            proc = ManagedProcess(
                args=[
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy", "Bypass",
                    "-File", str(opencode_script),
                ],
                log_name="opencode",
                log_dir=log_dir,
                pid_file=log_dir / "opencode.pid",
            )
            proc.start()
            processes.append(proc)
        else:
            echo(f"Warning: OpenCode script not found at {opencode_script}")

    # ── Report ─────────────────────────────────────────────────────────
    echo(f"\nEdge stack started. Logs: {log_dir}")
    if processes:
        echo("Running PIDs: " + ", ".join(str(p.pid) for p in processes if p.pid))

    # ── Liveness check ─────────────────────────────────────────────────
    time.sleep(2)
    dead = [p for p in processes if not p.is_alive()]
    if dead:
        echo("\nWarning: some services failed to stay running:")
        for p in dead:
            echo(f"  - {p}")
        echo("Check the .err.log files in the logs/ directory:")
        echo(f"  {log_dir}")
    else:
        echo("All services still running after 2s.")


def _run_cli(*cli_args: str) -> int:
    """Run a win-models CLI subcommand synchronously, returning exit code.

    Used for pre-start setup steps like ``unsloth sync-mcp`` and
    ``lmstudio setup`` that must complete before the long-lived
    services are launched.
    """
    py = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        echo(f"Error: venv python not found at {py}")
        return 1
    cmd = [str(py), "-m", "win_models.cli", *cli_args]
    echo(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, creationflags=_NO_WINDOW)
    return result.returncode


def _default_comfy_home() -> Path:
    """Return the ComfyUI home directory (from env or hardcoded default)."""
    from .config import DEFAULT_COMFYUI_HOME
    return Path(os.environ.get("COMFYUI_HOME", str(DEFAULT_COMFYUI_HOME)))


def _find_caddy() -> str | None:
    """Locate the caddy binary on PATH."""
    from shutil import which
    return which("caddy")


def _run_caddy_validate(caddy_bin: str, config: str) -> str | None:
    """Run ``caddy validate``; returns error text or None on success."""
    import subprocess
    try:
        result = subprocess.run(
            [caddy_bin, "validate", "--config", config],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=15,
            creationflags=_NO_WINDOW,
        )
        if result.returncode == 0:
            return None
        return result.stdout.strip()
    except Exception as exc:
        return str(exc)


# ── Parser ──────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="win-models edge")

    sub = parser.add_subparsers(dest="command", required=True)

    # ── stop ───────────────────────────────────────────────────────────
    sp = sub.add_parser("stop", help="Stop all edge services")
    sp.add_argument("--port", type=int, default=None, help="Studio port")
    sp.add_argument("--llama-port", type=int, default=None)
    sp.add_argument("--lmstudio-port", type=int, default=None)
    sp.add_argument("--asr-port", type=int, default=None)
    sp.add_argument("--comfy-port", type=int, default=None)
    sp.add_argument("--caddy", action="store_true", default=False,
                    help="Also stop Caddy reverse proxy")
    sp.add_argument("--all", action="store_true", default=False,
                    help="Stop everything including Caddy and OpenCode")
    sp.add_argument("--kill-python", action="store_true", default=False,
                    help=argparse.SUPPRESS)  # deprecated, no-op
    _apply_default_ports(sp)
    sp.set_defaults(func=stop)

    # ── start ───────────────────────────────────────────────────────────
    sp = sub.add_parser("start", help="Start all edge services")
    sp.add_argument("--model-key", default=None,
                    help="Model key from models-config.json")
    sp.add_argument("--studio", action="store_true", default=True)
    sp.add_argument("--no-studio", action="store_false", dest="studio")
    sp.add_argument("--studio-home", default=None)
    sp.add_argument("--model-root", default=None)
    sp.add_argument("--hf-cache", default=None)
    sp.add_argument("--caddy", action="store_true", default=True)
    sp.add_argument("--no-caddy", action="store_false", dest="caddy")
    sp.add_argument("--lmstudio", action="store_true", default=False)
    sp.add_argument("--asr", action="store_true", default=False)
    sp.add_argument("--parallel", type=int, default=1)
    sp.add_argument("--source-repo", default=None)
    sp.add_argument("--source-build", action="store_true")
    sp.add_argument("--chat-template", default=None)
    sp.add_argument("--lmstudio-host", default="127.0.0.1")
    sp.add_argument("--asr-home", default=None)
    sp.add_argument("--asr-host", default="127.0.0.1")
    sp.add_argument("--asr-model", default="nvidia/parakeet-tdt-0.6b-v2")
    sp.add_argument("--comfy", action="store_true", default=False,
                    help="Also start ComfyUI (disabled by default)")
    sp.add_argument("--no-comfy", action="store_false", dest="comfy")
    sp.add_argument("--comfy-home", default=None)
    sp.add_argument("--comfy-model-root", default=None)
    sp.add_argument("--comfy-memory-mode", default=None)
    _apply_default_ports(sp)
    sp.set_defaults(func=start)

    return parser


def _apply_default_ports(parser: argparse.ArgumentParser) -> None:
    """Set default port values from config/env."""
    parser.set_defaults(
        port=int(os.environ.get("WIN_MODELS_STUDIO_PORT", "8888")),
        studio_port=int(os.environ.get("WIN_MODELS_STUDIO_PORT", "8888")),
        llama_port=int(os.environ.get("WIN_MODELS_LLAMA_PORT", "8080")),
        lmstudio_port=int(os.environ.get("LMSTUDIO_PORT", "1234")),
        asr_port=int(os.environ.get("WIN_MODELS_ASR_PORT", "8891")),
        comfy_port=int(os.environ.get("COMFYUI_PORT", "8188")),
    )


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except SystemExit:
        raise
    except Exception as exc:
        echo(f"Error: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
