"""Edge server manager — start/stop for the full local stack.

Replaces start-edge.ps1 / stop-edge.ps1 with pure Python to avoid
nested PowerShell hangs (Get-CimInstance, pipeline deadlocks, etc.).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

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

# ── Port & process helpers (no PowerShell) ──────────────────────────────


def _netstat_ano(timeout: float = 10.0) -> list[dict[str, Any]]:
    """Parse `netstat -ano` output into structured rows.

    Returns list of {proto, local_addr, local_port, state, pid}.
    No PowerShell involved — plain old `netstat`.
    """
    try:
        completed = subprocess.run(
            ["netstat.exe", "-ano"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
    except subprocess.TimeoutExpired:
        echo("Warning: netstat -ano timed out (10s). No port info available.")
        return []
    except FileNotFoundError:
        echo("Warning: netstat.exe not found.")
        return []
    rows: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        parts = line.strip().split()
        if len(parts) < 4:
            continue
        proto = parts[0].upper()
        if proto not in ("TCP", "UDP"):
            continue
        local = parts[1]
        state = parts[3] if proto == "TCP" else ""
        pid_str = parts[-1]
        if not pid_str.isdigit():
            continue
        # Split local into addr:port
        if "[" in local:
            # IPv6: [::1]:8080
            match = re.match(r"\[(.+)\]:(\d+)", local)
            if not match:
                continue
            addr, port = match.group(1), int(match.group(2))
        else:
            last_colon = local.rfind(":")
            if last_colon < 0:
                continue
            addr, port = local[:last_colon], int(local[last_colon + 1 :])
        rows.append({"proto": proto, "addr": addr, "port": port, "state": state, "pid": int(pid_str)})
    return rows


def _find_pids_by_port(ports: set[int]) -> set[int]:
    """Find PIDs listening on any of the given ports."""
    pids: set[int] = set()
    for row in _netstat_ano():
        if row["port"] in ports and row["state"] in ("LISTEN", ""):
            pids.add(row["pid"])
    return pids


def _find_pids_by_name(names: set[str], timeout: float = 10.0) -> set[int]:
    """Find PIDs whose process name matches (tasklist /FI)."""
    pids: set[int] = set()
    for name in names:
        try:
            completed = subprocess.run(
                ["tasklist.exe", "/FI", f"IMAGENAME eq {name}", "/NH"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
        except subprocess.TimeoutExpired:
            echo(f"Warning: tasklist for {name} timed out.")
            continue
        except FileNotFoundError:
            echo("Warning: tasklist.exe not found.")
            return pids
        for line in completed.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[0].lower() == name.lower() and parts[1].isdigit():
                pids.add(int(parts[1]))
    return pids


def _kill_pids(pids: set[int], *, force: bool = False, timeout: float = 10.0) -> list[int]:
    """Kill PIDs with taskkill. Returns surviving PIDs."""
    if not pids:
        return []
    for pid in sorted(pids):
        args = ["taskkill.exe"]
        if force:
            args.append("/F")
        args.extend(["/PID", str(pid)])
        try:
            subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)
        except subprocess.TimeoutExpired:
            echo(f"Warning: taskkill /PID {pid} timed out.")
    # Small wait for processes to exit
    if force:
        time.sleep(0.5)
    else:
        time.sleep(1)
    # Check survivors
    survivors: list[int] = []
    for pid in sorted(pids):
        try:
            completed = subprocess.run(
                ["tasklist.exe", "/FI", f"PID eq {pid}", "/NH"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
            )
            if str(pid) in completed.stdout:
                survivors.append(pid)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
    return survivors


def _kill_by_port(ports: set[int], *, process_name: str | None = None) -> None:
    """Kill all processes listening on the given ports."""
    if not ports:
        return
    pids = _find_pids_by_port(ports)
    if not pids:
        # Maybe the process isn't listening anymore — try name
        if process_name:
            pids = _find_pids_by_name({process_name})
    if not pids:
        return

    echo(f"  Killing {len(pids)} process(es) on port(s) {sorted(ports)}...")
    survivors = _kill_pids(pids, force=True)
    if survivors:
        echo(f"  Warning: {len(survivors)} PID(s) survived forced kill: {survivors}")


def _kill_by_name(names: set[str]) -> None:
    """Kill all processes with the given image names."""
    if not names:
        return
    pids = _find_pids_by_name(names)
    if not pids:
        return
    # Never kill ourselves or our parent tooling
    own_pid = os.getpid()
    pids.discard(own_pid)
    # Also exclude common parent tool processes
    tool_pids = _find_pids_by_name({"uv.exe", "just.exe", "powershell.exe"})
    for tool_pid in tool_pids:
        pids.discard(tool_pid)
    if not pids:
        return
    echo(f"  Killing {len(pids)} process(es) by name: {sorted(names)}...")
    survivors = _kill_pids(pids, force=True)
    if survivors:
        echo(f"  Warning: {len(survivors)} PID(s) survived: {survivors}")


# ── Stop ────────────────────────────────────────────────────────────────


def stop(args: argparse.Namespace) -> None:
    """Stop the full edge stack: Unsloth, llama-server, Caddy, LM Studio, ASR, ComfyUI."""
    try:
        _stop_impl(args)
    except SystemExit:
        # SystemExit(0) from _stop_impl is intentional
        raise
    except BaseException:
        import traceback
        echo(f"Error in stop: {traceback.format_exc()}")
        # Best-effort: always exit clean
        raise SystemExit(0)


def _stop_impl(args: argparse.Namespace) -> None:
    ports: set[int] = set()

    if args.port:
        ports.add(args.port)  # Studio
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
        _kill_by_port(ports)

    # 2. Kill by known process names (no python.exe — handled by port + repo scan)
    proc_names = {"llama-server.exe", "caddy.exe", "litert-lm.exe"}
    _kill_by_name(proc_names)

    # 3. Done with targeted kills — port and name-based kills cover all known services.

    # 4. Caddy: only if explicitly requested (--caddy or --all)
    if args.caddy or getattr(args, "all", False):
        caddy = _find_caddy()
        if caddy:
            echo("Stopping Caddy gracefully...")
            subprocess.run(
                [caddy, "stop"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            time.sleep(1)
            survivors = _kill_by_port({443, 80}, process_name="caddy.exe")

    echo("Done.")


def _find_caddy(timeout: float = 5.0) -> str | None:
    """Locate the caddy binary."""
    try:
        completed = subprocess.run(
            ["where.exe", "caddy"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        path = completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else None
        return path if path and Path(path).exists() else None
    except Exception:
        return None


# ── Start ───────────────────────────────────────────────────────────────


def start(args: argparse.Namespace) -> None:
    """Start the full edge stack: Unsloth, Caddy, LM Studio, ASR, ComfyUI."""
    studio_home = Path(args.studio_home) if args.studio_home else DEFAULT_STUDIO_HOME
    model_root = Path(args.model_root) if args.model_root else DEFAULT_MODEL_ROOT
    hf_cache = Path(args.hf_cache) if args.hf_cache else model_root
    log_dir = REPO_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # ── Load model config ──────────────────────────────────────────────
    model_key = args.model_key or get_default_model_key()
    cfg = get_model_config(model_key) if model_key else None
    if cfg is None:
        # Fallback to env or hardcoded default
        model_ref = os.environ.get("UNSLOTH_DEFAULT_MODEL") or "unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_XL"
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

    # ── Validate Caddy ─────────────────────────────────────────────────
    caddy_bin = _find_caddy()
    if not caddy_bin and args.caddy:
        echo("Warning: Caddy not found on PATH.")
        args.caddy = False

    # ── Stop anything already running ──────────────────────────────────
    stop(argparse.Namespace(
        port=args.studio_port,
        llama_port=args.llama_port,
        lmstudio_port=args.lmstudio_port,
        asr_port=args.asr_port,
        comfy_port=args.comfy_port,
        caddy=args.caddy,
        kill_python=False,
        all=False,
    ))

    # ── Background process launcher ────────────────────────────────────
    processes: list[subprocess.Popen] = []

    def _bg(args_list: list[str], log_name: str, cwd: Path | None = None) -> subprocess.Popen:
        out_log = log_dir / f"{log_name}.out.log"
        err_log = log_dir / f"{log_name}.err.log"
        out_fh = open(out_log, "a", encoding="utf-8")
        err_fh = open(err_log, "a", encoding="utf-8")
        proc = subprocess.Popen(
            args_list,
            stdout=out_fh,
            stderr=err_fh,
            cwd=cwd or str(REPO_ROOT),
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        processes.append(proc)
        echo(f"  Started {log_name} (PID {proc.pid}) -> {out_log}")
        return proc

    # ── Unsloth Studio ─────────────────────────────────────────────────
    if args.studio:
        py = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
        if not py.exists():
            echo(f"Error: venv python not found at {py}")
        else:
            unsloth_args = [
                str(py), "-m", "win_models.cli",
                "unsloth", "serve",
                "--studio-home", str(studio_home),
                "--hf-cache-dir", str(hf_cache),
                "--model", model_ref,
                "--max-seq-length", str(ctx),
                "--parallel", str(args.parallel),
                "--cache-type-kv", cache_kv,
                "--reasoning-format", reason_fmt,
                "--port", str(args.studio_port),
            ]
            if args.llama_port:
                unsloth_args.extend(["--llama-port", str(args.llama_port)])
            if args.chat_template:
                unsloth_args.extend(["--chat-template-file", args.chat_template])
            if args.source_repo:
                unsloth_args.extend(["--source-repo", args.source_repo])
            if args.source_build:
                unsloth_args.append("--source-build-frontend")
            _bg(unsloth_args, "edge-unsloth")

    # ── Caddy reverse proxy ────────────────────────────────────────────
    if args.caddy and caddy_bin:
        caddy_config = REPO_ROOT / "Caddyfile"
        if caddy_config.exists():
            _bg([caddy_bin, "run", "--config", str(caddy_config)], "caddy")

    # ── LM Studio ──────────────────────────────────────────────────────
    if args.lmstudio:
        py = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
        if py.exists():
            _bg([
                str(py), "-m", "win_models.cli",
                "lmstudio", "serve",
                "--host", args.lmstudio_host,
                "--port", str(args.lmstudio_port),
            ], "lmstudio")

    # ── Parakeet ASR ───────────────────────────────────────────────────
    if args.asr:
        asr_home = Path(args.asr_home) if args.asr_home else model_root.parent / "parakeet-asr"
        asr_py = asr_home / ".venv" / "Scripts" / "python.exe"
        if asr_py.exists():
            _bg([
                str(asr_py), "-m", "win_models.parakeet_asr",
                "serve",
                "--host", args.asr_host,
                "--port", str(args.asr_port),
                "--model", args.asr_model,
                "--cache-dir", str(hf_cache),
            ], "parakeet-asr")

    echo(f"\nEdge stack started. Logs: {log_dir}")
    if processes:
        echo("Running PIDs: " + ", ".join(str(p.pid) for p in processes))


# ── Parser ──────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="win-models edge")

    sub = parser.add_subparsers(dest="command", required=True)

    # ── stop ───────────────────────────────────────────────────────────
    sp = sub.add_parser("stop", help="Stop all edge services")
    sp.add_argument("--port", type=int, default=None, help="Studio port (default: from config)")
    sp.add_argument("--llama-port", type=int, default=None)
    sp.add_argument("--lmstudio-port", type=int, default=None)
    sp.add_argument("--asr-port", type=int, default=None)
    sp.add_argument("--comfy-port", type=int, default=None)
    sp.add_argument("--caddy", action="store_true", default=False, help="Also stop Caddy reverse proxy")
    sp.add_argument("--all", action="store_true", default=False, help="Stop everything including Caddy and OpenCode")
    sp.add_argument("--kill-python", action="store_true", default=False,
                    help=argparse.SUPPRESS)  # deprecated, no-op
    _apply_default_ports(sp)
    sp.set_defaults(func=stop)

    # ── start ──────────────────────────────────────────────────────────
    sp = sub.add_parser("start", help="Start all edge services")
    sp.add_argument("--model-key", default=None,
                    help="Model key from models-config.json (default: config default)")
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
