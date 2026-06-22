from __future__ import annotations

import argparse
import os
import subprocess
import time
from pathlib import Path

from .common import download_file, echo, ensure_dir, post_json, print_table, run, stop_process_names
from .config import DEFAULT_HOST, DEFAULT_LITERT_PORT, DEFAULT_LLAMA_PORT, DEFAULT_MODEL_ROOT
from .models import LITERT_VARIANTS, LLAMA_VARIANTS, MODEL_GROUPS, VARIANTS, Variant


def llama_run_dir(model_root: Path) -> Path:
    run_dir = model_root / "llama-b9536-cuda12" / "run"
    exe = run_dir / "llama-server.exe"
    if not exe.exists():
        raise FileNotFoundError(f"Missing llama.cpp server at {exe}. Download/extract llama.cpp b9536 CUDA first.")
    return run_dir


def download(args: argparse.Namespace) -> None:
    model_root = Path(args.model_root)
    ensure_dir(model_root)
    variant = VARIANTS[args.variant]
    target_dir = variant.directory_path(model_root)
    for asset in variant.assets:
        download_file(asset.url, target_dir / asset.filename)
    echo(f"Downloaded {variant.key} under {model_root}")


def _expand_targets(targets: list[str]) -> list[Variant]:
    expanded: list[Variant] = []
    seen: set[str] = set()
    for target in targets:
        keys = MODEL_GROUPS.get(target, (target,))
        for key in keys:
            if key not in VARIANTS:
                valid = ", ".join(sorted((*VARIANTS, *MODEL_GROUPS)))
                raise ValueError(f"Unknown model target '{key}'. Valid targets: {valid}")
            if key in seen:
                continue
            expanded.append(VARIANTS[key])
            seen.add(key)
    return expanded


def _asset_for_filename(variant: Variant, filename: str):
    for asset in variant.assets:
        if asset.filename == filename:
            return asset
    raise FileNotFoundError(f"{variant.key} does not define an asset for {filename}")


def _cached_asset_path(variant: Variant, filename: str, cache_dir: Path) -> Path | None:
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        return None

    asset = _asset_for_filename(variant, filename)
    repo_id, repo_filename, revision = asset.hf_reference()
    try:
        return Path(
            hf_hub_download(
                repo_id=repo_id,
                filename=repo_filename,
                revision=revision,
                cache_dir=cache_dir,
                local_files_only=True,
            )
        )
    except Exception:
        return None


def _resolve_variant_file(variant: Variant, model_root: Path, filename: str) -> Path:
    cached = _cached_asset_path(variant, filename, model_root)
    if cached is not None and cached.exists():
        return cached

    local = variant.directory_path(model_root) / filename
    if local.exists():
        return local

    raise FileNotFoundError(
        f"Missing {filename}. Run: just plain download-cache {variant.key}"
    )


def _resolve_hf_token(token_file: str | None, token_op_ref: str | None) -> str | None:
    for env_name in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_HUB_TOKEN"):
        value = os.environ.get(env_name)
        if value:
            return value

    if token_file:
        path = Path(token_file)
        if path.exists():
            token = path.read_text(encoding="utf-8").strip()
            if token:
                return token

    if not token_op_ref:
        return None

    completed = subprocess.run(
        ["op", "read", token_op_ref],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"Could not read Hugging Face token from {token_op_ref}")
    token = completed.stdout.strip()
    if not token:
        raise RuntimeError(f"1Password item returned an empty Hugging Face token: {token_op_ref}")
    return token


def download_cache(args: argparse.Namespace) -> None:
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "0")
    os.environ.pop("SSLKEYLOGFILE", None)

    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise RuntimeError("Install the project dependencies first; missing package: huggingface-hub") from exc

    cache_dir = Path(args.hf_cache)
    ensure_dir(cache_dir)
    token = _resolve_hf_token(args.token_file, args.token_op_ref)
    variants = _expand_targets(args.targets)

    echo(f"Downloading {len(variants)} variant(s) into Hugging Face cache: {cache_dir}")
    for variant in variants:
        echo(f"\n[{variant.key}]")
        for asset in variant.assets:
            repo_id, repo_filename, revision = asset.hf_reference()
            echo(f"  {repo_id}@{revision}:{repo_filename}")
            cached = hf_hub_download(
                repo_id=repo_id,
                filename=repo_filename,
                revision=revision,
                cache_dir=cache_dir,
                token=token,
            )
            echo(f"    cached: {cached}")
    echo("\nDone.")


def serve(args: argparse.Namespace) -> None:
    model_root = Path(args.model_root)
    variant = VARIANTS[args.variant]
    if variant.runtime != "llama":
        raise ValueError(f"{variant.key} is a {variant.runtime} variant, not a llama.cpp variant")

    model = _resolve_variant_file(variant, model_root, variant.model_file)
    mmproj = (
        None
        if variant.mmproj_file is None
        else _resolve_variant_file(variant, model_root, variant.mmproj_file)
    )

    stop_process_names(["llama-server", "litert-lm"])
    media = variant.directory_path(model_root) / "media"
    ensure_dir(media)
    run_dir = llama_run_dir(model_root)

    command = [
        run_dir / "llama-server.exe",
        "-m",
        model,
        "--mmproj",
        mmproj,
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--alias",
        variant.alias,
        "--ctx-size",
        str(args.context_size),
        "--parallel",
        "1",
        "--n-gpu-layers",
        str(args.gpu_layers),
        "--flash-attn",
        "on",
        "--cache-type-k",
        "q8_0",
        "--cache-type-v",
        "q8_0",
        "--media-path",
        media,
        "--cache-ram",
        str(args.cache_ram),
        "--jinja",
        "--reasoning",
        args.reasoning,
        "--reasoning-format",
        "deepseek",
        "--sleep-idle-seconds",
        "120",
    ]
    echo(f"Starting {variant.key} as {variant.alias} on http://{args.host}:{args.port}/v1")
    echo("Server output will stream in this terminal. Press Ctrl+C to stop.")
    run(command, cwd=run_dir)


def serve_litert(args: argparse.Namespace) -> None:
    model_root = Path(args.model_root)
    variant = VARIANTS[args.variant]
    if variant.runtime != "litert":
        raise ValueError(f"{variant.key} is a {variant.runtime} variant, not a LiteRT variant")

    model = _resolve_variant_file(variant, model_root, variant.model_file)

    stop_process_names(["llama-server", "litert-lm"])
    run(["litert-lm", "import", model, variant.alias])
    echo(f"Starting LiteRT-LM {variant.key} on http://{args.host}:{args.port}/v1")
    echo(f"Use model id: {variant.alias},gpu")
    run(["litert-lm", "serve", "--host", args.host, "--port", str(args.port), "--verbose"])


def bench(args: argparse.Namespace) -> None:
    prompt = "Think briefly, then answer in exactly one sentence: for local coding use, name one advantage and one drawback of quantized models."
    rows: list[dict] = []
    for i in range(1, args.runs + 1):
        body = {
            "model": args.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": args.max_tokens,
            "temperature": 0,
        }
        start = time.perf_counter()
        response = post_json(args.base_url.rstrip("/") + "/chat/completions", body)
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        usage = response.get("usage") or {}
        timings = response.get("timings") or {}
        choice = (response.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        reasoning = message.get("reasoning_content") or ""
        rows.append(
            {
                "run": i,
                "model": args.model,
                "elapsed_ms": elapsed_ms,
                "prompt_tokens": usage.get("prompt_tokens", ""),
                "completion_tokens": usage.get("completion_tokens", ""),
                "prompt_tps": round(timings["prompt_per_second"], 2) if "prompt_per_second" in timings else "",
                "generation_tps": round(timings["predicted_per_second"], 2) if "predicted_per_second" in timings else "",
                "finish": choice.get("finish_reason", ""),
                "content_chars": len(content),
                "reasoning_chars": len(reasoning),
            }
        )
    print_table(
        rows,
        [
            "run",
            "model",
            "elapsed_ms",
            "prompt_tokens",
            "completion_tokens",
            "prompt_tps",
            "generation_tps",
            "finish",
            "content_chars",
            "reasoning_chars",
        ],
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="win-models plain")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("download")
    p.add_argument("variant", choices=sorted(VARIANTS))
    p.add_argument("--model-root", default=str(DEFAULT_MODEL_ROOT))
    p.set_defaults(func=download)

    p = sub.add_parser("download-cache")
    p.add_argument("targets", nargs="+", help="Variant key(s) or model group(s), e.g. new-gemma-26b")
    p.add_argument("--hf-cache", default=str(DEFAULT_MODEL_ROOT))
    p.add_argument(
        "--token-op-ref",
        default=os.environ.get("WIN_MODELS_HF_TOKEN_OP_REF", "op://clankers/huggingface-read/password"),
        help="1Password reference for the HF token. Env HF_TOKEN takes precedence.",
    )
    p.add_argument(
        "--token-file",
        default=os.environ.get("WIN_MODELS_HF_TOKEN_FILE", ""),
        help="Path to a temporary HF token file. Env HF_TOKEN takes precedence.",
    )
    p.set_defaults(func=download_cache)

    p = sub.add_parser("serve")
    p.add_argument("variant", choices=LLAMA_VARIANTS)
    p.add_argument("--model-root", default=str(DEFAULT_MODEL_ROOT))
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_LLAMA_PORT)
    p.add_argument("--reasoning", choices=("on", "off", "auto"), default="on")
    p.add_argument("--context-size", type=int, default=8192)
    p.add_argument("--gpu-layers", default="all")
    p.add_argument("--cache-ram", type=int, default=8192)
    p.set_defaults(func=serve)

    p = sub.add_parser("serve-litert")
    p.add_argument("variant", choices=LITERT_VARIANTS)
    p.add_argument("--model-root", default=str(DEFAULT_MODEL_ROOT))
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_LITERT_PORT)
    p.set_defaults(func=serve_litert)

    p = sub.add_parser("bench")
    p.add_argument("model")
    p.add_argument("--base-url", default=f"http://127.0.0.1:{DEFAULT_LLAMA_PORT}/v1")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--max-tokens", type=int, default=256)
    p.set_defaults(func=bench)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
