from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

from .common import echo, ensure_dir, open_url, powershell, run
from .config import DEFAULT_MODEL_ROOT, DEFAULT_STUDIO_HOME, DEFAULT_STUDIO_PORT


LOCAL_ZIP_MARKER = "UNSLOTH_LOCAL_ZIP_SHIM"


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


def setup(args: argparse.Namespace) -> None:
    studio_home = Path(args.studio_home)
    model_root = Path(args.model_root)
    zip_dir = Path(args.zip_dir)
    echo("=== Unsloth Studio setup ===")
    echo(f"StudioHome={studio_home}  ModelRoot={model_root}  tag={args.release_tag}  cuda={args.runtime}")

    unsloth = resolve_unsloth_exe(studio_home)
    python = resolve_studio_python(studio_home)
    llama_dir = studio_home / "llama.cpp"

    echo("\n[1/6] Patching llama.cpp prebuilt installer (local-zip shim)...")
    apply_llama_local_zip_shim(studio_home)

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
            "--simple-policy",
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

    echo(f"\n[5/6] Registering model folder {model_root} as a Studio scan folder...")
    register_scan_folder(studio_home, model_root)

    echo("\n[6/6] Installing unsloth.cmd launcher...")
    install_unsloth_wrapper(studio_home)
    echo("\nDone. Launch the web UI with: just unsloth serve")


def serve(args: argparse.Namespace) -> None:
    studio_home = Path(args.studio_home)
    unsloth = resolve_unsloth_exe(studio_home)
    bind_host = "0.0.0.0" if args.lan else args.host
    model = Path(args.model) if args.model else Path(args.model_root) / "gemma-4-12b-it-qat-q4_0" / "gemma-4-12b-it-qat-q4_0.gguf"
    if not model.exists():
        raise FileNotFoundError(f"Model not found: {model}")

    env = os.environ.copy()
    env["UNSLOTH_STUDIO_HOME"] = str(studio_home)
    env["LOG_LEVEL"] = args.log_level
    env["ENVIRONMENT_TYPE"] = "production"

    port_check = powershell(f"Get-NetTCPConnection -LocalPort {args.port} -State Listen -ErrorAction SilentlyContinue", check=False)
    if port_check.strip():
        echo(f"A server is already listening on port {args.port}.")
        if args.open:
            open_url(f"http://localhost:{args.port}")
        return

    command = [unsloth, "studio", "run", "--model", model, "-H", bind_host, "-p", str(args.port), "--yes"]
    if args.enable_tools:
        command.append("--enable-tools")
    else:
        command.append("--disable-tools")
    if args.verbose_llama:
        command.append("--verbose")

    echo(f"Starting Unsloth Studio on http://{bind_host}:{args.port}  (LOG_LEVEL={args.log_level})")
    echo("Backend output will stream in this terminal. Press Ctrl+C to stop.")
    echo(f"llama-server log: {studio_home}\\logs\\llama-server\\llama-*-port-*.log")
    if args.lan:
        echo("Bound to 0.0.0.0; open via localhost on this machine for browser secure-context features.")
    if args.open:
        echo(f"Opening http://localhost:{args.port}; refresh once Studio finishes starting if needed.")
        open_url(f"http://localhost:{args.port}")
    run(command, env=env)


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
    p.add_argument("--release-tag", default="b9536")
    p.add_argument("--runtime", default="13.3")
    p.add_argument("--skip-base", action="store_true")
    p.set_defaults(func=setup)

    p = sub.add_parser("serve")
    p.add_argument("--studio-home", default=str(DEFAULT_STUDIO_HOME))
    p.add_argument("--model-root", default=str(DEFAULT_MODEL_ROOT))
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--model", default="")
    p.add_argument("--port", type=int, default=DEFAULT_STUDIO_PORT)
    p.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")
    p.add_argument("--lan", action="store_true")
    p.add_argument("--open", action="store_true")
    p.add_argument("--enable-tools", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--verbose-llama", action="store_true")
    p.set_defaults(func=serve)

    p = sub.add_parser("stop")
    p.add_argument("--studio-home", default=str(DEFAULT_STUDIO_HOME))
    p.add_argument("--port", type=int, default=DEFAULT_STUDIO_PORT)
    p.set_defaults(func=stop)

    p = sub.add_parser("register")
    p.add_argument("path", nargs="?", default=str(DEFAULT_MODEL_ROOT))
    p.add_argument("--studio-home", default=str(DEFAULT_STUDIO_HOME))
    p.set_defaults(func=register)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
