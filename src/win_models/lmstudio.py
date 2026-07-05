from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .common import echo, ensure_dir, open_url, wait_openai_server
from .config import DEFAULT_MODEL_ROOT


DEFAULT_LMSTUDIO_HOST = os.environ.get("LMSTUDIO_HOST", "127.0.0.1")
DEFAULT_LMSTUDIO_PORT = int(os.environ.get("LMSTUDIO_PORT", "1234"))
DEFAULT_LMSTUDIO_MODEL_ROOT = Path(
    os.environ.get("LMSTUDIO_MODEL_ROOT", str(Path.home() / ".lmstudio" / "models"))
)
DEFAULT_LMSTUDIO_CONTEXT_LENGTH = int(
    os.environ.get("LMSTUDIO_CONTEXT_LENGTH", os.environ.get("UNSLOTH_CONTEXT_LENGTH", "131072"))
)
DEFAULT_LMSTUDIO_GPU = os.environ.get("LMSTUDIO_GPU", "max")
DEFAULT_LMSTUDIO_TTL = os.environ.get("LMSTUDIO_TTL", "")


@dataclass(frozen=True)
class GgufModel:
    source: Path
    publisher: str
    repo: str
    filename: str

    @property
    def repo_id(self) -> str:
        return f"{self.publisher}/{self.repo}"

    def target_path(self, lmstudio_model_root: Path) -> Path:
        return lmstudio_model_root / self.publisher / self.repo / self.filename


def default_lmstudio_models_dir() -> Path:
    return Path.home() / ".lmstudio" / "models"


def find_lms() -> str | None:
    found = shutil.which("lms")
    if found:
        return found
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "LM Studio" / "lms.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "LM Studio" / "resources" / "app" / ".webpack" / "lms.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def require_lms() -> str:
    lms = find_lms()
    if lms:
        return lms
    raise RuntimeError(
        "LM Studio CLI `lms` was not found. Install LM Studio, launch it once, "
        "then make sure `lms` is on PATH."
    )


def run_lms(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    lms = require_lms()
    completed = subprocess.run(
        [lms, *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if completed.stdout:
        echo(completed.stdout.rstrip())
    if check and completed.returncode != 0:
        raise SystemExit(completed.returncode)
    return completed


def hf_repo_from_cache_dir(path: Path) -> tuple[str, str] | None:
    for parent in [path.parent, *path.parents]:
        name = parent.name
        if not name.startswith("models--"):
            continue
        parts = name.split("--")
        if len(parts) < 3:
            return None
        return parts[1], "--".join(parts[2:])
    return None


def is_mtp_gguf(path: Path) -> bool:
    filename = path.name.lower()
    return (
        filename.startswith("mtp-")
        or filename.endswith("-mtp.gguf")
        or any(part.lower() == "mtp" for part in path.parts)
    )


def discover_hf_cache_ggufs(
    hf_cache: Path,
    *,
    include_mmproj: bool,
    include_mtp: bool,
) -> list[GgufModel]:
    if not hf_cache.exists():
        return []
    latest_by_target: dict[tuple[str, str, str], GgufModel] = {}
    mtimes: dict[tuple[str, str, str], float] = {}
    for source in hf_cache.glob("models--*--*/snapshots/**/*.gguf"):
        if not source.is_file():
            continue
        filename = source.name
        if not include_mmproj and "mmproj" in filename.lower():
            continue
        if not include_mtp and is_mtp_gguf(source):
            continue
        repo = hf_repo_from_cache_dir(source)
        if repo is None:
            continue
        publisher, model_repo = repo
        key = (publisher, model_repo, filename)
        try:
            mtime = source.stat().st_mtime
        except OSError:
            mtime = 0.0
        if key not in latest_by_target or mtime >= mtimes[key]:
            latest_by_target[key] = GgufModel(
                source=source,
                publisher=publisher,
                repo=model_repo,
                filename=filename,
            )
            mtimes[key] = mtime
    return sorted(
        latest_by_target.values(),
        key=lambda item: (item.publisher.lower(), item.repo.lower(), item.filename.lower()),
    )


def same_file_or_link(source: Path, target: Path) -> bool:
    if not target.exists():
        return False
    try:
        if target.is_symlink() and target.resolve() == source.resolve():
            return True
    except OSError:
        pass
    try:
        return os.path.samefile(source, target)
    except OSError:
        return False


def link_or_copy(source: Path, target: Path, mode: str) -> str:
    if same_file_or_link(source, target):
        return "exists"
    if target.exists():
        return "skip-existing"
    if mode == "junction":
        return create_repo_junction(source.parent, target.parent)
    ensure_dir(target.parent)
    if mode == "copy":
        shutil.copy2(source, target)
        return "copied"
    if mode == "hardlink":
        os.link(source, target)
        return "hardlinked"
    if mode == "symlink":
        os.symlink(source, target)
        return "symlinked"

    errors: list[str] = []
    for candidate in ("symlink", "hardlink", "junction"):
        try:
            return link_or_copy(source, target, candidate)
        except OSError as exc:
            errors.append(f"{candidate}: {exc}")
            if target.exists():
                try:
                    target.unlink()
                except OSError:
                    pass
    echo(f"failed: {target} -> {source}; " + "; ".join(errors))
    return "failed"


def create_repo_junction(source_dir: Path, target_dir: Path) -> str:
    if target_dir.exists():
        try:
            if target_dir.resolve() == source_dir.resolve():
                return "exists"
        except OSError:
            pass
        try:
            next(target_dir.iterdir())
        except StopIteration:
            target_dir.rmdir()
        except OSError:
            return "skip-existing"
        else:
            return "skip-existing"
    ensure_dir(target_dir.parent)
    completed = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(target_dir), str(source_dir)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if completed.returncode != 0:
        raise OSError(completed.stdout.strip())
    return "junctioned"


def maybe_link_default_models_dir(lmstudio_model_root: Path) -> None:
    default_dir = default_lmstudio_models_dir()
    if default_dir.exists():
        echo(f"LM Studio default model dir already exists: {default_dir}")
        if default_dir.resolve() != lmstudio_model_root.resolve():
            echo(
                "  If LM Studio does not show synced models, set its My Models directory "
                f"to {lmstudio_model_root}."
            )
        return

    ensure_dir(default_dir.parent)
    completed = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(default_dir), str(lmstudio_model_root)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if completed.returncode == 0:
        echo(f"Linked LM Studio default model dir: {default_dir} -> {lmstudio_model_root}")
    else:
        echo(
            f"Could not create LM Studio default model dir junction at {default_dir}.\n"
            f"{completed.stdout.strip()}\n"
            f"Set LM Studio's My Models directory to {lmstudio_model_root} manually."
        )


def sync_models(args: argparse.Namespace) -> None:
    hf_cache = Path(args.hf_cache)
    lmstudio_model_root = Path(args.lmstudio_model_root)
    ensure_dir(lmstudio_model_root)
    models = discover_hf_cache_ggufs(
        hf_cache,
        include_mmproj=args.include_mmproj,
        include_mtp=args.include_mtp,
    )
    if args.repo:
        wanted = {repo.lower() for repo in args.repo}
        models = [model for model in models if model.repo_id.lower() in wanted]

    if not models:
        echo(f"No GGUF files found under Hugging Face cache: {hf_cache}")
        return

    counts: dict[str, int] = {}
    for model in models:
        target = model.target_path(lmstudio_model_root)
        if args.dry_run:
            result = "would-sync"
        else:
            result = link_or_copy(model.source, target, args.link_mode)
        counts[result] = counts.get(result, 0) + 1
        if args.verbose or args.dry_run or result.startswith("failed"):
            echo(f"{result}: {target} -> {model.source}")

    echo(f"LM Studio model root: {lmstudio_model_root}")
    echo(f"Scanned HF cache: {hf_cache}")
    echo(
        "Synced GGUF files: "
        + ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
    )


def setup(args: argparse.Namespace) -> None:
    lmstudio_model_root = Path(args.lmstudio_model_root)
    ensure_dir(lmstudio_model_root)
    if args.link_default_dir:
        maybe_link_default_models_dir(lmstudio_model_root)
    sync_models(args)
    lms = find_lms()
    if lms:
        echo(f"Found lms: {lms}")
        run_lms(["ls"], check=False)
    else:
        echo("LM Studio CLI `lms` is not installed/on PATH yet; model links are ready for when it is.")


def serve(args: argparse.Namespace) -> None:
    base_url = f"http://{args.host}:{args.port}/v1"
    try:
        with urllib.request.urlopen(base_url + "/models", timeout=3):
            echo(f"LM Studio server already responds at {base_url}")
    except (OSError, urllib.error.URLError, TimeoutError):
        server_args = ["server", "start", "--port", str(args.port), "--bind", args.host]
        if args.cors:
            server_args.append("--cors")
        run_lms(server_args)
        wait_openai_server(base_url, args.timeout)
        echo(f"LM Studio OpenAI-compatible API is listening at {base_url}")

    if args.model:
        load_args = ["load", args.model, "--context-length", str(args.context_length)]
        if args.gpu:
            load_args += ["--gpu", args.gpu]
        if args.ttl:
            load_args += ["--ttl", str(args.ttl)]
        if args.identifier:
            load_args += ["--identifier", args.identifier]
        run_lms(load_args)

    if args.open:
        open_url(base_url)


def stop(args: argparse.Namespace) -> None:
    if not find_lms():
        echo("LM Studio CLI `lms` was not found; nothing to stop via lms.")
        return
    if args.unload:
        run_lms(["unload", "--all"], check=False)
    run_lms(["server", "stop"], check=False)


def list_models(_args: argparse.Namespace) -> None:
    run_lms(["ls"], check=False)
    run_lms(["ps"], check=False)


def status(args: argparse.Namespace) -> None:
    lms = find_lms()
    echo(f"lms: {lms or '(not found)'}")
    echo(f"LM Studio model root: {args.lmstudio_model_root}")
    echo(f"LM Studio default model dir: {default_lmstudio_models_dir()}")
    if lms:
        run_lms(["server", "status"], check=False)
        run_lms(["ps"], check=False)
    base_url = f"http://{args.host}:{args.port}/v1"
    try:
        models = wait_openai_server(base_url, 3)
        echo(f"{base_url}/models OK: {models}")
    except Exception as exc:
        echo(f"{base_url}/models not reachable: {exc}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="win-models lmstudio")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_model_root_options(p: argparse.ArgumentParser) -> None:
        p.add_argument("--hf-cache", default=os.environ.get("UNSLOTH_HF_CACHE", str(DEFAULT_MODEL_ROOT)))
        p.add_argument("--lmstudio-model-root", default=str(DEFAULT_LMSTUDIO_MODEL_ROOT))

    p = sub.add_parser("setup")
    add_model_root_options(p)
    p.add_argument("--link-mode", choices=("auto", "symlink", "hardlink", "junction", "copy"), default="auto")
    p.add_argument("--repo", action="append", help="Only sync this HF repo id; may be repeated.")
    p.add_argument("--include-mmproj", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--include-mtp", action=argparse.BooleanOptionalAction, default=False)
    p.add_argument("--link-default-dir", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=setup)

    p = sub.add_parser("sync-models")
    add_model_root_options(p)
    p.add_argument("--link-mode", choices=("auto", "symlink", "hardlink", "junction", "copy"), default="auto")
    p.add_argument("--repo", action="append", help="Only sync this HF repo id; may be repeated.")
    p.add_argument("--include-mmproj", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--include-mtp", action=argparse.BooleanOptionalAction, default=False)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=sync_models)

    p = sub.add_parser("serve")
    p.add_argument("--host", default=DEFAULT_LMSTUDIO_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_LMSTUDIO_PORT)
    p.add_argument("--model", default=os.environ.get("LMSTUDIO_DEFAULT_MODEL", ""))
    p.add_argument("--identifier", default=os.environ.get("LMSTUDIO_MODEL_IDENTIFIER", ""))
    p.add_argument("--context-length", type=int, default=DEFAULT_LMSTUDIO_CONTEXT_LENGTH)
    p.add_argument("--gpu", default=DEFAULT_LMSTUDIO_GPU)
    p.add_argument("--ttl", default=DEFAULT_LMSTUDIO_TTL)
    p.add_argument("--cors", action=argparse.BooleanOptionalAction, default=False)
    p.add_argument("--timeout", type=int, default=45)
    p.add_argument("--open", action="store_true")
    p.set_defaults(func=serve)

    p = sub.add_parser("stop")
    p.add_argument("--unload", action=argparse.BooleanOptionalAction, default=True)
    p.set_defaults(func=stop)

    p = sub.add_parser("list")
    p.set_defaults(func=list_models)

    p = sub.add_parser("status")
    add_model_root_options(p)
    p.add_argument("--host", default=DEFAULT_LMSTUDIO_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_LMSTUDIO_PORT)
    p.set_defaults(func=status)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
